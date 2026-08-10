"""Tests for the custom_arms (parameter-arm) experiment runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_eval_tests.experiments.common import ExperimentSpec, RunContext
from memory_eval_tests.experiments.custom_arms import MAX_ARMS_CAP, _run_custom_arms

pytestmark = pytest.mark.offline


class _FakeBase:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_when: str | None = None
        self.fail_all: bool = False

    def run(self, context: RunContext) -> dict:
        self.calls.append(
            {
                "model": context.baseline.get("model"),
                "top_k": context.baseline.get("top_k"),
                "dir": context.output_dir.name,
            }
        )
        if self.fail_all or (
            self.fail_when and context.baseline.get("top_k") == self.fail_when
        ):
            raise RuntimeError("boom")
        return {
            "methods": [
                {
                    "method": "m",
                    "label": "M",
                    "summary": {"answer_accuracy": 0.8},
                    "results": [],
                }
            ],
            "report": "arm report",
            "status": "complete",
        }


def _context(tmp_path: Path, *, axes: dict, max_arms: int = 8, **extra) -> RunContext:
    fake = _FakeBase()
    spec = ExperimentSpec(
        id="custom_arms",
        label="自定义参数臂实验",
        description="d",
        runner=_run_custom_arms,
    )
    payload_extra = {
        "base_experiment": "fake_base",
        "axes": json.dumps(axes),
        "max_arms": str(max_arms),
        **extra,
    }
    return RunContext(
        spec=spec,
        dataset=tmp_path / "dataset",
        output_dir=tmp_path / "run",
        baseline={"model": "gpt-4o-mini", "top_k": 99},
        environment={},
        variables=[],
        run_id="r",
        extra=payload_extra,
    ), fake


def _monkeypatch_base(monkeypatch, fake: _FakeBase) -> None:
    import memory_eval_tests.experiments.registry as registry

    base_spec = ExperimentSpec(
        id="fake_base",
        label="Fake",
        description="d",
        runner=fake.run,
        default_baseline={"model": "qwen3:8b", "top_k": 20},
    )
    monkeypatch.setattr(
        registry,
        "get_spec",
        lambda experiment_id: (
            base_spec
            if experiment_id == "fake_base"
            else registry.get_spec(experiment_id)
        ),
    )


def test_arms_inherit_parent_baseline_and_apply_arm_overrides(
    monkeypatch, tmp_path: Path
) -> None:
    context, fake = _context(tmp_path, axes={"top_k": ["5", "10"]})
    _monkeypatch_base(monkeypatch, fake)
    payload = _run_custom_arms(context)
    assert payload["status"] == "complete"
    assert len(fake.calls) == 2
    assert fake.calls[0]["model"] == "gpt-4o-mini"  # parent baseline inherited
    assert fake.calls[0]["top_k"] == "5"
    assert fake.calls[1]["top_k"] == "10"
    assert fake.calls[0]["dir"] == "arm-1"
    assert [m["label"] for m in payload["methods"]] == [
        "top_k=5 · M",
        "top_k=10 · M",
    ]
    assert (context.output_dir / "arm-1" / "report.md").exists()


def test_arm_product_cap_enforced(monkeypatch, tmp_path: Path) -> None:
    context, fake = _context(
        tmp_path,
        axes={"a": ["1", "2", "3"], "b": ["x", "y", "z"]},
        max_arms=8,
    )
    _monkeypatch_base(monkeypatch, fake)
    with pytest.raises(ValueError, match="exceeds max_arms"):
        _run_custom_arms(context)


def test_invalid_axes_rejected(monkeypatch, tmp_path: Path) -> None:
    context, fake = _context(tmp_path, axes="not-json")
    _monkeypatch_base(monkeypatch, fake)
    with pytest.raises(ValueError, match="axes"):
        _run_custom_arms(context)


def test_all_arms_failed_marks_parent_failed(monkeypatch, tmp_path: Path) -> None:
    context, fake = _context(tmp_path, axes={"top_k": ["5", "10"]})
    fake.fail_all = True
    _monkeypatch_base(monkeypatch, fake)
    payload = _run_custom_arms(context)
    assert payload["status"] == "failed"
    assert all(m["summary"]["status"] == "failed" for m in payload["methods"])


def test_partial_failure_stays_complete_with_report_marker(
    monkeypatch, tmp_path: Path
) -> None:
    context, fake = _context(tmp_path, axes={"top_k": ["5", "10"]})
    fake.fail_when = "10"
    _monkeypatch_base(monkeypatch, fake)
    payload = _run_custom_arms(context)
    assert payload["status"] == "complete"
    assert payload["methods"][0]["summary"]["status"] == "complete"
    assert payload["methods"][1]["summary"]["status"] == "failed"
    assert "1/2 臂失败" in payload["report"]


def test_custom_arms_registered(monkeypatch, tmp_path: Path) -> None:
    from memory_eval_tests.experiments.registry import list_specs

    ids = {spec.id for spec in list_specs()}
    assert "custom_arms" in ids
    spec = next(spec for spec in list_specs() if spec.id == "custom_arms")
    assert spec.extra_schema == {
        "base_experiment": "str",
        "axes": "str",
        "max_arms": "int",
        "comparison_type": "str",
        "frozen_context_run_id": "str",
        "source_run_id": "str",
        "environment_profile_id": "str",
        "environment_profile_version": "int",
    }
    assert MAX_ARMS_CAP == 16
