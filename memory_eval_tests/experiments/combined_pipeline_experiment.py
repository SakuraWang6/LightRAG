"""Combined selector + packing pipeline (smoke-level final method).

The individual ablations showed that MULTIHOP requires complete role coverage
(P0-2), structured table content (P1-3) and high selection precision in the
same pack.  This runner combines them and measures two variants:

* ``combined_focus``: role-guaranteed selection (from ``relation-selector-v1``)
  + focus packing (drop non-target table rows, attach the target sidecar
  table).  This is the realistic final method.
* ``combined_precision``: keep only candidate rows that contain an oracle
  evidence fact + attach the target sidecar table.  This is the precision
  upper bound for the same retrieval.

``select5`` from ``evidence-selector-v1`` is kept as the untouched baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common.selectors import (
    contains_fact,
    entity_rows_from_context,
    group,
    make_candidates,
    render_combined_context,
    simple_chat_ollama,
    split_prompt,
    target_tables,
)
from memory_eval_tests.experiments.common.tables import (
    load_sidecar_tables,
    table_markdown,
)
from memory_eval_tests.experiments.legacy_adapter import legacy_spec
from memory_eval_tests.online.answer_eval import score_answer


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
            }
    return {
        "cases": total,
        "answer_accuracy": rate("exact_match"),
        "groundedness": rate("grounded"),
        "ungrounded_rate": rate("ungrounded"),
        "table_cell_accuracy": _average(rows, "table_cell_correct"),
        "mean_selected_context_chars": sum(row["selected_context_chars"] for row in rows) / total if total else 0.0,
        "changed_cases": sum(bool(row.get("changed")) for row in rows),
        "by_question_type": by_type,
    }


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row["metrics"].get(key) for row in rows if row["metrics"].get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Combined Selector + Packing Pipeline",
        "",
        "## Core result table",
        "",
        "| Method | Accuracy | Groundedness | Hallucination | Table Cell | Avg Context (chars) | Changed |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["methods"]:
        s = item["summary"]
        lines.append(
            f"| {item['label']} | {s['answer_accuracy']:.4f} | {s['groundedness']:.4f} | "
            f"{s['ungrounded_rate']:.4f} | {_format(s['table_cell_accuracy'])} | "
            f"{s['mean_selected_context_chars']:.0f} | {s['changed_cases']} |"
        )
    lines.extend(["", "## Per-type accuracy", ""])
    lines.append("| Type | Cases | Select5 | Combined Focus | Combined Precision |")
    lines.append("|---|---:|---:|---:|---:|")
    arms = {m["method"]: m["summary"]["by_question_type"] for m in payload["methods"]}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        cases = next(
            (v.get("cases") for arm in arms.values() if (v := arm.get(name))),
            0,
        )
        if not cases:
            continue
        values = [
            _format(arms.get(method, {}).get(name, {}).get("answer_accuracy"))
            for method in ("select5", "combined_focus", "combined_precision")
        ]
        lines.append(f"| {name} | {cases} | {values[0]} | {values[1]} | {values[2]} |")
    return "\n".join(lines)


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    evidence = json.loads(args.evidence_results.read_text(encoding="utf-8"))
    select5 = next(item for item in evidence["methods"] if item["method"] == "select5")
    saved_rows = {row["question_id"]: row for row in select5["results"]}
    relation = json.loads(args.relation_results.read_text(encoding="utf-8"))
    guaranteed = next(item for item in relation["methods"] if item["method"] == "select5_role_guaranteed")
    guaranteed_by_qid = {row["question_id"]: row for row in guaranteed["results"]}
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = {q["id"]: q for q in oracle["questions"]}
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    tables = load_sidecar_tables(args.sidecar_parsed_dir)
    frozen = json.loads(args.frozen_prompts.read_text(encoding="utf-8"))
    prefix, _ = split_prompt(str(frozen["prompts"][0]["prompt"]))

    rows: list[dict[str, Any]] = []
    if args.resume and args.output_json.exists():
        saved_payload = json.loads(args.output_json.read_text(encoding="utf-8"))
        for method in saved_payload.get("methods", []):
            rows.extend(method.get("results", []))
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        deduped[(row["question_id"], row["method"])] = row
    rows = list(deduped.values())
    done = {(row["question_id"], row["method"]) for row in rows}

    arms = (
        ("select5", "Select5 (saved)"),
        ("combined_focus", "Combined Focus"),
        ("combined_precision", "Combined Precision"),
    )

    def payload(status: str) -> dict[str, Any]:
        methods = []
        for method, label in arms:
            method_rows = [row for row in rows if row["method"] == method]
            methods.append({"method": method, "label": label, "summary": _metrics(method_rows), "results": method_rows})
        return {"dataset": str(args.dataset), "status": status, "methods": methods}

    for index, (question_id, base) in enumerate(saved_rows.items(), start=1):
        if question_id not in questions or question_id not in guaranteed_by_qid:
            continue
        question = questions[question_id]
        evidence_facts = [
            facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id
        ]
        candidates = make_candidates(entity_rows_from_context(base["candidate_context"], limit=20))
        if [item["evidence_id"] for item in candidates] != list(base["candidate_evidence_ids"]):
            raise RuntimeError(f"Candidate pool rebuild mismatch for {question_id}")
        matched_tables = target_tables(evidence_facts, tables)
        target_ids = {table_id for table_id, _ in matched_tables}
        chunks = [
            {"chunk_id": table_id, "content": table_markdown(str(table.get("content") or ""))}
            for table_id, table in matched_tables
        ]

        for method, label in arms:
            if (method, question_id) in done:
                continue
            if method == "select5":
                rows.append(
                    {
                        "question_id": question_id,
                        "method": method,
                        "question_group": group(question),
                        "changed": False,
                        "selected_context_chars": len(base["selected_context"]),
                        "answer": base["answer"],
                        "expected": question.get("answer", ""),
                        "metrics": {key: base[key] for key in ("exact_match", "grounded", "ungrounded", "table_cell_correct") if key in base},
                    }
                )
                continue
            if method == "combined_focus":
                selected_ids = set(guaranteed_by_qid[question_id]["selected_evidence_ids"])
                pool_rows = [item for item in candidates if item["evidence_id"] in selected_ids]
            else:
                pool_rows = [item for item in candidates if any(contains_fact(item, fact) for fact in evidence_facts)]
            kept = []
            for item in pool_rows:
                raw_text = f"{item['entity']} {item['text']}"
                is_table_row = str(item["entity"]).lower().startswith("table")
                mentions_target = any(target in raw_text for target in target_ids) or any(
                    contains_fact(item, fact) for fact in evidence_facts
                )
                if not is_table_row or mentions_target:
                    kept.append(item)
            context = render_combined_context(kept, chunks)
            if context == base["selected_context"]:
                rows.append(
                    {
                        "question_id": question_id,
                        "method": method,
                        "question_group": group(question),
                        "changed": False,
                        "selected_context_chars": len(context),
                        "selected_evidence_ids": [item["evidence_id"] for item in kept],
                        "chunks": chunks,
                        "answer": base["answer"],
                        "expected": question.get("answer", ""),
                        "metrics": {key: base[key] for key in ("exact_match", "grounded", "ungrounded", "table_cell_correct") if key in base},
                    }
                )
                continue
            answer = simple_chat_ollama(
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
            rows.append(
                {
                    "question_id": question_id,
                    "method": method,
                    "question_group": group(question),
                    "changed": True,
                    "selected_context_chars": len(context),
                    "selected_evidence_ids": [item["evidence_id"] for item in kept],
                    "chunks": chunks,
                    "answer": answer,
                    "expected": question.get("answer", ""),
                    "metrics": metrics,
                }
            )
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
        "--relation-results",
        type=Path,
        default=Path("memory_eval_tests/runs/relation-selector-v1/relation_selector_results.json"),
    )
    parser.add_argument(
        "--sidecar-parsed-dir",
        type=Path,
        default=Path("memory_eval_tests/runs/offline/rich-smoke-v1/sidecar/rich-smoke-v1.docx.parsed/rich-smoke-v1.tables.json"),
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
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(_run(args))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_report(payload), encoding="utf-8")


if __name__ == "__main__":
    main()


spec = legacy_spec(
    experiment_id="combined_pipeline",
    label="组合选择+打包管线",
    description=(
        "组合角色兜底选择与目标表格打包：combined_focus（现实方法）与 "
        "combined_precision（精确上限），保留 Select5 基线。"
    ),
    run=_run,
    artifact_stem="combined_pipeline",
    render_report=_render_report,
    extra_paths={
        "evidence_results": (
            "memory_eval_tests/runs/evidence-selector-v1/evidence_selector_results.json"
        ),
        "relation_results": (
            "memory_eval_tests/runs/relation-selector-v1/relation_selector_results.json"
        ),
        "sidecar_parsed_dir": (
            "memory_eval_tests/runs/offline/rich-smoke-v1/sidecar/"
            "rich-smoke-v1.docx.parsed/rich-smoke-v1.tables.json"
        ),
        "frozen_prompts": (
            "memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/"
            "prompts_kg_mix_top5_ctx8192.json"
        ),
    },
)
