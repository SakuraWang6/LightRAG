"""Typed fair-comparison plan templates and deterministic dependency rules."""

from __future__ import annotations

from typing import Any, Literal

ComparisonType = Literal[
    "answer_model", "retrieval_configuration", "embedding", "full_pipeline"
]

_TEMPLATES: dict[str, dict[str, Any]] = {
    "answer_model": {
        "label": "回答模型比较",
        "allowed_variables": {"answer_model", "answer_prompt", "temperature", "num_predict", "seed"},
        "required_inputs": {"frozen_context_run_id"},
        "index_requirement": "reuse_frozen_final_context",
        "dependencies": ["freeze_final_context"],
    },
    "retrieval_configuration": {
        "label": "检索配置比较",
        "allowed_variables": {"retrieval_mode", "top_k", "chunk_top_k", "reranker", "max_total_tokens"},
        "required_inputs": {"source_run_id"},
        "index_requirement": "reuse_same_index",
        "dependencies": ["verify_source_index"],
    },
    "embedding": {
        "label": "Embedding 比较",
        "allowed_variables": {"embedding_model", "embedding_dimensions", "embedding_batch_size"},
        "required_inputs": {"environment_profile_id", "environment_profile_version"},
        "index_requirement": "rebuild_isolated_index_per_arm",
        "dependencies": ["allocate_isolated_workspace_per_arm", "ingest_and_index_per_arm"],
    },
    "full_pipeline": {
        "label": "完整链路比较",
        "allowed_variables": {
            "parser_engine", "extraction_model", "embedding_model", "query_model", "answer_model",
            "retrieval_mode", "reranker", "answer_prompt", "temperature", "num_predict",
        },
        "required_inputs": {"environment_profile_id", "environment_profile_version"},
        "index_requirement": "rebuild_isolated_pipeline_per_arm",
        "dependencies": ["allocate_isolated_workspace_per_arm", "ingest_and_index_per_arm"],
    },
}


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "type": name,
            "label": spec["label"],
            "allowed_variables": sorted(spec["allowed_variables"]),
            "required_inputs": sorted(spec["required_inputs"]),
            "index_requirement": spec["index_requirement"],
            "dependencies": list(spec["dependencies"]),
        }
        for name, spec in _TEMPLATES.items()
    ]


def validate_plan(
    *, comparison_type: str, variables: dict[str, list[Any]], inputs: dict[str, Any]
) -> dict[str, Any]:
    """Validate arms and calculate their non-negotiable execution dependencies."""
    spec = _TEMPLATES.get(comparison_type)
    if spec is None:
        raise ValueError(f"unknown comparison type: {comparison_type}")
    if not isinstance(variables, dict) or not variables:
        raise ValueError("comparison variables must be a non-empty object")
    unknown = sorted(set(variables) - spec["allowed_variables"])
    if unknown:
        raise ValueError(
            f"{comparison_type} does not allow variables: {', '.join(unknown)}"
        )
    invalid = [
        key for key, values in variables.items()
        if not isinstance(values, list) or len({str(value) for value in values}) < 2
    ]
    if invalid:
        raise ValueError(
            "each comparison variable needs at least two distinct values: " + ", ".join(sorted(invalid))
        )
    missing = sorted(
        key for key in spec["required_inputs"] if inputs.get(key) in (None, "")
    )
    if missing:
        raise ValueError(
            f"{comparison_type} requires inputs: {', '.join(missing)}"
        )
    arm_count = 1
    for values in variables.values():
        arm_count *= len(values)
    if arm_count > 16:
        raise ValueError("comparison plan exceeds the 16-arm safety limit")
    return {
        "comparison_type": comparison_type,
        "allowed_variables": sorted(spec["allowed_variables"]),
        "variables": variables,
        "inputs": inputs,
        "arm_count": arm_count,
        "index_requirement": spec["index_requirement"],
        "execution_dependencies": list(spec["dependencies"]),
        "reuse_permitted": comparison_type in {"answer_model", "retrieval_configuration"},
    }
