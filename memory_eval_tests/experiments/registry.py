"""Experiment registry: id -> ExperimentSpec."""

from __future__ import annotations

from importlib import import_module

from memory_eval_tests.experiments.common import ExperimentSpec


_SPEC_MODULES = {
    "context_selection": "context_selection",
    "context_size": "context_size",
    "custom_arms": "custom_arms",
    "structure_ablation": "structure_ablation",
    "scale": "scale",
    "end_to_end_baseline": "end_to_end_baseline",
    "online_baseline": "online_baseline",
    "kg_ablation": "kg_ablation",
    "evidence_selector": "evidence_selector_experiment",
    "relation_selector": "relation_selector_experiment",
    "table_packing": "table_packing_experiment",
    "combined_pipeline": "combined_pipeline_experiment",
    "oracle_upper_bound": "oracle_upper_bound",
    "frozen_prompt_llm_eval": "frozen_prompt_llm_eval",
    "evaluator_recheck": "evaluator_recheck",
    "evidence_selector_failure_analysis": "evidence_selector_failure_analysis",
}


def _load_spec(experiment_id: str) -> ExperimentSpec:
    module_name = _SPEC_MODULES.get(experiment_id)
    if module_name is None:
        known = ", ".join(sorted(_SPEC_MODULES))
        raise ValueError(f"Unknown experiment '{experiment_id}'. Known: {known}")
    module = import_module(f"memory_eval_tests.experiments.{module_name}")
    spec = getattr(module, "spec", None)
    if not isinstance(spec, ExperimentSpec):
        raise TypeError(f"experiment module {module_name!r} does not export an ExperimentSpec")
    return spec


def _specs() -> list[ExperimentSpec]:
    """Load research specs only for callers that explicitly list them."""
    return [_load_spec(experiment_id) for experiment_id in _SPEC_MODULES]


def get_spec(experiment_id: str) -> ExperimentSpec:
    return _load_spec(experiment_id)


def list_specs() -> list[ExperimentSpec]:
    return _specs()
