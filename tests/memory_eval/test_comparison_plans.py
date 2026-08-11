"""Compatibility contract tests for the product comparison screen."""

from __future__ import annotations

import pytest

from lightrag.api.eval_comparison import compare_contract

pytestmark = pytest.mark.offline


def test_contract_blocks_ranking_when_dataset_fingerprints_differ() -> None:
    run = {"execution_manifest": {"dataset": {"manifest_sha256": "a"}}, "experiment": {"id": "x"}}
    other = {"execution_manifest": {"dataset": {"manifest_sha256": "b"}}, "experiment": {"id": "x"}}
    result = compare_contract([run, other])
    assert result["ranking_permitted"] is False
    assert "dataset_fingerprint" in result["incompatible_fields"]


def test_contract_blocks_dynamic_profiles_with_different_effective_configurations() -> None:
    base = {
        "execution_manifest": {
            "dataset": {"manifest_sha256": "a"},
            "execution_unit": {
                "profile": {"id": "server-default", "version": 1},
                "configuration_fingerprint": "one",
            },
        },
        "experiment": {"id": "end_to_end_baseline"},
    }
    other = {
        **base,
        "execution_manifest": {
            **base["execution_manifest"],
            "execution_unit": {
                **base["execution_manifest"]["execution_unit"],
                "configuration_fingerprint": "two",
            },
        },
    }
    result = compare_contract([base, other])
    assert result["ranking_permitted"] is False
    assert "environment_configuration" in result["incompatible_fields"]
