"""Experiment registry: id -> ExperimentSpec."""

from __future__ import annotations

from memory_eval_tests.experiments.common import ExperimentSpec


def _specs() -> list[ExperimentSpec]:
    # Lazy imports so a single broken experiment never blocks the registry.
    from memory_eval_tests.experiments.context_selection import spec as context_selection
    from memory_eval_tests.experiments.context_size import spec as context_size
    from memory_eval_tests.experiments.structure_ablation import spec as structure_ablation
    from memory_eval_tests.experiments.scale import spec as scale
    from memory_eval_tests.experiments.online_baseline import spec as online_baseline

    return [context_selection, context_size, structure_ablation, scale, online_baseline]


def get_spec(experiment_id: str) -> ExperimentSpec:
    for spec in _specs():
        if spec.id == experiment_id:
            return spec
    known = ", ".join(spec.id for spec in _specs())
    raise ValueError(f"Unknown experiment '{experiment_id}'. Known: {known}")


def list_specs() -> list[ExperimentSpec]:
    return _specs()
