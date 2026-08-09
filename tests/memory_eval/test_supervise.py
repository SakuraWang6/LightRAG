"""Tests for the experiment supervisor (restart, heartbeat, process tree)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from memory_eval_tests.experiments import supervise

pytestmark = pytest.mark.offline


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        experiment="context_size",
        dataset=Path("memory_data_service/generated/rich-smoke-v1"),
        output_dir=Path("memory_eval_tests/runs/x"),
        run_id=None,
        model=None,
        mode=None,
        top_k=None,
        chunk_top_k=None,
        num_ctx=None,
        num_predict=None,
        temperature=None,
        ollama_url="http://127.0.0.1:11434",
        rag_api_url="http://127.0.0.1:9621",
        api_key=None,
        access_token=None,
        runs_root=None,
        storage_dir=None,
        engine=None,
        max_cases=0,
        skip_kg=False,
        extra=[],
        max_restarts=3,
        supervision="auto",
        stale_minutes=60,
        poll_seconds=30,
        keep_proxy=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_build_command_forwards_new_args() -> None:
    args = _args(
        api_key="k",
        access_token="t",
        runs_root=Path("/tmp/runs"),
        run_id="r1",
    )
    cmd = supervise._build_command(
        args,
        heartbeat=True,
        restart_count=2,
        original_started_at="2026-08-09T00:00:00+00:00",
    )
    assert cmd[cmd.index("--api-key") + 1] == "k"
    assert cmd[cmd.index("--access-token") + 1] == "t"
    assert cmd[cmd.index("--runs-root") + 1] == "/tmp/runs"
    assert cmd[cmd.index("--run-id") + 1] == "r1"
    assert "--heartbeat" in cmd
    assert cmd[cmd.index("--restart-count") + 1] == "2"
    assert "--original-started-at" in cmd

    plain = supervise._build_command(
        _args(),
        heartbeat=False,
        restart_count=0,
        original_started_at=None,
    )
    assert "--heartbeat" not in plain
    assert "--restart-count" not in plain
    assert "--original-started-at" not in plain


def test_build_command_forwards_restart_mode() -> None:
    cmd = supervise._build_command(
        _args(),
        heartbeat=False,
        restart_count=1,
        original_started_at=None,
        restart_mode="resume",
    )
    assert cmd[cmd.index("--restart-mode") + 1] == "resume"


def test_activity_mtime_takes_max_of_heartbeat_and_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert supervise._activity_mtime(run_dir) is None
    heartbeat = run_dir / ".heartbeat"
    log = run_dir / "run.log"
    heartbeat.write_text("a", encoding="utf-8")
    log.write_text("b" * 100, encoding="utf-8")
    # Force the heartbeat to be older than the log.
    old = heartbeat.stat().st_mtime - 10
    os.utime(heartbeat, (old, old))
    assert supervise._activity_mtime(run_dir) == pytest.approx(log.stat().st_mtime)


def test_child_env_keeps_proxy_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy.test:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.test:3128")
    stripped = supervise._child_env(_args())
    assert "http_proxy" not in stripped
    kept = supervise._child_env(_args(keep_proxy=True))
    assert kept["http_proxy"] == "http://proxy.test:3128"
    assert kept["NO_PROXY"] == "127.0.0.1,localhost"


def test_stale_and_supervision_helpers() -> None:
    assert supervise._stale(None, 1000.0, 60) is False
    assert supervise._stale(941.0, 1000.0, 60) is False
    assert supervise._stale(940.0, 1000.0, 60) is True
    assert supervise._should_monitor_stale("heartbeat") is True
    assert supervise._should_monitor_stale("none") is False
    assert supervise._should_monitor_stale("auto") is False


def test_existing_run_state_inherits(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert supervise._existing_run_state(run_dir) is None
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"started_at": "2026-08-09T00:00:00+00:00", "restarts": 2}),
        encoding="utf-8",
    )
    assert supervise._existing_run_state(run_dir) == (
        "2026-08-09T00:00:00+00:00",
        2,
    )
    (run_dir / "run.json").write_text(json.dumps({"restarts": 5}), encoding="utf-8")
    assert supervise._existing_run_state(run_dir) is None


def test_flock_conflict(tmp_path: Path) -> None:
    handle = supervise._acquire_lock(tmp_path)
    with pytest.raises(SystemExit, match="already monitoring"):
        supervise._acquire_lock(tmp_path)
    handle.close()


def test_terminate_tree_kills_grandchildren(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "grandchild.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen(['sleep', '1000'])\n"
        "open(sys.argv[1], 'w').write(str(p.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(script), str(pid_file)],
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pid_file.exists(), "grandchild never started"
    grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
    supervise._terminate_tree(proc)
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)


def test_spawns_child_in_new_session(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(supervise.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(supervise.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(supervise.time, "sleep", lambda *a: None)
    rc = supervise.main(
        [
            "--experiment",
            "context_size",
            "--dataset",
            str(tmp_path / "d"),
            "--output-dir",
            str(tmp_path / "run"),
        ]
    )
    assert rc == 0
    assert captured.get("start_new_session") is True


def test_restart_inherits_state_and_counts(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    attempt = {"n": 0}
    output_dir = tmp_path / "run"

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            attempt["n"] += 1
            calls.append(cmd)

        def poll(self):
            return 1 if attempt["n"] == 1 else 0

        def wait(self):
            if attempt["n"] == 1:
                # The first child wrote its initial envelope before crashing.
                (output_dir / "run.json").write_text(
                    json.dumps(
                        {
                            "started_at": "2026-08-09T00:00:00+00:00",
                            "restarts": 0,
                        }
                    ),
                    encoding="utf-8",
                )
            return 1 if attempt["n"] == 1 else 0

    monkeypatch.setattr(supervise.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(supervise.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(supervise.time, "sleep", lambda *a: None)

    rc = supervise.main(
        [
            "--experiment",
            "context_size",
            "--dataset",
            str(tmp_path / "d"),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0
    assert len(calls) == 2
    second = calls[1]
    assert second[second.index("--restart-count") + 1] == "1"
    assert (
        second[second.index("--original-started-at") + 1] == "2026-08-09T00:00:00+00:00"
    )
    assert second[second.index("--restart-mode") + 1] == "resume"
    log = (output_dir / "run.log").read_text(encoding="utf-8")
    assert "resume from partial.json" in log
    progress = json.loads((output_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["message"] == "第 1 次重启（续跑）"
