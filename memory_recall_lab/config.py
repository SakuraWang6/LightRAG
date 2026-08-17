"""Experiment configuration for the recall-only lab.

The recall lab expresses *capabilities* in YAML and never switches behaviour
on experiment names.  A run is fully described by the git commit, the resolved
config (defaults + config file + CLI overrides) and the dataset fingerprint.
The runner translates the resolved config into ``LIGHTRAG_*`` environment
variables for the isolated LightRAG server.  Every capability switch has a
default that matches the current stable LightRAG behaviour, so an empty
config is exactly ``main``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

EXACT_ID_PREFIXES = ("FACT", "EQ", "REF", "TBL", "FIG")
VALID_RANKING_STRATEGIES = ("none", "structured")
VALID_TABLE_MODES = ("atomic", "fixed_token")
VALID_RUNTIME_MODES = ("naive", "local", "global", "hybrid", "mix")


class ConfigError(ValueError):
    """Raised when an experiment config is structurally or semantically invalid."""


@dataclass(frozen=True)
class ExperimentMeta:
    name: str = "a2_atomic_context"
    historical: bool = False
    legacy_mode: bool = False
    reproducible_from_current_code: bool = True
    git_commit: str | None = None


@dataclass(frozen=True)
class TableChunking:
    mode: str = "atomic"
    atomic: bool = True
    preceding_context: bool = True
    row_safe_split: bool = True
    sidecar_backfill: bool = True


@dataclass(frozen=True)
class Chunking:
    table: TableChunking = field(default_factory=TableChunking)


@dataclass(frozen=True)
class TableRepresentation:
    raw: bool = False
    table_view: bool = False
    row_view: bool = False
    structured_envelope: bool = False


@dataclass(frozen=True)
class Representation:
    table: TableRepresentation = field(default_factory=TableRepresentation)


@dataclass(frozen=True)
class DenseRetrieval:
    enabled: bool = True


@dataclass(frozen=True)
class ExactIdRetrieval:
    enabled: bool = True
    types: tuple[str, ...] = ("FACT", "EQ", "REF")


@dataclass(frozen=True)
class Retrieval:
    dense: DenseRetrieval = field(default_factory=DenseRetrieval)
    exact_id: ExactIdRetrieval = field(default_factory=ExactIdRetrieval)


@dataclass(frozen=True)
class Ranking:
    strategy: str = "none"
    lexical_overlap: bool = False


@dataclass(frozen=True)
class Runtime:
    mode: str = "naive"
    top_k: int = 20
    chunk_top_k: int = 20
    skip_kg: bool = True
    model: str = "qwen3:8b"
    engine: str = "native"
    max_cases: int = 0
    question_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evaluation:
    save_ranking: bool = True
    save_ranking_audit: bool = False


@dataclass(frozen=True)
class ExperimentConfig:
    experiment: ExperimentMeta = field(default_factory=ExperimentMeta)
    chunking: Chunking = field(default_factory=Chunking)
    representation: Representation = field(default_factory=Representation)
    retrieval: Retrieval = field(default_factory=Retrieval)
    ranking: Ranking = field(default_factory=Ranking)
    runtime: Runtime = field(default_factory=Runtime)
    evaluation: Evaluation = field(default_factory=Evaluation)
    config_file: str = ""


def _coerce_bool(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{path}: expected a boolean, got {value!r}")


def _coerce_str_list(
    value: Any, path: str, allowed: tuple[str, ...]
) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [item.strip().upper() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        values = [str(item).strip().upper() for item in value]
    else:
        raise ConfigError(f"{path}: expected a list of identifiers, got {value!r}")
    unknown = [item for item in values if item not in allowed] if allowed else []
    if unknown:
        raise ConfigError(
            f"{path}: unsupported identifier type(s): {', '.join(unknown)}"
        )
    seen: list[str] = []
    for item in values:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _require(value: bool, message: str) -> None:
    if not value:
        raise ConfigError(message)


def _build_dataclass(cls: type[Any], payload: dict[str, Any], path: str) -> Any:
    """Build one frozen dataclass from a raw YAML section, rejecting unknowns."""
    known = {f.name for f in fields(cls) if f.init}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown field(s): {', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, item in payload.items():
        annotation = getattr(cls, "__annotations__", {}).get(name)
        kwargs[name] = _build_value(annotation, item, f"{path}.{name}")
    return cls(**kwargs)


def _build_value(annotation: Any, value: Any, path: str) -> Any:
    if annotation is None:
        return value
    origin = getattr(annotation, "__origin__", None)
    if origin in {tuple, list}:
        return tuple(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _build_dataclass(annotation, value, path)
    if isinstance(annotation, type) and issubclass(annotation, bool):
        return _coerce_bool(value, path)
    if isinstance(annotation, type) and issubclass(annotation, int):
        return int(value)
    if isinstance(annotation, type) and issubclass(annotation, str):
        return str(value)
    return value


def _section(payload: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping, got {value!r}")
    return value


def _validate(cfg: ExperimentConfig) -> None:
    table = cfg.chunking.table
    rep = cfg.representation.table
    exact = cfg.retrieval.exact_id
    ranking = cfg.ranking
    runtime = cfg.runtime
    meta = cfg.experiment

    _require(
        table.mode in VALID_TABLE_MODES,
        f"chunking.table.mode must be one of {VALID_TABLE_MODES}",
    )
    if table.mode == "fixed_token":
        _require(
            meta.legacy_mode
            and meta.historical
            and not meta.reproducible_from_current_code,
            "chunking.table.mode=fixed_token is the legacy A0 behaviour that is not "
            "reproducible from the current chunker; set experiment.legacy_mode=true, "
            "experiment.historical=true and experiment.reproducible_from_current_code=false "
            "and reproduce it from the recorded git commit instead",
        )
    if table.mode != "fixed_token":
        _require(
            table.atomic,
            "chunking.table.atomic must stay true: the atomic table is a stable Evidence Layer capability",
        )
        _require(
            table.row_safe_split,
            "chunking.table.row_safe_split must stay true: row-safe long-table splitting is a stable capability",
        )
        _require(
            table.sidecar_backfill,
            "chunking.table.sidecar_backfill must stay true: sidecar correctness is a stable capability",
        )
    if rep.raw and rep.structured_envelope:
        raise ConfigError(
            "representation.table.raw and representation.table.structured_envelope are mutually exclusive"
        )
    if rep.table_view or rep.row_view:
        _require(
            table.atomic, "table views require the atomic table (chunking.table.atomic)"
        )

    if exact.enabled:
        _require(
            len(exact.types) > 0,
            "retrieval.exact_id.types must not be empty when exact_id is enabled",
        )
    else:
        _require(
            len(exact.types) == 0,
            "retrieval.exact_id.types must be empty when exact_id is disabled",
        )

    _require(
        ranking.strategy in VALID_RANKING_STRATEGIES,
        f"ranking.strategy must be one of {VALID_RANKING_STRATEGIES}",
    )
    if ranking.strategy == "structured":
        _require(
            exact.enabled,
            "ranking.strategy=structured requires retrieval.exact_id.enabled=true",
        )
        _require(
            rep.table_view or rep.row_view,
            "ranking.strategy=structured requires a table view or row view so candidate types can be tiered",
        )
    if ranking.lexical_overlap:
        _require(
            ranking.strategy == "structured",
            "ranking.lexical_overlap only applies to ranking.strategy=structured",
        )

    _require(
        runtime.mode in VALID_RUNTIME_MODES,
        f"runtime.mode must be one of {VALID_RUNTIME_MODES}",
    )
    if runtime.skip_kg:
        _require(
            runtime.mode == "naive", "runtime.skip_kg=true requires runtime.mode=naive"
        )
    _require(runtime.top_k > 0, "runtime.top_k must be positive")
    _require(runtime.chunk_top_k > 0, "runtime.chunk_top_k must be positive")


def _defaults_payload() -> dict[str, Any]:
    return {
        "experiment": {
            "name": "a2_atomic_context",
            "historical": False,
            "legacy_mode": False,
            "reproducible_from_current_code": True,
            "git_commit": None,
        },
        "chunking": {
            "table": {
                "mode": "atomic",
                "atomic": True,
                "preceding_context": True,
                "row_safe_split": True,
                "sidecar_backfill": True,
            }
        },
        "representation": {
            "table": {
                "raw": False,
                "table_view": False,
                "row_view": False,
                "structured_envelope": False,
            }
        },
        "retrieval": {
            "dense": {"enabled": True},
            "exact_id": {"enabled": True, "types": ["FACT", "EQ", "REF"]},
        },
        "ranking": {"strategy": "none", "lexical_overlap": False},
        "runtime": {
            "mode": "naive",
            "top_k": 20,
            "chunk_top_k": 20,
            "skip_kg": True,
            "model": "qwen3:8b",
            "engine": "native",
            "max_cases": 0,
            "question_types": [],
        },
        "evaluation": {"save_ranking": True, "save_ranking_audit": False},
    }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> ExperimentConfig:
    """Load, validate and resolve an experiment config.

    ``overrides`` follows the flat runtime keys (``top_k``, ``mode``, ...) and
    is applied after the YAML file, mirroring CLI overrides.
    """
    config_path = Path(path) if path is not None else None
    payload = _defaults_payload()
    if config_path is not None:
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{config_path}: top-level YAML must be a mapping")
        payload = _deep_merge(payload, raw)
    if overrides:
        payload = _deep_merge(payload, {"runtime": overrides})

    experiment = _build_dataclass(
        ExperimentMeta, _section(payload, "experiment", "experiment"), "experiment"
    )
    chunking_section = _section(payload, "chunking", "chunking")
    table_raw = chunking_section.get("table", {})
    if not isinstance(table_raw, dict):
        raise ConfigError("chunking.table: expected a mapping")
    chunking = Chunking(
        table=_build_dataclass(TableChunking, table_raw, "chunking.table")
    )
    representation_section = _section(payload, "representation", "representation")
    rep_raw = representation_section.get("table", {})
    if not isinstance(rep_raw, dict):
        raise ConfigError("representation.table: expected a mapping")
    representation = Representation(
        table=_build_dataclass(TableRepresentation, rep_raw, "representation.table")
    )
    retrieval_payload = _section(payload, "retrieval", "retrieval")
    dense_raw = retrieval_payload.get("dense", {})
    if not isinstance(dense_raw, dict):
        raise ConfigError("retrieval.dense: expected a mapping")
    dense = _build_dataclass(DenseRetrieval, dense_raw, "retrieval.dense")
    exact_raw = retrieval_payload.get("exact_id", {})
    if not isinstance(exact_raw, dict):
        raise ConfigError("retrieval.exact_id: expected a mapping")
    exact_types = _coerce_str_list(
        exact_raw.get("types", ["FACT", "EQ", "REF"]),
        "retrieval.exact_id.types",
        EXACT_ID_PREFIXES,
    )
    exact = ExactIdRetrieval(
        enabled=_coerce_bool(
            exact_raw.get("enabled", True), "retrieval.exact_id.enabled"
        ),
        types=exact_types,
    )
    ranking = _build_dataclass(
        Ranking, _section(payload, "ranking", "ranking"), "ranking"
    )
    runtime_payload = _section(payload, "runtime", "runtime")
    question_raw = runtime_payload.get("question_types", [])
    question_types = (
        _coerce_str_list(question_raw, "runtime.question_types", tuple())
        if question_raw
        else tuple()
    )
    runtime = Runtime(
        mode=str(runtime_payload.get("mode", "naive")),
        top_k=int(runtime_payload.get("top_k", 20)),
        chunk_top_k=int(runtime_payload.get("chunk_top_k", 20)),
        skip_kg=_coerce_bool(runtime_payload.get("skip_kg", True), "runtime.skip_kg"),
        model=str(runtime_payload.get("model", "qwen3:8b")),
        engine=str(runtime_payload.get("engine", "native")),
        max_cases=int(runtime_payload.get("max_cases", 0)),
        question_types=question_types,
    )
    evaluation = _build_dataclass(
        Evaluation,
        _section(payload, "evaluation", "evaluation"),
        "evaluation",
    )
    cfg = ExperimentConfig(
        experiment=experiment,
        chunking=chunking,
        representation=representation,
        retrieval=Retrieval(dense=dense, exact_id=exact),
        ranking=ranking,
        runtime=runtime,
        evaluation=evaluation,
        config_file=str(config_path) if config_path is not None else "",
    )
    _validate(cfg)
    return cfg


def resolved_to_dict(cfg: ExperimentConfig) -> dict[str, Any]:
    """Serialize a resolved config into a plain nested dict for run metadata."""

    def convert(value: Any) -> Any:
        if is_dataclass(value):
            return {
                f.name: convert(getattr(value, f.name))
                for f in fields(value)
                if f.name != "config_file"
            }
        if isinstance(value, tuple):
            return list(value)
        return value

    return convert(cfg)


def resolved_to_yaml(cfg: ExperimentConfig) -> str:
    return yaml.safe_dump(
        resolved_to_dict(cfg),
        allow_unicode=True,
        sort_keys=False,
    )


def apply_to_environment(cfg: ExperimentConfig) -> dict[str, str]:
    """Translate resolved capability switches into child-server env vars."""
    table = cfg.representation.table
    exact = cfg.retrieval.exact_id
    return {
        "LIGHTRAG_TABLE_PRECEDING_CONTEXT": "1"
        if cfg.chunking.table.preceding_context
        else "0",
        "LIGHTRAG_TABLE_STRUCTURED_ENVELOPE": "1" if table.structured_envelope else "0",
        "LIGHTRAG_TABLE_VIEW": "1" if table.table_view else "0",
        "LIGHTRAG_TABLE_ROW_VIEW": "1" if table.row_view else "0",
        "LIGHTRAG_EXACT_ID_TYPES": ",".join(exact.types) if exact.enabled else "",
        "LIGHTRAG_RANKING_STRATEGY": cfg.ranking.strategy,
    }


def apply_environment(cfg: ExperimentConfig) -> None:
    os.environ.update(apply_to_environment(cfg))


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "configs" / "a2_atomic_context.yaml"
