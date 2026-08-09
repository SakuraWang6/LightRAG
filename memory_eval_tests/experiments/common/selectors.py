"""Shared candidate/prompt helpers for the selector-family experiments.

These functions were historically private to the legacy selector scripts
(evidence_selector, relation_selector, combined_pipeline).  Moving them here
removes cross-module private imports while keeping one implementation for both
the legacy runners and the registered harness.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from typing import Any

from memory_eval_tests.experiments.common.tables import find_table_for_fact


def simple_chat_ollama(
    *, host: str, model: str, system: str, user: str, num_predict: int
) -> str:
    """Non-streaming Ollama chat used by selector experiments (fixed 16K ctx)."""
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(
            {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {
                    "temperature": 0,
                    "num_ctx": 16384,
                    "num_predict": num_predict,
                },
                "think": False,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str((body.get("message") or {}).get("content") or "")


def split_prompt(prompt: str) -> tuple[str, str]:
    marker = "\n\n---User Query---\n"
    if marker not in prompt:
        raise ValueError("LightRAG prompt is missing the user-query marker")
    system, question = prompt.split(marker, 1)
    context_marker = "---Context---\n"
    if context_marker not in system:
        raise ValueError("LightRAG prompt is missing the context marker")
    prefix = system.split(context_marker, 1)[0]
    prefix += (
        "For this controlled evaluation, answer concisely in no more than three sentences "
        "before the required references section.\n\n"
    )
    return prefix + context_marker + "\n", question


def entity_rows_from_context(context: str, limit: int = 20) -> list[dict[str, Any]]:
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


def entity_rows(prompt: str, *, limit: int) -> list[dict[str, Any]]:
    return entity_rows_from_context(prompt, limit=limit)


def make_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for ordinal, row in enumerate(rows, start=1):
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        candidates.append(
            {
                "evidence_id": f"EVD-E-{ordinal:02d}-{digest}",
                "object_type": str(row.get("type") or "UNKNOWN"),
                "entity": str(row.get("entity") or ""),
                "text": str(row.get("description") or ""),
                "raw": row,
            }
        )
    return candidates


def render_context(candidates: list[dict[str, Any]]) -> str:
    rows = [item["raw"] for item in candidates]
    return (
        "Knowledge Graph Data (Entity):\n\n```json\n"
        + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        + "\n```\n\nKnowledge Graph Data (Relationship):\n\n```json\n\n```\n\nDocument Chunks:\n\n```json\n\n```\n"
    )


def render_combined_context(
    rows: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> str:
    entity = (
        "Knowledge Graph Data (Entity):\n\n```json\n"
        + "\n".join(json.dumps(row["raw"], ensure_ascii=False) for row in rows)
        + "\n```\n"
    )
    relationships = "Knowledge Graph Data (Relationship):\n\n```json\n\n```\n"
    documents = (
        "Document Chunks:\n\n```json\n"
        + "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks)
        + "\n```\n"
    )
    return entity + relationships + documents


def contains_fact(candidate: dict[str, Any], fact: dict[str, Any]) -> bool:
    text = f"{candidate['entity']} {candidate['text']}".lower()
    markers = [
        str(fact.get("fact_id") or ""),
        str(fact.get("answer") or ""),
    ]
    return any(marker and marker.lower() in text for marker in markers)


def oracle_candidate_ids(
    candidates: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[str]:
    return [
        item["evidence_id"]
        for item in candidates
        if any(contains_fact(item, fact) for fact in facts)
    ]


def selector_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
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
        "You are an evidence selector. Do not answer the question. Select at most "
        f"{limit} evidence IDs that are sufficient and directly relevant. Prefer authoritative facts "
        "over distractors. Return ONLY strict JSON with this shape: "
        '{"selected_evidence_ids":["EVD-..."]}.\n\n'
        f"Question: {question}\n\nCandidates:\n{json.dumps(rendered, ensure_ascii=False)}"
    )


def role_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
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


def parse_selection(
    raw: str, candidates: list[dict[str, Any]], limit: int
) -> list[str]:
    candidate_ids = {item["evidence_id"] for item in candidates}
    match = re.search(r"\{.*?\}", raw, flags=re.S)
    if match:
        try:
            parsed = json.loads(match.group(0))
            selected = parsed.get("selected_evidence_ids", [])
            if isinstance(selected, list):
                valid = [str(item) for item in selected if str(item) in candidate_ids]
                if valid:
                    return list(dict.fromkeys(valid))[:limit]
        except json.JSONDecodeError:
            pass
    # The fallback is only for malformed selector output and is recorded in raw
    # output. It preserves a runnable/reviewable experiment rather than silently
    # giving the answer model all candidates.
    return [item["evidence_id"] for item in candidates[:limit]]


def group(question: dict[str, Any]) -> str:
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


def target_tables(
    evidence_facts: list[dict[str, Any]], tables: dict[str, dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for fact in evidence_facts:
        if str(fact.get("object_type") or "") != "table":
            continue
        matched = find_table_for_fact(fact, tables)
        if matched:
            result.append(matched)
    return result


def facts_covered(
    candidates: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[str]:
    covered = []
    for fact in facts:
        if any(contains_fact(candidate, fact) for candidate in candidates):
            covered.append(str(fact.get("fact_id") or ""))
    return covered


def role_guaranteed_repair(
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    evidence_facts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Deterministically add the best matching candidate for every uncovered fact."""
    repaired = list(selected)
    before = {item["evidence_id"] for item in selected}
    matched_facts = {
        str(fact["fact_id"])
        for fact in evidence_facts
        if any(contains_fact(item, fact) for item in selected)
    }
    additions: list[str] = []
    for fact in evidence_facts:
        fid = str(fact.get("fact_id") or "")
        if fid in matched_facts:
            continue
        matches = [
            item
            for item in candidates
            if item["evidence_id"] not in before and contains_fact(item, fact)
        ]
        if matches:
            chosen = max(matches, key=lambda item: len(item["text"]))
            repaired.append(chosen)
            additions.append(chosen["evidence_id"])
            before.add(chosen["evidence_id"])
    return repaired, additions
