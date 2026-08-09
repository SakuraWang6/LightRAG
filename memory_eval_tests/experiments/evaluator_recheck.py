"""Re-score saved memory-evaluation answers without calling an LLM or RAG API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.online.answer_eval import score_answer

DEFAULT_REPORTS = (
    Path("memory_eval_tests/runs/online/rich-smoke-v1-api/answer_mix.json"),
    Path("memory_eval_tests/runs/online/rich-smoke-v1-local-qwen8b-skipkg/answer_mix_top5_ctx8192.json"),
    Path("memory_eval_tests/runs/online/rich-smoke-v1-local-qwen8b-kg-timeout900/answer_mix_top5_ctx8192.json"),
    Path("memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/answer_kg_mix_top5_ctx8192_gpt4o-mini.json"),
    Path("memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/context_size_qwen8b_ctx16384.json"),
)


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _expand_report(path: Path) -> list[tuple[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    nested = payload.get("reports")
    if isinstance(nested, list):
        return [(f"{path} (Top-{item.get('top_k')})", item) for item in nested]
    return [(str(path), payload)]


def _change_reason(question: dict[str, Any], old: bool, new: bool) -> str:
    if old == new:
        return "unchanged"
    if not old and new:
        if question.get("expected_behavior") == "abstain":
            return "evaluator false negative: expanded deterministic abstention phrase coverage"
        if question.get("question_type") in {"formula", "equation", "formula_variable"}:
            return "evaluator false negative: formula normalization (Greek/subscript/fraction form)"
        return "evaluator false negative: normalized answer matching"
    return "score became stricter; inspect answer manually"


def recheck_one(
    *, label: str, report: dict[str, Any], questions: dict[str, dict[str, Any]], facts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    rows = []
    for old_row in report.get("results", []):
        question_id = str(old_row.get("question_id", ""))
        question = questions.get(question_id)
        if not question:
            continue
        evidence = [facts[fact_id] for fact_id in question.get("evidence_fact_ids", []) if fact_id in facts]
        new = score_answer(
            answer_text=str(old_row.get("answer", "")),
            expected=str(old_row.get("expected", question.get("answer", ""))),
            question=question,
            evidence_facts=evidence,
            references_blob="",
            # Saved reports store this legacy field as reference/context evidence
            # availability.  The raw per-query reference payload was not kept.
            evidence_available_override=old_row.get(
                "evidence_available", old_row.get("citation_correct")
            ),
        )
        old_exact = bool(old_row.get("exact_match"))
        rows.append(
            {
                "question_id": question_id,
                "question_type": question.get("question_type", ""),
                "old_exact_match": old_exact,
                "new_exact_match": new["exact_match"],
                "old_grounded": bool(old_row.get("grounded")),
                "new_grounded": new["grounded"],
                "new_evidence_available": new["evidence_available"],
                "new_citation_presence": new["citation_presence"],
                "new_citation_correctness": new["citation_correctness"],
                "new_abstention_correct": new["abstention_correct"],
                "reason": _change_reason(question, old_exact, bool(new["exact_match"])),
                "answer": old_row.get("answer", ""),
            }
        )
    total = len(rows)
    return {
        "label": label,
        "cases": total,
        "old_answer_accuracy": sum(row["old_exact_match"] for row in rows) / total if total else 0.0,
        "new_answer_accuracy": sum(row["new_exact_match"] for row in rows) / total if total else 0.0,
        "old_groundedness": sum(row["old_grounded"] for row in rows) / total if total else 0.0,
        "new_groundedness": sum(row["new_grounded"] for row in rows) / total if total else 0.0,
        "evidence_available": _average(rows, "new_evidence_available"),
        "citation_presence": _average(rows, "new_citation_presence"),
        "citation_correctness": _average(rows, "new_citation_correctness"),
        "changed_questions": [row for row in rows if row["old_exact_match"] != row["new_exact_match"]],
        "genuine_model_failures": [row for row in rows if not row["new_exact_match"]],
        "results": rows,
    }


def render_markdown(rechecks: list[dict[str, Any]]) -> str:
    lines = [
        "# Evaluator Recheck Report",
        "",
        "This report re-scores saved answer text only. It makes no LLM, embedding, or retrieval calls. "
        "`evidence_available` reuses the saved legacy reference-availability flag because historical raw references were not retained.",
        "",
        "## Aggregate changes",
        "",
        "| Report | Cases | Old Accuracy | New Accuracy | Old Groundedness | New Groundedness | Evidence Available | Citation Presence | Citation Correctness |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in rechecks:
        value = lambda key: "n/a" if item[key] is None else f"{item[key]:.4f}"
        lines.append(
            f"| `{item['label']}` | {item['cases']} | {value('old_answer_accuracy')} | {value('new_answer_accuracy')} | "
            f"{value('old_groundedness')} | {value('new_groundedness')} | {value('evidence_available')} | "
            f"{value('citation_presence')} | {value('citation_correctness')} |"
        )
    lines.extend(["", "## Changed questions", ""])
    changed = [(item["label"], row) for item in rechecks for row in item["changed_questions"]]
    if not changed:
        lines.append("No exact-match scores changed under the repaired evaluator.")
    else:
        lines.extend(
            [
                "| Report | Question | Old | New | Classification |",
                "|---|---|---:|---:|---|",
                *[
                    f"| `{label}` | {row['question_id']} | {int(row['old_exact_match'])} | {int(row['new_exact_match'])} | {row['reason']} |"
                    for label, row in changed
                ],
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Changed false-to-true cases are evaluator false negatives, not newly improved model answers.",
            "- Remaining false cases are candidates for genuine model/retrieval/structure failures; they require the evidence-level decomposition in the next stage, not automatic blame on the model.",
            "- `citation_presence` measures explicit stable `FACT-*`/`OBJ-*` IDs in answers. `citation_correctness` is only defined when such a citation appears; it is not inferred from answer-value overlap.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("memory_data_service/generated/rich-smoke-v1"))
    parser.add_argument("--report", type=Path, action="append")
    parser.add_argument(
        "--output-json", type=Path, default=Path("memory_eval_tests/runs/evaluator_recheck.json")
    )
    parser.add_argument(
        "--output-md", type=Path, default=Path("memory_eval_tests/runs/evaluator_recheck_report.md")
    )
    args = parser.parse_args()
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = {str(question["id"]): question for question in oracle["questions"]}
    facts = {str(fact["fact_id"]): fact for fact in oracle["facts"]}
    rechecks = []
    for path in args.report or list(DEFAULT_REPORTS):
        for label, report in _expand_report(path):
            rechecks.append(recheck_one(label=label, report=report, questions=questions, facts=facts))
    payload = {"dataset": str(args.dataset), "reports": rechecks}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(rechecks), encoding="utf-8")


if __name__ == "__main__":
    main()
