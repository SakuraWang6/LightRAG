"""Candidate/context helpers shared by the selector-family experiments.

Implementations live in :mod:`common.selectors`; this module keeps the stable
public names used by the registered experiments.
"""

from __future__ import annotations

from memory_eval_tests.experiments.common.selectors import (
    contains_fact,
    entity_rows,
    facts_covered,
    group,
    make_candidates,
    oracle_candidate_ids,
    parse_selection,
    render_combined_context,
    render_context,
    role_guaranteed_repair,
    role_prompt,
    selector_prompt,
    split_prompt,
    target_tables,
)

__all__ = [
    "contains_fact",
    "entity_rows",
    "facts_covered",
    "group",
    "make_candidates",
    "oracle_candidate_ids",
    "parse_selection",
    "render_combined_context",
    "render_context",
    "role_guaranteed_repair",
    "role_prompt",
    "selector_prompt",
    "split_prompt",
    "target_tables",
]
