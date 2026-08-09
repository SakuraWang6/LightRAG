"""Write ``kind=report`` envelopes for aggregate report outputs.

The WebUI renders ``kind=report`` runs through ``ReportDocument``; the scale /
readiness / comparison CLIs used to write bare markdown only, so the console
could never show them.  These helpers wrap an output file into a standard
envelope so every report appears in the evaluation console.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.common import (
    capture_environment,
    write_simple_envelope,
)


def write_report_envelope(
    *,
    output_path: Path,
    report_type: str,
    label: str,
    description: str,
    baseline: dict[str, Any],
    methods: list[dict[str, Any]] | None = None,
    status: str = "complete",
    runs_root: Path | None = None,
) -> Path:
    """Write ``run.json`` next to an aggregate report markdown file."""
    return write_simple_envelope(
        output_path.parent,
        kind="report",
        run_id=output_path.stem,
        experiment={
            "id": f"{report_type}_report",
            "label": label,
            "description": description,
        },
        baseline=baseline,
        environment=capture_environment(),
        methods=methods or [],
        status=status,
        report_rel_path=output_path.name,
        extra={"report_type": report_type, "metric_semantics": "report"},
        runs_root=runs_root,
    )
