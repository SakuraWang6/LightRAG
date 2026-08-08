"""Native-vs-oracle structure ablation using saved Select5 evidence packs.

Native answers are reused from the completed Select5 experiment because they
already saw exactly the saved evidence text with no structural metadata.  The
oracle arm appends only metadata from the synthetic object/relation oracle for
facts that are demonstrably present in that same evidence pack.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from memory_eval_tests.answer_eval import score_answer
from memory_eval_tests.dataset_client import DatasetClient
from memory_eval_tests.evidence_selector_experiment import _chat_ollama, _split_prompt
from memory_eval_tests.kg_ablation import DEFAULT_STORAGE, _find_rag, _load_keyword_cache, _query_param


def _contains_fact(context: str, fact: dict[str, Any]) -> bool:
    lowered = context.lower()
    return any(str(value).lower() in lowered for value in (fact.get("fact_id"), fact.get("answer")) if value)


def _oracle_metadata(
    *, context: str, evidence_facts: list[dict[str, Any]], objects: dict[str, dict[str, Any]],
    order: dict[str, int], relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    object_ids = []
    for fact in evidence_facts:
        object_id = str(fact.get("object_id_hint") or "")
        if object_id and object_id in objects and _contains_fact(context, fact):
            object_ids.append(object_id)
    for object_id in dict.fromkeys(object_ids):
        obj = objects[object_id]
        index = order[object_id]
        item = {
            "object_id": object_id,
            "object_type": obj.get("object_type"),
            "page": obj.get("page_start"),
            "document_order": index,
            "section": obj.get("section"),
            "parent_id": obj.get("parent_id") or None,
            "previous_object": next((key for key, value in order.items() if value == index - 1), None),
            "next_object": next((key for key, value in order.items() if value == index + 1), None),
            "relations": [
                {"relation_type": rel["relation_type"], "source_id": rel["source_id"], "target_id": rel["target_id"]}
                for rel in relations
                if rel.get("source_id") == object_id or rel.get("target_id") == object_id
            ],
        }
        result.append({key: value for key, value in item.items() if value not in (None, [], "")})
    return result


def _group(question: dict[str, Any]) -> str:
    if question.get("expected_behavior") == "abstain":
        return "ABSTAIN"
    kind = str(question.get("question_type", "")).lower()
    if "multi" in kind or "cross" in kind:
        return "MULTIHOP"
    if "table" in kind:
        return "TABLE"
    if "figure" in kind or "fig" in kind:
        return "FIGURE"
    if "equation" in kind or "formula" in kind:
        return "FORMULA"
    return "FACT"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    rate = lambda key: sum(bool(row["metrics"].get(key)) for row in rows) / total if total else 0.0
    by_type = {}
    for name in ("FACT", "TABLE", "FIGURE", "FORMULA", "MULTIHOP", "ABSTAIN"):
        subset = [row for row in rows if row["question_group"] == name]
        if subset:
            by_type[name] = {"cases": len(subset), "accuracy": sum(bool(r["metrics"]["exact_match"]) for r in subset) / len(subset)}
    return {"cases": total, "accuracy": rate("exact_match"), "groundedness": rate("grounded"), "hallucination_rate": rate("hallucinated"), "by_question_type": by_type}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    evidence = json.loads(args.evidence_results.read_text(encoding="utf-8"))
    select5 = next(item for item in evidence["methods"] if item["method"] == "select5")
    saved = {row["question_id"]: row for row in select5["results"]}
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = {q["id"]: q for q in oracle["questions"]}
    facts = {fact["fact_id"]: fact for fact in oracle["facts"]}
    objects_payload = json.loads((args.dataset / "objects.json").read_text(encoding="utf-8"))
    object_list = objects_payload["objects"]
    objects = {item["object_id"]: item for item in object_list}
    order = {item["object_id"]: index for index, item in enumerate(object_list)}
    relations = json.loads((args.dataset / "relations.json").read_text(encoding="utf-8"))["relations"]
    cache = _load_keyword_cache(args.storage_dir)
    rows = []
    rag = _find_rag()
    await rag.initialize_storages()
    rag.llm_response_cache.global_config["enable_llm_cache"] = False
    try:
        for index, question_id in enumerate(saved, start=1):
            base = saved[question_id]
            question = questions[question_id]
            evidence_facts = [facts[item] for item in question.get("evidence_fact_ids", []) if item in facts]
            high, low = cache[question["question"]]
            prompt = await rag.aquery(question["question"], param=_query_param(top_k=20, high_keywords=high, low_keywords=low, prompt_only=True))
            prefix, user = _split_prompt(str(prompt))
            native_context = base["selected_context"]
            metadata = _oracle_metadata(context=native_context, evidence_facts=evidence_facts, objects=objects, order=order, relations=relations)
            oracle_context = native_context + "\nOracle Structure Metadata (metadata only; do not treat as new evidence):\n```json\n" + json.dumps(metadata, ensure_ascii=False) + "\n```\n"
            oracle_answer = _chat_ollama(host=args.ollama_url, model=args.model, system=prefix + oracle_context, user=user, num_predict=128)
            oracle_metrics = score_answer(answer_text=oracle_answer, expected=question["answer"], question=question, evidence_facts=evidence_facts, references_blob=native_context)
            native_metrics = {key: base[key] for key in oracle_metrics if key in base}
            rows.append({
                "question_id": question_id, "question_group": _group(question), "evidence_ids": base["selected_evidence_ids"],
                "native_metadata": [], "oracle_metadata": metadata, "native_answer": base["answer"], "oracle_answer": oracle_answer,
                "native_metrics": native_metrics, "oracle_metrics": oracle_metrics,
            })
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps({"status": "in_progress", "results": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[{index}/{len(saved)}] {question_id}", flush=True)
    finally:
        await rag.finalize_storages()
    native_rows = [{"question_group": row["question_group"], "metrics": row["native_metrics"]} for row in rows]
    oracle_rows = [{"question_group": row["question_group"], "metrics": row["oracle_metrics"]} for row in rows]
    return {"status": "complete", "dataset": str(args.dataset), "native": _metrics(native_rows), "oracle_full": _metrics(oracle_rows), "results": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("memory_data_service/generated/rich-smoke-v1"))
    parser.add_argument("--storage-dir", type=Path, default=DEFAULT_STORAGE)
    parser.add_argument("--evidence-results", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    args = parser.parse_args()
    payload = asyncio.run(run(args))
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
