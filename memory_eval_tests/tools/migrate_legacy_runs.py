"""Index legacy run directories (no ``run.json``) into standard envelopes.

Older online/scale runs were written before the envelope contract existed, so
the eval console could not list them.  This tool discovers such directories
under ``runs_root``, builds methods from the result JSONs found inside, and
writes a ``run.json`` envelope marked ``legacy: true`` so the console shows the
full history while making the old metric semantics explicit.

Run with ``--dry-run`` to preview; without it, envelopes are written in place.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import (
    capture_environment,
    write_simple_envelope,
)

_SKIP_DIRS = {"inputs", "rag_storage", "sidecar", "__pycache__", ".git"}
_RESULT_PATTERNS = (
    ("retrieval_", "retrieval", "检索评估"),
    ("answer_", "answer", "回答评估"),
    ("scale_eval", "scale", "规模评测"),
    ("context_size_", "context_size", "上下文窗口评测"),
)
_SKIP_NAME_PARTS = (".partial.",)
_SKIP_NAMES = {"api_preflight.json"}

_LEGACY_REPORTS = {
    "scale_report.md": ("scale", "规模评测报告", "scale_report"),
    "readiness_report.md": ("readiness", "文档记忆就绪度报告", "readiness_report"),
    "comparison_report.md": ("comparison", "评测对比报告", "comparison_report"),
    "evaluator_recheck_report.md": (
        "evaluator_recheck",
        "评估器复核报告",
        "evaluator_recheck_report",
    ),
}


def _method_kind(filename: str) -> tuple[str, str] | None:
    for prefix, method, label in _RESULT_PATTERNS:
        if filename.startswith(prefix) and filename.endswith(".json"):
            return method, label
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _method_from_json(path: Path) -> dict[str, Any] | None:
    name = path.name
    if name in _SKIP_NAMES or any(part in name for part in _SKIP_NAME_PARTS):
        return None
    if name.startswith("prompts_"):
        return None
    kind = _method_kind(name)
    if kind is None:
        return None
    try:
        payload = _load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("status") == "in_progress":
        return None
    method, label = kind
    return {
        "method": method,
        "label": label,
        "params": {},
        "summary": {
            key: value
            for key, value in payload.items()
            if isinstance(value, (int, float, bool))
        },
        "results": [],
    }


def _dataset_from_payloads(payloads: list[dict[str, Any]]) -> str | None:
    for payload in payloads:
        value = payload.get("dataset_id") or payload.get("dataset")
        if not value:
            continue
        value = str(value)
        if "/" in value:
            value = value.rsplit("/", 1)[-1]
        if value:
            return value
    return None


def _known_dataset_ids(generated_root: Path) -> list[str]:
    if not generated_root.is_dir():
        return []
    return sorted(
        (child.name for child in generated_root.iterdir() if child.is_dir()),
        key=len,
        reverse=True,
    )


def _dataset_from_dir_name(dir_name: str, generated_root: Path) -> str | None:
    base = dir_name.removeprefix("scale-")
    for dataset_id in _known_dataset_ids(generated_root):
        if base.startswith(dataset_id):
            return dataset_id
    return None


def _scalar_baseline(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    baseline: dict[str, Any] = {}
    for payload in payloads:
        for key in ("mode", "top_k", "chunk_top_k", "max_cases", "engine"):
            if key not in baseline and payload.get(key) is not None:
                baseline[key] = payload[key]
    return baseline


def _candidate_dirs(runs_root: Path) -> list[Path]:
    """Legacy online run directories (``runs_root/online/*``) without envelopes."""
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(runs_root), followlinks=False):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".parsed")
        ]
        if "run.json" in filenames:
            continue
        relative = Path(dirpath).relative_to(runs_root)
        if len(relative.parts) != 2 or relative.parts[0] != "online":
            continue
        candidates.append(Path(dirpath))
    return candidates


def migrate_legacy_runs(
    *,
    runs_root: Path,
    generated_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write envelopes for legacy run directories; returns a change summary."""
    created: list[str] = []
    skipped: list[str] = []
    for run_dir in _candidate_dirs(runs_root):
        result_paths = sorted(
            path
            for path in run_dir.glob("*.json")
            if _method_from_json(path) is not None
        )
        payloads = [_load_json(path) for path in result_paths if path.exists()]
        methods = [
            method for path in result_paths if (method := _method_from_json(path))
        ]
        dataset = _dataset_from_payloads(payloads) or _dataset_from_dir_name(
            run_dir.name, generated_root
        )
        baseline: dict[str, Any] = {"dataset": dataset} if dataset else {}
        baseline.update(_scalar_baseline(payloads))
        report_md = next(
            (
                path.name
                for path in sorted(run_dir.glob("*.md"))
                if path.name not in {"report.md"}
            ),
            None,
        )
        status = "complete" if methods else "incomplete"
        experiment = {
            "id": "legacy_online",
            "label": run_dir.name,
            "description": (
                "历史产物迁移（旧口径，未按新版指标重跑）；"
                "与新版实验对比前需重新运行评估。"
            ),
        }
        if dry_run:
            created.append(f"{run_dir} -> {status} ({len(methods)} methods)")
            continue
        write_simple_envelope(
            run_dir,
            kind="online",
            run_id=run_dir.name,
            experiment=experiment,
            baseline=baseline,
            environment=capture_environment(),
            methods=methods,
            status=status,
            report_rel_path=report_md,
            extra={"legacy": True, "metric_semantics": "legacy"},
            runs_root=runs_root,
        )
        created.append(str(run_dir))
    return {
        "dry_run": dry_run,
        "created": created,
        "skipped": skipped,
        "count": len(created),
    }


def migrate_legacy_reports(*, runs_root: Path, dry_run: bool = False) -> dict[str, Any]:
    """Wrap bare aggregate report markdown files into ``kind=report`` envelopes."""
    from memory_eval_tests.reporting.report_envelope import write_report_envelope

    created: list[str] = []
    for filename, (report_type, label, experiment_id) in _LEGACY_REPORTS.items():
        source = runs_root / filename
        if not source.exists():
            continue
        run_dir = runs_root / f"{experiment_id}-v1"
        if (run_dir / "run.json").exists():
            continue
        if dry_run:
            created.append(f"{source} -> {run_dir}")
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / filename
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        write_report_envelope(
            output_path=target,
            report_type=report_type,
            label=label,
            description=f"历史报告迁移（{filename}，旧口径产物）。",
            baseline={},
            runs_root=runs_root,
        )
        created.append(str(run_dir))
    return {"dry_run": dry_run, "created": created, "count": len(created)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-root", type=Path, default=Path("memory_eval_tests/runs")
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("memory_data_service/generated"),
        help="Dataset manifest root used to infer dataset ids from dir names.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing."
    )
    parser.add_argument(
        "--reports",
        action="store_true",
        help="Migrate legacy aggregate report markdown files instead of online runs.",
    )
    args = parser.parse_args(argv)
    if args.reports:
        summary = migrate_legacy_reports(runs_root=args.runs_root, dry_run=args.dry_run)
    else:
        summary = migrate_legacy_runs(
            runs_root=args.runs_root,
            generated_root=args.generated_root,
            dry_run=args.dry_run,
        )
    for entry in summary["created"]:
        print(entry)
    print(
        f"{'Dry-run: ' if summary['dry_run'] else ''}"
        f"{summary['count']} legacy run(s) indexed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
