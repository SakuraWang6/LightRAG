"""Relation/Role-aware evidence selection on the frozen Top-20 candidate pool.

P0-2 of the four-stage plan.  The candidate pool and the Select5 baseline are
reused verbatim from ``evidence-selector-v1`` so the only change between arms is
the selection strategy:

* ``select5`` (saved baseline): the plain Top20 -> Select5 pack.
* ``select5_role_prompt``: a role-aware selector prompt that asks the LLM to
  cover every distinct role/hop required by the question.
* ``select5_role_guaranteed``: the saved Select5 selection plus a deterministic
  oracle-role repair.  When an oracle evidence fact is present in the candidate
  pool but was not selected, the best matching candidate is force-added.  This
  is the coverage ceiling of role-aware selection for the current retrieval.

The arm therefore separates R1 (candidate pool does not contain the evidence)
from R2 (the selector dropped evidence that was available): R1 is visible as
``candidate_recall < 1`` and cannot be repaired; R2 is visible as
``role_coverage < candidate_recall`` and is repaired by the guaranteed arm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.evidence_selector_experiment import (
    _chat_ollama,
    _contains_fact,
    _group,
    _make_candidates,
    _parse_selection,
    _render_context,
    _split_prompt,
)
from memory_eval_tests.online.answer_eval import score_answer


def _entity_rows_from_context(context: str, limit: int = 20) -> list[dict[str, Any]]:
    match = re.search(
        r"Knowledge Graph Data \(Entity\):\s*```json\s*(.*?)\s*```",
        context,
        flags=re.S,
    )
    if not match:
        return []
    rows = []
    for line in match.group(1).splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("entity"):
            rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _role_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
    rendered = [
        {
            "evidence_id": item["evidence_id"],
            "object_type": item["object_type"],
            "entity": item["entity"],
            "text": item["text"],
        }
        for item in candidates
    ]
    return (
        "You are an evidence selector. Do not answer the question. The question may require "
        "multiple distinct pieces of evidence (multi-hop). Identify each distinct role the "
        "question needs (for example a latency fact, an equation, a figure, or a table), and "
        "ensure at least one evidence ID covers every role before spending the remaining budget. "
        "Prefer authoritative facts over distractors. Select at most "
        f"{limit} evidence IDs. Return ONLY strict JSON with this shape: "
        '{"selected_evidence_ids":["EVD-..."],"roles":["..."]}.\n\n'
        f"Question: {question}\n\nCandidates:\n{json.dumps(rendered, ensure_ascii=False)}"
    )


def _facts_covered(candidates: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    covered = []
    for fact in facts:
        if any(_contains_fact(candidate, fact) for candidate in candidates):
            covered.append(str(fact.get("fact_id") or ""))
    return covered


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    rate = lambda key: sum(bool(row["metrics"].get(key)) for row in rows) / total if total else 0.0
    by_type: dict[str, dict[str, Any]] = {}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        subset = [row for row in rows if row["question_group"] == name]
        if subset:
            by_type[name] = {
                "cases": len(subset),
                "answer_accuracy": sum(bool(r["metrics"]["exact_match"]) for r in subset) / len(subset),
                "role_coverage": sum(r["role_coverage"] for r in subset) / len(subset),
            }
    return {
        "cases": total,
        "answer_accuracy": rate("exact_match"),
        "groundedness": rate("grounded"),
        "hallucination_rate": rate("hallucinated"),
        "candidate_recall": sum(row["candidate_recall"] for row in rows) / total if total else 0.0,
        "role_coverage": sum(row["role_coverage"] for row in rows) / total if total else 0.0,
        "full_role_coverage_rate": sum(row["role_coverage"] >= 1.0 for row in rows) / total if total else 0.0,
        "selection_precision": sum(bool(row.get("selection_precision")) for row in rows) / total if total else 0.0,
        "mean_selected_context_chars": sum(row["selected_context_chars"] for row in rows) / total if total else 0.0,
        "mean_evidence_count": sum(row["evidence_count"] for row in rows) / total if total else 0.0,
        "by_question_type": by_type,
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Relation / Role-aware Selection Experiment",
        "",
        "Candidate pool, keywords, answer model (qwen3:8b, temperature 0, 16,384-token window) and prompt template are identical to `evidence-selector-v1`. Only the selection strategy changes.",
        "",
        "## Core result table",
        "",
        "| Method | Candidate Recall | Role Coverage | Full Coverage Rate | Precision | Accuracy | Groundedness | Hallucination | MULTIHOP Acc | Avg Context (chars) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["methods"]:
        summary = item["summary"]
        multi = summary["by_question_type"].get("MULTIHOP", {})
        lines.append(
            f"| {item['label']} | {summary['candidate_recall']:.4f} | {summary['role_coverage']:.4f} | "
            f"{summary['full_role_coverage_rate']:.4f} | {summary['selection_precision']:.4f} | "
            f"{summary['answer_accuracy']:.4f} | {summary['groundedness']:.4f} | "
            f"{summary['hallucination_rate']:.4f} | {multi.get('answer_accuracy', float('nan')):.4f} | "
            f"{summary['mean_selected_context_chars']:.0f} |"
        )
    lines.extend(["", "## MULTIHOP per-question detail", ""])
    lines.append("| Question | Candidate Recall | Role Coverage (selected) | Method | Accuracy |")
    lines.append("|---|---:|---:|---|---|")
    for item in payload["methods"]:
        method = item["method"]
        for row in item["results"]:
            if row["question_group"] != "MULTIHOP":
                continue
            lines.append(
                f"| {row['question_id']} | {row['candidate_recall']:.2f} | {row['role_coverage']:.2f} | "
                f"{method} | {'PASS' if row['metrics']['exact_match'] else 'FAIL'} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `candidate_recall < 1` marks R1 (retrieval failure): the Top-20 pool lacks oracle evidence and no selector can repair it.",
            "- `role_coverage < candidate_recall` marks R2 (selection failure): evidence was available but the selector dropped it.",
            "- The guaranteed arm force-adds the best matching candidate for every oracle fact present in the pool, so its role coverage equals candidate recall by construction.",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    evidence = json.loads(args.evidence_results.read_text(encoding="utf-8"))
    select5 = next(item for item in evidence["methods"] if item["method"] == "select5")
    saved_rows = {row["question_id"]: row for row in select5["results"]}
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = {q["id"]: q for q in oracle["questions"]}
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    frozen = json.loads(args.frozen_prompts.read_text(encoding="utf-8"))
    prefix, _ = _split_prompt(str(frozen["prompts"][0]["prompt"]))

    rows: list[dict[str, Any]] = []
    if args.resume and args.output_json.exists():
        saved_payload = json.loads(args.output_json.read_text(encoding="utf-8"))
        for method in saved_payload.get("methods", []):
            rows.extend(method.get("results", []))
    done = {(row["question_id"], row["method"]) for row in rows}
    ordered_ids = list(saved_rows.keys())
    if args.max_cases > 0:
        ordered_ids = ordered_ids[: args.max_cases]

    def payload(status: str) -> dict[str, Any]:
        methods = []
        for method, label in (("select5", "Select5 (saved)"), ("select5_role_prompt", "Select5 + Role Prompt"), ("select5_role_guaranteed", "Select5 + Role Guaranteed")):
            method_rows = [row for row in rows if row["method"] == method]
            methods.append({"method": method, "label": label, "summary": _metrics(method_rows), "results": method_rows})
        return {"dataset": str(args.dataset), "status": status, "methods": methods}

    for index, question_id in enumerate(ordered_ids, start=1):
        base = saved_rows[question_id]
        if question_id not in questions:
            continue
        question = questions[question_id]
        candidates = _make_candidates(_entity_rows_from_context(base["candidate_context"], limit=20))
        saved_ids = list(base["selected_evidence_ids"])
        rebuilt_ids = [item["evidence_id"] for item in candidates]
        if rebuilt_ids != list(base["candidate_evidence_ids"]):
            raise RuntimeError(f"Candidate pool rebuild mismatch for {question_id}")
        evidence_facts = [
            facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id
        ]
        candidate_covered = _facts_covered(candidates, evidence_facts)
        candidate_recall = len(candidate_covered) / len(evidence_facts) if evidence_facts else 1.0

        def run_arm(method: str, selected: list[dict[str, Any]], selector_raw: str | None, answer: str | None, context: str | None) -> dict[str, Any]:
            selected_ids = [item["evidence_id"] for item in selected]
            matched_facts = _facts_covered(selected, evidence_facts)
            role_coverage = len(matched_facts) / len(evidence_facts) if evidence_facts else 1.0
            relevant = [item["evidence_id"] for item in selected if item["evidence_id"] in base["candidate_oracle_evidence_ids"]]
            if context is None:
                context = _render_context(selected)
            if answer is None:
                answer = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + context,
                    user=str(question["question"]),
                    num_predict=256,
                )
            metrics = score_answer(
                answer_text=answer,
                expected=str(question.get("answer", "")),
                question=question,
                evidence_facts=evidence_facts,
                references_blob=context,
            )
            return {
                "question_id": question_id,
                "method": method,
                "question_group": _group(question),
                "candidate_recall": candidate_recall,
                "role_coverage": role_coverage,
                "selection_precision": len(relevant) / len(selected_ids) if selected_ids else None,
                "evidence_count": len(selected_ids),
                "selected_context_chars": len(context),
                "selected_evidence_ids": selected_ids,
                "selector_raw_output": selector_raw,
                "answer": answer,
                "expected": question.get("answer", ""),
                "metrics": metrics,
            }

        # Arm 1: saved Select5 (no re-generation).
        if ("select5", question_id) not in done:
            saved_selected = [item for item in candidates if item["evidence_id"] in saved_ids]
            rows.append(
                run_arm(
                    "select5",
                    saved_selected,
                    base.get("selector_raw_output"),
                    base["answer"],
                    base["selected_context"],
                )
            )

        # Arm 2: role-aware LLM prompt.
        if ("select5_role_prompt", question_id) not in done:
            raw_selector = _chat_ollama(
                host=args.ollama_url,
                model=args.model,
                system="Follow the requested JSON schema exactly.",
                user=_role_prompt(str(question["question"]), candidates, 5),
                num_predict=160,
            )
            ids = _parse_selection(raw_selector, candidates, 5)
            selected = [item for item in candidates if item["evidence_id"] in ids]
            rows.append(run_arm("select5_role_prompt", selected, raw_selector, None, None))

        # Arm 3: saved Select5 + deterministic oracle-role repair.
        if ("select5_role_guaranteed", question_id) not in done:
            selected = [item for item in candidates if item["evidence_id"] in saved_ids]
            before = {item["evidence_id"] for item in selected}
            matched_facts = {str(fact["fact_id"]) for fact in evidence_facts if any(_contains_fact(item, fact) for item in selected)}
            additions = []
            for fact in evidence_facts:
                fid = str(fact.get("fact_id") or "")
                if fid in matched_facts:
                    continue
                matches = [item for item in candidates if item["evidence_id"] not in before and _contains_fact(item, fact)]
                if matches:
                    chosen = max(matches, key=lambda item: len(item["text"]))
                    additions.append(chosen)
                    before.add(chosen["evidence_id"])
            repaired = selected + additions
            selector_note = (
                None
                if not additions
                else json.dumps({"repaired": True, "added_evidence_ids": [item["evidence_id"] for item in additions]}, ensure_ascii=False)
            )
            if not additions:
                rows.append(
                    run_arm(
                        "select5_role_guaranteed",
                        selected,
                        selector_note,
                        base["answer"],
                        base["selected_context"],
                    )
                )
            else:
                rows.append(run_arm("select5_role_guaranteed", repaired, selector_note, None, None))

        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(payload("in_progress"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[{index}/{len(saved_rows)}] {question_id}", flush=True)
    return payload("complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("memory_data_service/generated/rich-smoke-v1"))
    parser.add_argument(
        "--evidence-results",
        type=Path,
        default=Path("memory_eval_tests/runs/evidence-selector-v1/evidence_selector_results.json"),
    )
    parser.add_argument(
        "--frozen-prompts",
        type=Path,
        default=Path("memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/prompts_kg_mix_top5_ctx8192.json"),
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(_run(args))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_report(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
