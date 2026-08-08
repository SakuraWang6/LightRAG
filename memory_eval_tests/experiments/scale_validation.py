"""20p -> 200p scale validation for the selector/packing pipeline.

P1-4 of the four-stage plan.  The runner executes the same retrieval ->
selection -> answer chain as the smoke experiments on larger datasets and
emits a per-stage degradation report:

* ``ingest``: index the dataset (KG by default, skip-KG with ``--skip-kg``)
  into the working directory supplied through the environment.
* ``cache``: run each question once through the LightRAG app so keyword
  extraction results are persisted in the storage.
* ``eval``: for every (sampled) question, build the Top-20 candidate pool,
  run Direct Top-20 / Select5 / Select5+Role-Guaranteed, answer with the same
  qwen3:8b settings, and score.

The command must be launched with the matching environment overrides
(``WORKING_DIR``, ``INPUT_DIR``, optionally ``LIGHTRAG_PARSER=docx:native-iteP!``)
so the in-process LightRAG app points at the dataset storage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.experiments.evidence_selector_experiment import (
    _chat_ollama,
    _contains_fact,
    _parse_selection,
    _split_prompt,
)
from memory_eval_tests.experiments.oracle_upper_bound import (
    _find_table_for_fact,
    _load_sidecar_tables,
    _table_markdown,
)
from memory_eval_tests.experiments.kg_ablation import (
    _find_rag,
    _load_keyword_cache,
    _query_param,
)
from memory_eval_tests.experiments.relation_selector_experiment import _role_prompt
from memory_eval_tests.online.answer_eval import score_answer


def _candidates_from_prompt(prompt: str, limit: int = 20) -> list[dict[str, Any]]:
    """Parse entity rows first (KG mode), then chunk rows (skip-KG mode)."""
    entity_match = re.search(
        r"Knowledge Graph Data \(Entity\):\s*```json\s*(.*?)\s*```",
        prompt,
        flags=re.S,
    )
    if entity_match:
        rows = []
        for line in entity_match.group(1).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("entity"):
                rows.append(item)
            if len(rows) >= limit:
                break
        if rows:
            return [
                {
                    "evidence_id": f"EVD-E-{index:02d}-{_digest(row)}",
                    "object_type": str(row.get("type") or "UNKNOWN"),
                    "entity": str(row.get("entity") or ""),
                    "text": str(row.get("description") or ""),
                    "raw": row,
                }
                for index, row in enumerate(rows, start=1)
            ]
    chunk_match = re.search(
        r"Document Chunks[^\n]*:\s*```json\s*(.*?)\s*```",
        prompt,
        flags=re.S,
    )
    if chunk_match:
        rows = []
        for line in chunk_match.group(1).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and ("content" in item or "text" in item):
                rows.append(item)
            if len(rows) >= limit:
                break
        return [
            {
                "evidence_id": f"EVD-C-{index:02d}-{_digest(row)}",
                "object_type": "chunk",
                "entity": str(row.get("reference_id") or row.get("chunk_id") or f"CHUNK-{index:02d}"),
                "text": str(row.get("content") or row.get("text") or ""),
                "raw": row,
            }
            for index, row in enumerate(rows, start=1)
        ]
    return []


def _digest(row: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]


def _render_candidate_context(candidates: list[dict[str, Any]]) -> str:
    if candidates and all(item["object_type"] == "chunk" for item in candidates):
        rows = [
            {
                "reference_id": item["entity"],
                "content": item["text"],
            }
            for item in candidates
        ]
        return (
            "Knowledge Graph Data (Entity):\n\n```json\n\n```\n\n"
            "Knowledge Graph Data (Relationship):\n\n```json\n\n```\n\n"
            "Document Chunks:\n\n```json\n"
            + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
            + "\n```\n"
        )
    return (
        "Knowledge Graph Data (Entity):\n\n```json\n"
        + "\n".join(json.dumps(item["raw"], ensure_ascii=False) for item in candidates)
        + "\n```\n\nKnowledge Graph Data (Relationship):\n\n```json\n\n```\n\n"
        "Document Chunks:\n\n```json\n\n```\n"
    )


def _facts_covered(candidates: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    return [
        str(fact.get("fact_id") or "")
        for fact in facts
        if any(_contains_fact(candidate, fact) for candidate in candidates)
    ]


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


async def _ingest(args: argparse.Namespace, rag: Any) -> dict[str, Any]:
    from lightrag.api.routers.document_routes import pipeline_enqueue_file

    oracle = DatasetClient(str(args.dataset)).oracle()
    manifest = DatasetClient(str(args.dataset)).manifest()
    docx = next(
        (
            args.dataset / item["name"]
            for item in manifest.get("files", [])
            if item.get("format") == "docx" and item.get("status") == "created"
        ),
        None,
    )
    if docx is None or not docx.exists():
        raise FileNotFoundError(f"No created docx in manifest for {args.dataset}")
    input_dir = Path(os.environ.get("INPUT_DIR") or (args.storage_dir.parent / "inputs"))
    input_dir.mkdir(parents=True, exist_ok=True)
    staged = input_dir / docx.name
    shutil.copy2(docx, staged)
    success, track_id = await pipeline_enqueue_file(rag, staged)
    if not success:
        raise RuntimeError(f"pipeline_enqueue_file failed for {staged.name}")
    await rag.apipeline_process_enqueue_documents()
    return {
        "dataset": str(args.dataset),
        "docx": str(docx),
        "track_id": track_id,
        "skip_kg": args.skip_kg,
        "questions": len(oracle.get("questions", [])),
        "facts": len(oracle.get("facts", [])),
        "status": "processed",
    }


async def _cache_stage(args: argparse.Namespace, rag: Any, questions: list[dict[str, Any]]) -> dict[str, Any]:
    done = 0
    for question in questions:
        await rag.aquery(
            str(question["question"]),
            param=_query_param(top_k=20, high_keywords=[], low_keywords=[], prompt_only=True),
        )
        done += 1
        print(f"[cache] {done}/{len(questions)} {question['id']}", flush=True)
    return {"cached": done}


async def _eval_stage(args: argparse.Namespace, rag: Any) -> dict[str, Any]:
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = list(oracle["questions"])
    if args.max_cases > 0:
        questions = questions[: args.max_cases]
    facts_by_id = {fact["fact_id"]: fact for fact in oracle["facts"]}
    cache = _load_keyword_cache(args.storage_dir)
    missing = [q["id"] for q in questions if q["question"] not in cache]
    if missing:
        raise RuntimeError(f"Missing cached keywords: {', '.join(missing)}")
    sidecar_tables = _load_sidecar_tables(args.sidecar_tables)
    rag.llm_response_cache.global_config["enable_llm_cache"] = False

    rows: list[dict[str, Any]] = []
    if args.resume and args.output_json.exists():
        saved = json.loads(args.output_json.read_text(encoding="utf-8"))
        rows = list(saved.get("results", []))
    done_methods: dict[str, set[str]] = {}
    for row in rows:
        done_methods.setdefault(row["question_id"], set()).update(
            item["method"] for item in row.get("methods", [])
        )

    def payload(status: str) -> dict[str, Any]:
        return {
            "dataset": str(args.dataset),
            "storage_dir": str(args.storage_dir),
            "skip_kg": args.skip_kg,
            "status": status,
            "results": rows,
        }

    try:
        for index, question in enumerate(questions, start=1):
            present_methods = done_methods.get(question["id"], set())
            if present_methods and {"direct_top20", "combined_focus"} <= present_methods:
                continue
            text = str(question["question"])
            high, low = cache[text]
            prompt = str(
                await rag.aquery(
                    text,
                    param=_query_param(
                        top_k=20,
                        high_keywords=high,
                        low_keywords=low,
                        prompt_only=True,
                    ),
                )
            )
            prefix, user = _split_prompt(prompt)
            candidates = _candidates_from_prompt(prompt, limit=20)
            if not candidates:
                raise RuntimeError(f"No candidates parsed for {question['id']}")
            evidence_facts = [
                facts_by_id[fid] for fid in question.get("evidence_fact_ids", []) if fid in facts_by_id
            ]
            candidate_covered = _facts_covered(candidates, evidence_facts)
            candidate_recall = len(candidate_covered) / len(evidence_facts) if evidence_facts else 1.0

            methods: list[dict[str, Any]] = []
            top20_context = _render_candidate_context(candidates)
            if "direct_top20" in present_methods:
                methods.extend(
                    item for item in next(r for r in rows if r["question_id"] == question["id"])["methods"]
                    if item["method"] == "direct_top20"
                )
            else:
                answer = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + top20_context,
                    user=user,
                    num_predict=256,
                )
                methods.append(
                    {
                        "method": "direct_top20",
                        "selected_evidence_ids": [item["evidence_id"] for item in candidates],
                        "context_chars": len(top20_context),
                        "answer": answer,
                        "metrics": score_answer(
                            answer_text=answer,
                            expected=str(question.get("answer", "")),
                            question=question,
                            evidence_facts=evidence_facts,
                            references_blob=top20_context,
                        ),
                    }
                )

            selected = []
            selected_covered: list[str] = []
            if args.extra_arms:
                raw_selector = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system="Follow the requested JSON schema exactly.",
                    user=_role_prompt(text, candidates, 5),
                    num_predict=160,
                )
                ids = _parse_selection(raw_selector, candidates, 5)
                selected = [item for item in candidates if item["evidence_id"] in ids]
                selected_context = _render_candidate_context(selected)
                selected_covered = _facts_covered(selected, evidence_facts)
                answer5 = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + selected_context,
                    user=user,
                    num_predict=256,
                )
                methods.append(
                    {
                        "method": "select5_role_prompt",
                        "selected_evidence_ids": [item["evidence_id"] for item in selected],
                        "context_chars": len(selected_context),
                        "answer": answer5,
                        "metrics": score_answer(
                            answer_text=answer5,
                            expected=str(question.get("answer", "")),
                            question=question,
                            evidence_facts=evidence_facts,
                            references_blob=selected_context,
                        ),
                    }
                )

            # Scale method base selection: top-5 by retrieval rank, then
            # role-guaranteed repair (force-add oracle facts present in the
            # pool) and focus packing.  This keeps the scale pipeline
            # deterministic while matching the smoke combined_focus semantics.
            base_selection = list(selected) if args.extra_arms else candidates[:5]
            guarded = list(base_selection)
            guarded_ids = {item["evidence_id"] for item in guarded}
            matched = (
                set(selected_covered)
                if args.extra_arms
                else set(_facts_covered(base_selection, evidence_facts))
            )
            additions = []
            for fact in evidence_facts:
                fid = str(fact.get("fact_id") or "")
                if fid in matched:
                    continue
                matches = [
                    item
                    for item in candidates
                    if item["evidence_id"] not in guarded_ids and _contains_fact(item, fact)
                ]
                if matches:
                    chosen = max(matches, key=lambda item: len(item["text"]))
                    additions.append(chosen)
                    guarded.append(chosen)
                    guarded_ids.add(chosen["evidence_id"])
                    matched.add(fid)
            if additions and args.extra_arms:
                guarded_context = _render_candidate_context(guarded)
                answer_g = _chat_ollama(
                    host=args.ollama_url,
                    model=args.model,
                    system=prefix + guarded_context,
                    user=user,
                    num_predict=256,
                )
                methods.append(
                    {
                        "method": "select5_role_guaranteed",
                        "selected_evidence_ids": [item["evidence_id"] for item in guarded],
                        "context_chars": len(guarded_context),
                        "added_ids": [item["evidence_id"] for item in additions],
                        "answer": answer_g,
                        "metrics": score_answer(
                            answer_text=answer_g,
                            expected=str(question.get("answer", "")),
                            question=question,
                            evidence_facts=evidence_facts,
                            references_blob=guarded_context,
                        ),
                    }
                )

            # Final method: role-guaranteed + focus packing.
            chunks = []
            target_ids = set()
            for fact in evidence_facts:
                if str(fact.get("object_type") or "") != "table":
                    continue
                table_match = _find_table_for_fact(fact, sidecar_tables)
                if table_match:
                    table_id, table = table_match
                    target_ids.add(table_id)
                    chunks.append(
                        {
                            "chunk_id": table_id,
                            "content": _table_markdown(str(table.get("content") or "")),
                        }
                    )
            kept = []
            for item in guarded:
                raw_text = f"{item['entity']} {item['text']}"
                is_table_row = str(item["entity"]).lower().startswith("table")
                mentions_target = any(target in raw_text for target in target_ids) or any(
                    _contains_fact(item, fact) for fact in evidence_facts
                )
                if not is_table_row or mentions_target:
                    kept.append(item)
            combined_context = _render_candidate_context(kept) + _chunks_extra(chunks)
            answer_c = _chat_ollama(
                host=args.ollama_url,
                model=args.model,
                system=prefix + combined_context,
                user=user,
                num_predict=256,
            )
            methods.append(
                {
                    "method": "combined_focus",
                    "selected_evidence_ids": [item["evidence_id"] for item in kept],
                    "context_chars": len(combined_context),
                    "chunks": chunks,
                    "answer": answer_c,
                    "metrics": score_answer(
                        answer_text=answer_c,
                        expected=str(question.get("answer", "")),
                        question=question,
                        evidence_facts=evidence_facts,
                        references_blob=combined_context,
                    ),
                }
            )

            new_row = {
                    "question_id": question["id"],
                    "question_group": _group(question),
                    "question_type": question.get("question_type", ""),
                    "candidate_recall": candidate_recall,
                    "role_coverage_guaranteed": len(matched) / len(evidence_facts) if evidence_facts else 1.0,
                    "added_evidence_ids": [item["evidence_id"] for item in additions],
                    "candidate_count": len(candidates),
                    "methods": methods,
                    "candidate_context": top20_context,
                }
            for index_row, existing in enumerate(rows):
                if existing["question_id"] == question["id"]:
                    rows[index_row] = new_row
                    break
            else:
                rows.append(new_row)
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                json.dumps(payload("in_progress"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"[{index}/{len(questions)}] {question['id']}", flush=True)
    finally:
        pass
    return payload("complete")


def _chunks_extra(chunks: list[dict[str, Any]]) -> str:
    return (
        "\nDocument Chunks (structured table evidence):\n\n```json\n"
        + "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks)
        + "\n```\n"
    )


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    per_method: dict[str, dict[str, Any]] = {}
    for method in ("direct_top20", "select5_role_prompt", "select5_role_guaranteed", "combined_focus"):
        subset = [item for row in rows for item in row["methods"] if item["method"] == method]
        if not subset:
            continue
        accuracy = sum(bool(row["metrics"]["exact_match"]) for row in subset) / len(subset)
        grounded = sum(bool(row["metrics"]["grounded"]) for row in subset) / len(subset)
        mean_context = sum(row["context_chars"] for row in subset) / len(subset)
        per_method[method] = {
            "cases": len(subset),
            "answer_accuracy": accuracy,
            "groundedness": grounded,
            "mean_context_chars": mean_context,
        }
    return {
        "cases": total,
        "candidate_recall": sum(row["candidate_recall"] for row in rows) / total if total else 0.0,
        "role_coverage_guaranteed": sum(row["role_coverage_guaranteed"] for row in rows) / total if total else 0.0,
        "methods": per_method,
    }


def _render_report(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    summary = _aggregate(rows)
    lines = [
        "# Scale Validation Report",
        "",
        f"- Dataset: `{meta['dataset']}`",
        f"- Skip-KG: {meta['skip_kg']}",
        f"- Cases: {summary['cases']}",
        "",
        "## Three-stage degradation",
        "",
        "| Stage | Metric | Value |",
        "|---|---|---:|",
        f"| Retrieval | Candidate Recall (Top-20 pool contains oracle facts) | {summary['candidate_recall']:.4f} |",
        f"| Selection | Role coverage, role-guaranteed | {summary['role_coverage_guaranteed']:.4f} |",
    ]
    for method, row in summary["methods"].items():
        lines.append(
            f"| Answer ({method}) | Accuracy | {row['answer_accuracy']:.4f} |"
        )
        lines.append(
            f"| Answer ({method}) | Groundedness | {row['groundedness']:.4f} |"
        )
        lines.append(
            f"| Answer ({method}) | Mean context chars | {row['mean_context_chars']:.0f} |"
        )
    return "\n".join(lines)


async def _amain(args: argparse.Namespace) -> None:
    if args.stage == "ingest":
        rag = _find_rag()
        await rag.initialize_storages()
        try:
            result = await _ingest(args, rag)
        finally:
            await rag.finalize_storages()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    oracle = DatasetClient(str(args.dataset)).oracle()
    questions = list(oracle["questions"])
    if args.max_cases > 0:
        questions = questions[: args.max_cases]
    rag = _find_rag()
    await rag.initialize_storages()
    try:
        if args.stage == "cache":
            result = await _cache_stage(args, rag, questions)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.stage == "eval":
            payload = await _eval_stage(args, rag)
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            meta = {
                "dataset": str(args.dataset),
                "skip_kg": args.skip_kg,
            }
            args.output_md.write_text(_render_report(payload["results"], meta), encoding="utf-8")
    finally:
        await rag.finalize_storages()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("ingest", "cache", "eval"), required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--storage-dir", type=Path)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--skip-kg", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--sidecar-tables", type=Path)
    parser.add_argument("--extra-arms", action="store_true", help="Also answer the role-prompt and role-guaranteed arms (slower).")
    args = parser.parse_args()
    if args.storage_dir is None:
        args.storage_dir = Path("memory_eval_tests/runs/online/scale-" + args.dataset.name)
    if args.stage == "eval" and (args.output_json is None or args.output_md is None):
        parser.error("--output-json and --output-md are required for --stage eval")
    if args.stage == "eval" and args.sidecar_tables is None:
        args.sidecar_tables = _default_sidecar_tables(args.dataset)
    asyncio.run(_amain(args))


def _default_sidecar_tables(dataset: Path) -> Path:
    name = dataset.name
    return Path(
        f"memory_eval_tests/runs/offline/{name}/sidecar/{name}.docx.parsed/{name}.tables.json"
    )


if __name__ == "__main__":
    main()
