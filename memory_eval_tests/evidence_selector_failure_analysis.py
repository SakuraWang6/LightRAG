"""Render evidence-selector failure decomposition from saved experiment rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _by_method(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["method"]: {row["question_id"]: row for row in item["results"]} for item in payload["methods"]}


def _classify(row: dict[str, Any], direct_top20: dict[str, Any]) -> tuple[str, str, str | None]:
    if row["exact_match"]:
        return "PASS", "Correct under the repaired deterministic evaluator.", None
    if row["candidate_recall"] < 1:
        return "R1 Retrieval Failure", "An oracle fact has no matching unit in the Top-20 candidate pool.", None
    if row["selected_recall"] < row["candidate_recall"]:
        return "R2 Selection Failure", "The candidate pool contains the oracle fact, but the selector omitted it.", None
    if not row["exact_match"] and direct_top20.get("exact_match"):
        return (
            "R3 Context Interference",
            "The selected context retains the oracle evidence, but the uncompressed Top-20 answer is correct while the selected answer is not.",
            "R4 Generation / Reasoning Failure",
        )
    if not row["exact_match"]:
        return (
            "R4 Generation / Reasoning Failure",
            "The selected context retains the oracle evidence, yet the deterministic evaluator marks the answer incorrect.",
            None,
        )
    raise AssertionError("All incorrect-answer branches should be classified above")


def render(payload: dict[str, Any]) -> str:
    methods = _by_method(payload)
    selected = methods["select5"]
    direct = methods["direct_top20"]
    rows = []
    counts: dict[str, int] = {}
    for question_id, row in selected.items():
        primary, evidence, secondary = _classify(row, direct[question_id])
        if primary != "PASS":
            counts[primary] = counts.get(primary, 0) + 1
        rows.append((question_id, row, primary, secondary, evidence))
    lines = [
        "# Evidence Selection Failure Analysis",
        "",
        "Scope: `Top20 → Select5` is decomposed against the same Top-20 candidate pool and the Direct Top-20 answer. The evaluator is the repaired deterministic evaluator; this report does not automatically label any remaining incorrect answer as an evaluator failure.",
        "",
        "## Failure counts",
        "",
        "| Primary cause | Cases |",
        "|---|---:|",
    ]
    for cause in ("R1 Retrieval Failure", "R2 Selection Failure", "R3 Context Interference", "R4 Generation / Reasoning Failure", "R5 Evaluator Failure"):
        lines.append(f"| {cause} | {counts.get(cause, 0)} |")
    lines.extend(["", "## Per-question evidence", "", "| Question | Type | Top-20 Recall | Selected Recall | Direct Top-20 | Select5 | Primary | Secondary | Evidence |", "|---|---|---:|---:|---:|---:|---|---|---|"])
    for question_id, row, primary, secondary, evidence in rows:
        lines.append(
            f"| {question_id} | {row['question_group']} | {row['candidate_recall']:.2f} | {row['selected_recall']:.2f} | "
            f"{int(bool(direct[question_id]['exact_match']))} | {int(bool(row['exact_match']))} | {primary} | {secondary or ''} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- R1 is evidenced only by oracle-fact absence from the saved Top-20 candidate units.",
            "- R2 is evidenced only when a fact matched the candidate units but not any selected unit.",
            "- R3 requires retained evidence plus a Direct Top-20 success, so it is not a guess based on answer wording.",
            "- R4 is the residual only after R1/R2 have been ruled out. It may include reasoning, extraction, or prompt-following errors; it does not prove a model-only cause.",
            "- R5 is zero in this table because the evaluator was repaired and no additional manual adjudication has been supplied; any future confirmed false negative should be moved there with the raw answer as evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(render(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
