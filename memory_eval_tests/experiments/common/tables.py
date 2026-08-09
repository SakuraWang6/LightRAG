"""Sidecar table helpers (generic dataset-aware loaders).

The oracle-evidence packing helpers were previously private to the legacy
``oracle_upper_bound`` script; they live here so registered and legacy
experiments share one implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sidecar_tables(parsed_dir_or_file: Path) -> dict[str, dict[str, Any]]:
    """Load the first ``*.tables.json`` under a parsed dir, or a direct file."""
    target: Path = parsed_dir_or_file
    if target.is_dir():
        candidates = sorted(target.glob("*.tables.json"))
        if not candidates:
            return {}
        target = candidates[0]
    payload = json.loads(target.read_text(encoding="utf-8"))
    return dict(payload.get("tables") or {})


def table_markdown(content: str) -> str:
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


def find_table_for_fact(
    fact: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    fid = str(fact.get("fact_id") or "")
    for table_id, table in tables.items():
        content = str(table.get("content") or "")
        if fid in content:
            return table_id, table
    return None


def _fact_text(
    fact: dict[str, Any],
    supports: dict[str, str],
    objects: dict[str, dict[str, Any]],
) -> str:
    fid = str(fact.get("fact_id") or "")
    if supports.get(fid):
        return supports[fid]
    if fact.get("expected_text"):
        return str(fact["expected_text"])
    obj = objects.get(str(fact.get("object_id_hint") or ""), {})
    return str(obj.get("text") or "")


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


def build_oracle_context(
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
            matched = find_table_for_fact(fact, tables)
            if matched:
                table_id, table = matched
                chunk_rows.append(
                    {
                        "chunk_id": str(table.get("id") or table_id),
                        "content": table_markdown(str(table.get("content") or "")),
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
            str(fact.get("object_id_hint") or "")
            for fact in facts
            if fact.get("object_id_hint")
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
