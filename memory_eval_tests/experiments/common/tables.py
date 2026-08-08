"""Sidecar table helpers (generic dataset-aware loaders)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memory_eval_tests.experiments.oracle_upper_bound import (
    _build_oracle_context,
    _find_table_for_fact,
    _load_sidecar_tables,
    _table_markdown,
)


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
    return _table_markdown(content)


def find_table_for_fact(
    fact: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    return _find_table_for_fact(fact, tables)


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
    return _build_oracle_context(
        question=question,
        facts=facts,
        objects=objects,
        supports=supports,
        relations=relations,
        tables=tables,
        arm=arm,
    )
