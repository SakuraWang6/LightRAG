"""Candidate/context helpers shared by the selector-family experiments."""

from __future__ import annotations

import json
import re
from typing import Any

from memory_eval_tests.experiments.evidence_selector_experiment import (
    _contains_fact,
    _entity_rows,
    _group,
    _make_candidates,
    _oracle_candidate_ids,
    _parse_selection,
    _render_context,
    _selector_prompt,
    _split_prompt,
)
from memory_eval_tests.experiments.relation_selector_experiment import _role_prompt
from memory_eval_tests.experiments.combined_pipeline_experiment import (
    _render_context as _render_combined_context,
    _target_tables,
)


def entity_rows(prompt: str, *, limit: int) -> list[dict[str, Any]]:
    return _entity_rows(prompt, limit=limit)


def make_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _make_candidates(rows)


def split_prompt(prompt: str) -> tuple[str, str]:
    return _split_prompt(prompt)


def render_context(candidates: list[dict[str, Any]]) -> str:
    return _render_context(candidates)


def render_combined_context(rows: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> str:
    return _render_combined_context(rows, chunks)


def contains_fact(candidate: dict[str, Any], fact: dict[str, Any]) -> bool:
    return _contains_fact(candidate, fact)


def oracle_candidate_ids(candidates: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    return _oracle_candidate_ids(candidates, facts)


def group(question: dict[str, Any]) -> str:
    return _group(question)


def selector_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
    return _selector_prompt(question, candidates, limit)


def role_prompt(question: str, candidates: list[dict[str, Any]], limit: int) -> str:
    return _role_prompt(question, candidates, limit)


def parse_selection(raw: str, candidates: list[dict[str, Any]], limit: int) -> list[str]:
    return _parse_selection(raw, candidates, limit)


def facts_covered(candidates: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    covered = []
    for fact in facts:
        if any(_contains_fact(candidate, fact) for candidate in candidates):
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
        if any(_contains_fact(item, fact) for item in selected)
    }
    additions: list[str] = []
    for fact in evidence_facts:
        fid = str(fact.get("fact_id") or "")
        if fid in matched_facts:
            continue
        matches = [
            item
            for item in candidates
            if item["evidence_id"] not in before and _contains_fact(item, fact)
        ]
        if matches:
            chosen = max(matches, key=lambda item: len(item["text"]))
            repaired.append(chosen)
            additions.append(chosen["evidence_id"])
            before.add(chosen["evidence_id"])
    return repaired, additions


def target_tables(
    evidence_facts: list[dict[str, Any]], tables: dict[str, dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    return _target_tables(evidence_facts, tables)
