"""Experiment registry: id -> ExperimentSpec."""

from __future__ import annotations

from memory_eval_tests.experiments.common import ExperimentSpec


def _specs() -> list[ExperimentSpec]:
    # Lazy imports so a single broken experiment never blocks the registry.
    from memory_eval_tests.experiments.combined_pipeline_experiment import (
        spec as combined_pipeline,
    )
    from memory_eval_tests.experiments.context_selection import (
        spec as context_selection,
    )
    from memory_eval_tests.experiments.context_size import spec as context_size
    from memory_eval_tests.experiments.custom_arms import spec as custom_arms
    from memory_eval_tests.experiments.evaluator_recheck import (
        spec as evaluator_recheck,
    )
    from memory_eval_tests.experiments.evidence_selector_experiment import (
        spec as evidence_selector,
    )
    from memory_eval_tests.experiments.evidence_selector_failure_analysis import (
        spec as evidence_selector_failure_analysis,
    )
    from memory_eval_tests.experiments.frozen_prompt_llm_eval import (
        spec as frozen_prompt_llm_eval,
    )
    from memory_eval_tests.experiments.kg_ablation import spec as kg_ablation
    from memory_eval_tests.experiments.online_baseline import spec as online_baseline
    from memory_eval_tests.experiments.oracle_upper_bound import (
        spec as oracle_upper_bound,
    )
    from memory_eval_tests.experiments.relation_selector_experiment import (
        spec as relation_selector,
    )
    from memory_eval_tests.experiments.scale import spec as scale
    from memory_eval_tests.experiments.structure_ablation import (
        spec as structure_ablation,
    )
    from memory_eval_tests.experiments.table_packing_experiment import (
        spec as table_packing,
    )

    return [
        context_selection,
        context_size,
        custom_arms,
        structure_ablation,
        scale,
        online_baseline,
        kg_ablation,
        evidence_selector,
        relation_selector,
        table_packing,
        combined_pipeline,
        oracle_upper_bound,
        frozen_prompt_llm_eval,
        evaluator_recheck,
        evidence_selector_failure_analysis,
    ]


def get_spec(experiment_id: str) -> ExperimentSpec:
    for spec in _specs():
        if spec.id == experiment_id:
            return spec
    known = ", ".join(spec.id for spec in _specs())
    raise ValueError(f"Unknown experiment '{experiment_id}'. Known: {known}")


def list_specs() -> list[ExperimentSpec]:
    return _specs()
