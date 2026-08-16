"""Tests for the recall-lab config loader, validation and env mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_recall_lab.config import (
    ConfigError,
    apply_to_environment,
    load_config,
    resolved_to_dict,
    resolved_to_yaml,
)


CONFIGS = Path(__file__).resolve().parents[2] / "memory_recall_lab" / "configs"


def test_load_valid_a2_config() -> None:
    cfg = load_config(CONFIGS / "a2_atomic_context.yaml")
    assert cfg.experiment.name == "a2_atomic_context"
    assert cfg.chunking.table.mode == "atomic"
    assert cfg.chunking.table.preceding_context is True
    assert cfg.representation.table.table_view is False
    assert cfg.retrieval.exact_id.types == ("FACT", "EQ", "REF")
    assert cfg.ranking.strategy == "none"
    assert cfg.runtime.top_k == 20


def test_defaults_match_a2() -> None:
    default = load_config(None)
    a2 = load_config(CONFIGS / "a2_atomic_context.yaml")
    assert resolved_to_dict(default) == resolved_to_dict(a2)


def test_cli_overrides_are_applied() -> None:
    cfg = load_config(
        CONFIGS / "a2_atomic_context.yaml",
        overrides={"top_k": 10, "chunk_top_k": 5, "mode": "naive", "skip_kg": True},
    )
    assert cfg.runtime.top_k == 10
    assert cfg.runtime.chunk_top_k == 5
    resolved = resolved_to_dict(cfg)
    assert resolved["runtime"]["top_k"] == 10
    assert "top_k: 10" in resolved_to_yaml(cfg)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("chunking:\n  table:\n    atomic: true\n    mystery: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown field"):
        load_config(path)


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("chunking: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_row_view_requires_atomic_table(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "chunking:\n  table:\n    atomic: false\n"
        "representation:\n  table:\n    table_view: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="atomic"):
        load_config(path)


def test_exact_id_enabled_requires_types(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "retrieval:\n  exact_id:\n    enabled: true\n    types: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must not be empty"):
        load_config(path)


def test_unknown_exact_id_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "retrieval:\n  exact_id:\n    types: [FACT, NOPE]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unsupported identifier"):
        load_config(path)


def test_structured_ranking_requires_views_and_exact_id(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("ranking:\n  strategy: structured\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="structured"):
        load_config(path)


def test_fixed_token_requires_legacy_meta(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("chunking:\n  table:\n    mode: fixed_token\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="legacy A0"):
        load_config(path)


def test_skip_kg_requires_naive_mode(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("runtime:\n  mode: local\n  skip_kg: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="requires runtime.mode=naive"):
        load_config(path)


def test_apply_to_environment_maps_capabilities() -> None:
    cfg = load_config(CONFIGS / "a2_atomic_context.yaml")
    env = apply_to_environment(cfg)
    assert env["LIGHTRAG_TABLE_VIEW"] == "0"
    assert env["LIGHTRAG_TABLE_ROW_VIEW"] == "0"
    assert env["LIGHTRAG_EXACT_ID_TYPES"] == "FACT,EQ,REF"
    assert env["LIGHTRAG_RANKING_STRATEGY"] == "none"
    assert env["LIGHTRAG_TABLE_PRECEDING_CONTEXT"] == "1"
