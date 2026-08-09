"""Online retrieval+answer baseline, merged into one standard envelope."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import (
    ExperimentSpec,
    RunContext,
    normalize_summary,
)


def _summary_metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if isinstance(value, (int, float, bool)) and key != "results"
    }


def _run_forwarding(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Run a subprocess and forward its output line-by-line to our stdout.

    Forwarding through ``sys.stdout.write`` (not ``print``) avoids doubling
    newlines when child lines already end with one, and lets run.py's tee
    capture the child's output into run.log live.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    code = proc.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd)


def _render_report(retrieval: dict[str, Any], answer: dict[str, Any]) -> str:
    lines = [
        "# 在线基线评测",
        "",
        "通过 LightRAG API 的 mix 检索与回答，作为受控实验的端到端基线。",
        "",
        "## 检索",
        "",
        "| Recall@K | MRR | Cases |",
        "|---|---:|---:|",
        f"| {retrieval.get('average_recall', 0):.4f} | {retrieval.get('mrr', 0):.4f} | {retrieval.get('cases', 0)} |",
        "",
        "## 回答",
        "",
        "| Accuracy | Groundedness | Hallucination | Abstention | Citation |",
        "|---|---:|---:|---:|---:|",
        f"| {answer.get('answer_accuracy', 0):.4f} | {answer.get('groundedness', 0):.4f} | "
        f"{answer.get('ungrounded_rate', 0):.4f} | {answer.get('abstention_accuracy') or 0:.4f} | "
        f"{answer.get('evidence_available') or 0:.4f} |",
        "",
    ]
    return "\n".join(lines)


def _runner(context: RunContext) -> dict[str, Any]:
    output_dir = context.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    baseline = context.baseline
    retrieval_json = output_dir / "retrieval.json"
    answer_json = output_dir / "answer.json"
    top_k = int(baseline.get("top_k") or 5)
    chunk_top_k = int(baseline.get("chunk_top_k") or 5)
    max_total_tokens = baseline.get("max_total_tokens") or 8192
    mode = baseline.get("mode", "mix")
    max_cases = int(baseline.get("max_cases") or 0)
    api_key = context.environment.get("api_key")
    access_token = context.environment.get("access_token")

    def _auth_args() -> list[str]:
        args: list[str] = []
        if api_key:
            args.extend(["--api-key", api_key])
        if access_token:
            args.extend(["--access-token", access_token])
        if max_cases > 0:
            args.extend(["--max-cases", str(max_cases)])
        return args

    _run_forwarding(
        [
            sys.executable,
            "-m",
            "memory_eval_tests.online.retrieval_eval",
            "--dataset",
            str(context.dataset),
            "--rag-api-url",
            context.environment["rag_api_url"],
            "--mode",
            mode,
            "--top-k",
            str(top_k),
            *_auth_args(),
            "--output",
            str(retrieval_json),
        ],
        cwd=repo_root,
        env=env,
    )
    _run_forwarding(
        [
            sys.executable,
            "-m",
            "memory_eval_tests.online.answer_eval",
            "--dataset",
            str(context.dataset),
            "--rag-api-url",
            context.environment["rag_api_url"],
            "--mode",
            mode,
            "--top-k",
            str(top_k),
            "--chunk-top-k",
            str(chunk_top_k),
            "--max-total-tokens",
            str(max_total_tokens),
            *_auth_args(),
            "--output",
            str(answer_json),
        ],
        cwd=repo_root,
        env=env,
    )
    retrieval = json.loads(retrieval_json.read_text(encoding="utf-8"))
    answer = json.loads(answer_json.read_text(encoding="utf-8"))
    methods = [
        {
            "method": "retrieval",
            "label": "检索结果",
            "params": {"mode": mode, "top_k": top_k},
            "summary": normalize_summary(_summary_metrics(retrieval), "retrieval"),
            "results": retrieval.get("results", []),
        },
        {
            "method": "answer",
            "label": "回答评测",
            "params": {"mode": mode, "top_k": top_k, "chunk_top_k": chunk_top_k},
            "summary": normalize_summary(_summary_metrics(answer), "answer"),
            "results": answer.get("results", []),
        },
    ]
    return {
        "methods": methods,
        "report": _render_report(retrieval, answer),
        "status": "complete",
    }


spec = ExperimentSpec(
    id="online_baseline",
    label="在线基线评测",
    description=(
        "通过 LightRAG API 的 mix 检索与回答评测作为端到端基线；"
        "口径与历史在线 run 一致（mode=mix、Top-5、max_total_tokens=8192）。"
    ),
    default_baseline={
        "model": "qwen3:8b",
        "mode": "mix",
        "top_k": 5,
        "chunk_top_k": 5,
        "max_total_tokens": 8192,
        "kg": True,
    },
    variables=[],
    runner=_runner,
)
