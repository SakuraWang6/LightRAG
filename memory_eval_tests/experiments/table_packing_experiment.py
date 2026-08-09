"""Table-aware evidence packing on the saved Select5 packs.

P1-3 of the four-stage plan.  The selected evidence (and its answers) are reused
verbatim from ``evidence-selector-v1``; the packing arms change only how the
selected table evidence is rendered to the answer model:

* ``select5`` (saved baseline): terse KG entity rows, empty Document Chunks.
* ``table_pack_full``: adds the full structured target table (sidecar rows) to
  the Document Chunks section of the selected pack.
* ``table_pack_minimal``: adds only header rows, the gold row and one neighbour
  on each side, trimming long/adjacent tables.
* ``table_pack_focus``: drops selected entity rows for non-target tables before
  packing, keeping narrative/equation/figure rows and the target table chunk.

Target tables are resolved from the question's evidence facts (fact -> sidecar
table).  Questions whose selected pack contains no table fact are unchanged and
reuse the saved answer, so the arms are a strict rendering delta.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.common.selectors import (
    contains_fact,
    group,
    make_candidates,
    simple_chat_ollama,
    split_prompt,
)
from memory_eval_tests.experiments.common.tables import (
    find_table_for_fact,
    load_sidecar_tables,
    table_markdown,
)
from memory_eval_tests.experiments.legacy_adapter import legacy_spec
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


def _render_context(rows: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> str:
    entity = "Knowledge Graph Data (Entity):\n\n```json\n" + "\n".join(
        json.dumps(row["raw"], ensure_ascii=False) for row in rows
    ) + "\n```\n"
    relationships = "Knowledge Graph Data (Relationship):\n\n```json\n\n```\n"
    documents = "Document Chunks:\n\n```json\n" + "\n".join(
        json.dumps(chunk, ensure_ascii=False) for chunk in chunks
    ) + "\n```\n"
    return entity + relationships + documents


def _gold_row_index(content: str, fact_id: str) -> int | None:
    try:
        rows = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(rows, list):
        return None
    for index, row in enumerate(rows):
        if any(str(cell) == fact_id for cell in (row if isinstance(row, list) else [row])):
            return index
    return None


def _minimal_markdown(content: str, fact_id: str, neighbors: int = 1) -> str:
    try:
        rows = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(rows, list) or not rows:
        return content
    gold = _gold_row_index(content, fact_id)
    if gold is None:
        return table_markdown(content)
    keep = set(range(min(2, len(rows))))
    keep.update(range(max(0, gold - neighbors), min(len(rows), gold + neighbors + 1)))
    selected_rows = [rows[index] for index in sorted(keep)]
    lines = []
    for index, row in enumerate(selected_rows):
        cells = [str(cell or "") for cell in (row if isinstance(row, list) else [row])]
        lines.append("| " + " | ".join(cells) + " |")
        if index == 1:
            lines.append("|" + "|".join([" --- "] * len(cells)) + "|")
    return "\n".join(lines)


def _target_tables(
    evidence_facts: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any], str]]:
    result = []
    for fact in evidence_facts:
        if str(fact.get("object_type") or "") != "table":
            continue
        matched = find_table_for_fact(fact, tables)
        if matched:
            table_id, table = matched
            result.append((table_id, table, str(fact.get("fact_id") or "")))
    return result


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
                "table_cell_accuracy": _average(subset, "table_cell_correct"),
                "numeric_unit_accuracy": _average(subset, "numeric_unit_correct"),
            }
    return {
        "cases": total,
        "answer_accuracy": rate("exact_match"),
        "groundedness": rate("grounded"),
        "ungrounded_rate": rate("ungrounded"),
        "table_cell_accuracy": _average(rows, "table_cell_correct"),
        "numeric_unit_accuracy": _average(rows, "numeric_unit_correct"),
        "mean_selected_context_chars": sum(row["selected_context_chars"] for row in rows) / total if total else 0.0,
        "changed_cases": sum(bool(row.get("changed")) for row in rows),
        "by_question_type": by_type,
    }


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row["metrics"].get(key) for row in rows if row["metrics"].get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Table-aware Evidence Packing Experiment",
        "",
        "Answer model, prompt template and selected evidence come from `evidence-selector-v1` Select5; only the table rendering in the pack changes.",
        "",
        "## Core result table",
        "",
        "| Pack | Accuracy | Groundedness | Hallucination | Table Cell | Numeric | Avg Context (chars) | Changed Cases |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["methods"]:
        summary = item["summary"]
        lines.append(
            f"| {item['label']} | {summary['answer_accuracy']:.4f} | {summary['groundedness']:.4f} | "
            f"{summary['ungrounded_rate']:.4f} | {_format(summary['table_cell_accuracy'])} | "
            f"{_format(summary['numeric_unit_accuracy'])} | {summary['mean_selected_context_chars']:.0f} | "
            f"{summary['changed_cases']} |"
        )
    lines.extend(["", "## TABLE per-question detail", ""])
    lines.append("| Question | Pack | Context (chars) | Expected | Answer (first 140 chars) | PASS |")
    lines.append("|---|---|---:|---|---|---|")
    for item in payload["methods"]:
        for row in item["results"]:
            if row["question_group"] != "TABLE":
                continue
            lines.append(
                f"| {row['question_id']} | {item['method']} | {row['selected_context_chars']} | "
                f"{row['expected']} | {row['answer'][:140]} | {'PASS' if row['metrics']['exact_match'] else 'FAIL'} |"
            )
    return "\n".join(lines)


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    evidence = json.loads(args.evidence_results.read_text(encoding="utf-8"))
    select5 = next(item for item in evidence["methods"] if item["method"] == "select5")
    saved_rows = {row["question_id"]: row for row in select5["results"]}
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
    ordered_ids = list(saved_rows.keys())
    if args.max_cases > 0:
        ordered_ids = ordered_ids[: args.max_cases]

    arms = (
        ("select5", "Select5 (saved)"),
        ("table_pack_full", "Table Pack Full"),
        ("table_pack_minimal", "Table Pack Minimal"),
        ("table_pack_focus", "Table Pack Focus"),
    )

    def payload(status: str) -> dict[str, Any]:
        methods = []
        for method, label in arms:
            method_rows = [row for row in rows if row["method"] == method]
            methods.append({"method": method, "label": label, "summary": _metrics(method_rows), "results": method_rows})
        return {"dataset": str(args.dataset), "status": status, "methods": methods}

    for index, question_id in enumerate(ordered_ids, start=1):
        base = saved_rows[question_id]
        if question_id not in questions:
            continue
        question = questions[question_id]
        evidence_facts = [
            facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id
        ]
        candidates = make_candidates(_entity_rows_from_context(base["candidate_context"], limit=20))
        if [item["evidence_id"] for item in candidates] != list(base["candidate_evidence_ids"]):
            raise RuntimeError(f"Candidate pool rebuild mismatch for {question_id}")
        selected = [item for item in candidates if item["evidence_id"] in base["selected_evidence_ids"]]
        target_tables = _target_tables(evidence_facts, tables)
        target_ids = {table_id for table_id, _, _ in target_tables}

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
                        "selected_context": base["selected_context"],
                        "answer": base["answer"],
                        "expected": question.get("answer", ""),
                        "metrics": {key: base[key] for key in ("exact_match", "grounded", "ungrounded", "table_cell_correct", "numeric_unit_correct") if key in base},
                    }
                )
                continue
            if not target_tables:
                rows.append(
                    {
                        "question_id": question_id,
                        "method": method,
                        "question_group": group(question),
                        "changed": False,
                        "selected_context_chars": len(base["selected_context"]),
                        "selected_context": base["selected_context"],
                        "answer": base["answer"],
                        "expected": question.get("answer", ""),
                        "metrics": {key: base[key] for key in ("exact_match", "grounded", "ungrounded", "table_cell_correct", "numeric_unit_correct") if key in base},
                    }
                )
                continue

            chunks = []
            for table_id, table, fact_id in target_tables:
                content = str(table.get("content") or "")
                if method == "table_pack_minimal":
                    content_text = _minimal_markdown(content, fact_id)
                else:
                    content_text = table_markdown(content)
                chunks.append(
                    {
                        "chunk_id": table_id,
                        "content": content_text,
                    }
                )
            if method == "table_pack_focus":
                kept = []
                for item in selected:
                    raw_text = f"{item['entity']} {item['text']}"
                    is_table_row = str(item["entity"]).lower().startswith("table")
                    mentions_target = any(target in raw_text for target in target_ids) or any(
                        contains_fact(item, fact) for fact in evidence_facts
                    )
                    if not is_table_row or mentions_target:
                        kept.append(item)
                packed_rows = kept
            else:
                packed_rows = selected
            context = _render_context(packed_rows, chunks)
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
                    "selected_context": context,
                    "chunks": chunks,
                    "kept_row_count": len(packed_rows),
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
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(_run(args))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_report(payload), encoding="utf-8")


if __name__ == "__main__":
    main()


spec = legacy_spec(
    experiment_id="table_packing",
    label="表格打包消融",
    description=(
        "在保存的 Select5 证据包上对比表格完整打包 / 最小化打包 / 聚焦打包 "
        "三种渲染差异。"
    ),
    run=_run,
    artifact_stem="table_packing",
    render_report=_render_report,
    extra_paths={
        "evidence_results": (
            "memory_eval_tests/runs/evidence-selector-v1/evidence_selector_results.json"
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
