"""Oracle Evidence Pack Upper Bound for the answer model.

P0-1 of the four-stage plan.  The runner builds a *perfect* evidence pack for
every question from the synthetic oracle (supporting fact statements, object
text, structured table rows from the parsed sidecar, and oracle relations), and
asks qwen3:8b to answer with the same prompt template and decoding settings as
the Select5 experiments.  Retrieval and selection are bypassed on purpose: the
result is the generation ceiling of the answer model given correct evidence.

Two arms are measured:

* ``oracle_text``: one entity row per oracle fact (fact statement text only).
* ``oracle_full``: entity rows plus oracle relation rows and structured
  Document Chunks (full table rows, figure/equation object text).

The delta between the arms isolates how much structural table content helps the
answer model extract values, which feeds directly into the Table-aware Packing
design of P1-3.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.evidence_selector_experiment import (
    _chat_ollama,
    _group,
    _split_prompt,
)
from memory_eval_tests.online.answer_eval import score_answer


def _load_sidecar_tables(sidecar_parsed_dir: Path) -> dict[str, dict[str, Any]]:
    tables_file = (
        sidecar_parsed_dir
        if sidecar_parsed_dir.is_file()
        else sidecar_parsed_dir / "rich-smoke-v1.tables.json"
    )
    payload = json.loads(tables_file.read_text(encoding="utf-8"))
    return dict(payload.get("tables") or {})


def _table_markdown(content: str) -> str:
    try:
        rows = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(rows, list) or not rows:
        return content
    lines = []
    for index, row in enumerate(rows):
        cells = [str(cell or "") for cell in (row if isinstance(row, list) else [row])]
        lines.append("| " + " | ".join(cells) + " |")
        if index == 1:
            lines.append("|" + "|".join([" --- "] * len(cells)) + "|")
    return "\n".join(lines)


def _entity_section(rows: list[dict[str, Any]]) -> str:
    return "Knowledge Graph Data (Entity):\n\n```json\n" + "\n".join(
        json.dumps(row, ensure_ascii=False) for row in rows
    ) + "\n```\n"


def _relationship_section(rows: list[dict[str, Any]]) -> str:
    return "Knowledge Graph Data (Relationship):\n\n```json\n" + "\n".join(
        json.dumps(row, ensure_ascii=False) for row in rows
    ) + "\n```\n"


def _chunks_section(rows: list[dict[str, Any]]) -> str:
    return "Document Chunks:\n\n```json\n" + "\n".join(
        json.dumps(row, ensure_ascii=False) for row in rows
    ) + "\n```\n"


def _fact_text(fact: dict[str, Any], supports: dict[str, str], objects: dict[str, dict[str, Any]]) -> str:
    fid = str(fact.get("fact_id") or "")
    if supports.get(fid):
        return supports[fid]
    if fact.get("expected_text"):
        return str(fact["expected_text"])
    obj = objects.get(str(fact.get("object_id_hint") or ""), {})
    return str(obj.get("text") or "")


def _find_table_for_fact(
    fact: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    fid = str(fact.get("fact_id") or "")
    for table_id, table in tables.items():
        content = str(table.get("content") or "")
        if fid in content:
            return table_id, table
    return None


def _build_oracle_context(
    *,
    question: dict[str, Any],
    facts: list[dict[str, Any]],
    objects: dict[str, dict[str, Any]],
    supports: dict[str, str],
    relations: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    arm: str,
) -> str:
    entity_rows: list[dict[str, Any]] = []
    relationship_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    for fact in facts:
        fid = str(fact.get("fact_id") or "")
        obj = objects.get(str(fact.get("object_id_hint") or ""), {})
        text = _fact_text(fact, supports, objects)
        entity_type = str(obj.get("object_type") or fact.get("fact_type") or "concept")
        entity_rows.append(
            {
                "entity": fid,
                "type": entity_type,
                "description": text,
            }
        )
        if arm != "oracle_full":
            continue
        if entity_type == "table":
            matched = _find_table_for_fact(fact, tables)
            if matched:
                table_id, table = matched
                chunk_rows.append(
                    {
                        "chunk_id": str(table.get("id") or table_id),
                        "content": _table_markdown(str(table.get("content") or "")),
                    }
                )
        elif obj:
            chunk_rows.append(
                {
                    "chunk_id": str(obj.get("object_id") or fid),
                    "content": str(obj.get("text") or text),
                }
            )
    if arm == "oracle_full":
        evidence_object_ids = {
            str(fact.get("object_id_hint") or "") for fact in facts if fact.get("object_id_hint")
        }
        evidence_fact_ids = {str(fact.get("fact_id") or "") for fact in facts}
        for rel in relations:
            source = str(rel.get("source_id") or "")
            target = str(rel.get("target_id") or "")
            rel_type = str(rel.get("relation_type") or "")
            if rel_type == "contains":
                continue
            if source in evidence_object_ids and target in evidence_fact_ids:
                relationship_rows.append(
                    {
                        "entity1": source,
                        "entity2": target,
                        "description": str(rel.get("evidence_text") or f"{rel_type} {target}"),
                    }
                )
    return (
        _entity_section(entity_rows)
        + _relationship_section(relationship_rows)
        + _chunks_section(chunk_rows)
    )


def _metric_for(rows: list[dict[str, Any]], key: str) -> float:
    return sum(bool(row["metrics"].get(key)) for row in rows) / len(rows) if rows else 0.0


def applicable_for(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row["metrics"].get(key) for row in rows if row["metrics"].get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)

    grouped: dict[str, dict[str, Any]] = {}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        subset = [row for row in rows if row["question_group"] == name]
        if subset:
            grouped[name] = {
                "cases": len(subset),
                "answer_accuracy": _metric_for(subset, "exact_match"),
                "groundedness": _metric_for(subset, "grounded"),
                "abstention_accuracy": applicable_for(subset, "abstention_correct"),
            }
    return {
        "cases": total,
        "answer_accuracy": _metric_for(rows, "exact_match"),
        "groundedness": _metric_for(rows, "grounded"),
        "hallucination_rate": _metric_for(rows, "hallucinated"),
        "abstention_accuracy": applicable_for(rows, "abstention_correct"),
        "numeric_unit_accuracy": applicable_for(rows, "numeric_unit_correct"),
        "formula_accuracy": applicable_for(rows, "formula_correct"),
        "table_cell_accuracy": applicable_for(rows, "table_cell_correct"),
        "mean_context_chars": sum(row["context_chars"] for row in rows) / total if total else 0.0,
        "by_question_type": grouped,
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Oracle Evidence Pack Upper Bound",
        "",
        "Answer model: qwen3:8b, temperature 0, 16,384-token window, same prompt template as the Select5 experiments. The oracle pack bypasses retrieval and selection entirely; it is the generation ceiling with correct, sufficient evidence.",
        "",
        "## Core result table",
        "",
        "| Arm | Accuracy | Groundedness | Hallucination | Abstention | Numeric | Formula | Table Cell | Avg Context (chars) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    baseline = payload.get("selector_baseline")
    if baseline:
        lines.append(
            f"| Select5 (saved baseline) | {baseline['answer_accuracy']:.4f} | {baseline['groundedness']:.4f} | "
            f"{baseline['hallucination_rate']:.4f} | {_format(baseline['abstention_accuracy'])} | "
            f"{_format(baseline['numeric_unit_accuracy'])} | {_format(baseline['formula_accuracy'])} | "
            f"{_format(baseline['table_cell_accuracy'])} | {baseline['mean_selected_context_chars']:.0f} |"
        )
    for item in payload["arms"]:
        summary = item["summary"]
        lines.append(
            f"| {item['label']} | {summary['answer_accuracy']:.4f} | {summary['groundedness']:.4f} | "
            f"{summary['hallucination_rate']:.4f} | {_format(summary['abstention_accuracy'])} | "
            f"{_format(summary['numeric_unit_accuracy'])} | {_format(summary['formula_accuracy'])} | "
            f"{_format(summary['table_cell_accuracy'])} | {summary['mean_context_chars']:.0f} |"
        )
    lines.extend(["", "## Per-type answer accuracy", ""])
    lines.append("| Type | Cases | Oracle-Text | Oracle-Full |")
    lines.append("|---|---:|---:|---:|")
    arms = {item["arm"]: item["summary"]["by_question_type"] for item in payload["arms"]}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        counts = [arms[arm].get(name, {}).get("cases") for arm in arms]
        cases = next((value for value in counts if value), 0)
        if not cases:
            continue
        text = arms.get("oracle_text", {}).get(name, {}).get("answer_accuracy")
        full = arms.get("oracle_full", {}).get(name, {}).get("answer_accuracy")
        lines.append(f"| {name} | {cases} | {_format(text)} | {_format(full)} |")
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "- Evidence packs are built from `facts.json`, `objects.json`, `relations.json` and the parsed sidecar `tables.json`; no retrieval or selection is invoked.",
            "- The system prefix is taken from the frozen Top-5 KG prompts so the answer template matches the Select5 runs.",
            "- Raw answers, rendered contexts and per-question metrics are retained in the JSON artifact.",
            "",
        ]
    )
    return "\n".join(lines)


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = list(oracle["questions"])
    if args.max_cases > 0:
        questions = questions[: args.max_cases]
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    objects = {obj["object_id"]: obj for obj in (json.loads((args.dataset / "objects.json").read_text(encoding="utf-8"))["objects"])}
    relations = json.loads((args.dataset / "relations.json").read_text(encoding="utf-8"))["relations"]
    supports = {
        str(rel["target_id"]): str(rel["evidence_text"])
        for rel in relations
        if rel.get("relation_type") == "supports" and str(rel.get("target_id", "")).startswith("FACT") and rel.get("evidence_text")
    }
    tables = _load_sidecar_tables(args.sidecar_parsed_dir)
    frozen = json.loads(args.frozen_prompts.read_text(encoding="utf-8"))
    prefix, _ = _split_prompt(str(frozen["prompts"][0]["prompt"]))
    selector_baseline: dict[str, Any] | None = None
    if args.selector_results is not None:
        saved_selector = json.loads(args.selector_results.read_text(encoding="utf-8"))
        select5 = next(item for item in saved_selector["methods"] if item["method"] == "select5")
        summary = select5["summary"]
        selector_baseline = {
            key: summary[key]
            for key in (
                "answer_accuracy",
                "groundedness",
                "hallucination_rate",
                "abstention_accuracy",
                "numeric_unit_accuracy",
                "formula_accuracy",
                "table_cell_accuracy",
            )
        }
        selector_baseline["mean_selected_context_chars"] = summary["mean_selected_context_chars"]

    rows: list[dict[str, Any]] = []
    if args.resume and args.output_json.exists():
        saved = json.loads(args.output_json.read_text(encoding="utf-8"))
        rows = list(saved.get("results", []))
    done_ids = {row["question_id"] for row in rows}

    def payload(status: str) -> dict[str, Any]:
        arm_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            arm_rows[row["arm"]].append(row)
        arms = [
            {
                "arm": arm,
                "label": "Oracle-Text" if arm == "oracle_text" else "Oracle-Full",
                "summary": _summary(arm_rows[arm]),
            }
            for arm in ("oracle_text", "oracle_full")
        ]
        return {
            "dataset": str(args.dataset),
            "model": args.model,
            "status": status,
            "selector_baseline": selector_baseline,
            "arms": arms,
            "results": rows,
        }

    try:
        for index, question in enumerate(questions, start=1):
            if question["id"] in done_ids:
                continue
            evidence_facts = [
                facts_by_id[fid]
                for fid in question.get("evidence_fact_ids", [])
                if fid in facts_by_id
            ]
            for arm in ("oracle_text", "oracle_full"):
                context = _build_oracle_context(
                    question=question,
                    facts=evidence_facts,
                    objects=objects,
                    supports=supports,
                    relations=relations,
                    tables=tables,
                    arm=arm,
                )
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
                rows.append(
                    {
                        "question_id": question["id"],
                        "arm": arm,
                        "question_group": _group(question),
                        "question_type": question.get("question_type", ""),
                        "evidence_fact_ids": question.get("evidence_fact_ids", []),
                        "context_chars": len(context),
                        "context": context,
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
            print(f"[{index}/{len(questions)}] {question['id']}", flush=True)
    finally:
        pass
    return payload("complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("memory_data_service/generated/rich-smoke-v1"))
    parser.add_argument(
        "--frozen-prompts",
        type=Path,
        default=Path("memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/prompts_kg_mix_top5_ctx8192.json"),
    )
    parser.add_argument(
        "--selector-results",
        type=Path,
        default=Path("memory_eval_tests/runs/evidence-selector-v1/evidence_selector_results.json"),
    )
    parser.add_argument(
        "--sidecar-parsed-dir",
        type=Path,
        default=Path("memory_eval_tests/runs/offline/rich-smoke-v1/sidecar/rich-smoke-v1.docx.parsed"),
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
