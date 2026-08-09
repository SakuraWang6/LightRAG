"""Standard run envelope, progress file, and unified condition handling."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "1.0"

BASELINE_DEFAULTS: dict[str, Any] = {
    "mode": "mix",
    "top_k": 5,
    "chunk_top_k": 5,
    "model": "qwen3:8b",
    "vlm_model": "gemma3:4b",
    "num_ctx": 16384,
    "num_predict": 128,
    "temperature": 0,
    "kg": True,
    "vlm": False,
    "engine": "native",
    "max_cases": 0,
}

# Larger-context arms (Top-20 candidate pools) default to a 32K window.
WIDE_CONTEXT_ARMS = {
    "direct_top20",
    "select3",
    "select5",
    "role_select5",
    "combined_focus",
    "combined_precision",
    "oracle_text",
    "oracle_full",
}

_CONDITION_LABELS = {
    "dataset": "数据集",
    "pages": "文档页数",
    "tier": "规模档",
    "profile": "生成档案",
    "formats": "格式",
    "engine": "解析引擎",
    "model": "生成模型",
    "vlm_model": "VLM",
    "mode": "检索模式",
    "top_k": "Top-K",
    "chunk_top_k": "Chunk Top-K",
    "num_ctx": "上下文窗口",
    "num_predict": "最大输出",
    "temperature": "温度",
    "kg": "KG",
    "vlm": "VLM 抽取",
    "rag_api_url": "RAG API",
    "ollama_url": "Ollama",
    "storage_dir": "存储目录",
    "embedding_model": "Embedding",
    "methods": "方法数",
}


@dataclass
class ExperimentSpec:
    id: str
    label: str
    description: str
    runner: Callable[["RunContext"], dict[str, Any]]
    default_baseline: dict[str, Any] = field(default_factory=lambda: dict(BASELINE_DEFAULTS))
    variables: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "experiment"


@dataclass
class RunContext:
    spec: ExperimentSpec
    dataset: Path
    output_dir: Path
    baseline: dict[str, Any]
    environment: dict[str, Any]
    variables: list[dict[str, Any]]
    run_id: str
    extra: dict[str, Any] = field(default_factory=dict)

    def progress(self, status: str, done: int, total: int, phase: str = "", message: str = "") -> None:
        write_progress(self.output_dir, status=status, done=done, total=total, phase=phase, message=message)


def _dataset_meta(dataset: Path) -> dict[str, Any]:
    manifest = dataset / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        "dataset": str(payload.get("dataset_id") or dataset.name),
        "pages": payload.get("pages"),
        "tier": payload.get("tier"),
        "profile": payload.get("profile"),
        "formats": payload.get("formats"),
        "title": payload.get("title"),
    }


def capture_environment(**overrides: Any) -> dict[str, Any]:
    try:
        from lightrag._version import __version__ as core_version
        from lightrag._version import __api_version__ as api_version
    except Exception:
        core_version, api_version = "unknown", "unknown"
    env: dict[str, Any] = {
        "lightrag_version": core_version,
        "api_version": api_version,
        "rag_api_url": os.getenv("RAG_API_URL", "http://127.0.0.1:9621"),
        "ollama_url": os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        "llm_binding": os.getenv("LLM_BINDING", "ollama"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "bge-m3:latest"),
        "vlm_model": os.getenv("VLM_LLM_MODEL", "gemma3:4b"),
        "vlm_process_enable": os.getenv("VLM_PROCESS_ENABLE", "false").lower() in {"1", "true", "yes"},
        "storage_dir": os.getenv("WORKING_DIR", ""),
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


def build_conditions(
    environment: dict[str, Any],
    baseline: dict[str, Any],
    dataset_meta: dict[str, Any],
    method_count: int | None = None,
) -> list[dict[str, str]]:
    merged: dict[str, Any] = {}
    merged.update(dataset_meta)
    merged.update(baseline)
    merged.update(
        {
            key: environment[key]
            for key in ("rag_api_url", "ollama_url", "storage_dir", "embedding_model")
            if environment.get(key)
        }
    )
    if method_count is not None:
        merged["methods"] = method_count
    order = [
        "dataset", "pages", "tier", "profile", "formats", "engine", "model",
        "mode", "top_k", "chunk_top_k", "num_ctx", "num_predict", "temperature",
        "kg", "vlm_model", "vlm", "methods", "rag_api_url", "ollama_url",
        "storage_dir", "embedding_model",
    ]
    result = []
    for key in order:
        if key not in merged or merged[key] in (None, ""):
            continue
        value = merged[key]
        if isinstance(value, bool):
            value = "开" if value else "关"
        elif isinstance(value, list):
            value = ",".join(str(item) for item in value)
        result.append({"key": key, "label": _CONDITION_LABELS.get(key, key), "value": str(value)})
    return result


def write_envelope(
    output_dir: Path,
    *,
    context: RunContext,
    status: str,
    methods: list[dict[str, Any]],
    report_rel_path: str | None = None,
    extra: dict[str, Any] | None = None,
    write_progress_file: bool = True,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": context.spec.kind,
        "run_id": context.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "experiment": {
            "id": context.spec.id,
            "label": context.spec.label,
            "description": context.spec.description,
        },
        "environment": context.environment,
        "baseline": context.baseline,
        "variables": context.variables,
        "methods": methods,
        "reports": {"report.md": report_rel_path} if report_rel_path else {},
    }
    if extra:
        envelope.update(extra)
    path = output_dir / "run.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if write_progress_file:
        write_progress(output_dir, status=status, done=1, total=1, phase="done", message="")
    return path


def write_simple_envelope(
    output_dir: Path,
    *,
    kind: str,
    run_id: str,
    experiment: dict[str, Any],
    baseline: dict[str, Any],
    environment: dict[str, Any],
    methods: list[dict[str, Any]],
    status: str,
    report_rel_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Envelope writer for non-registry runs (offline/online evaluators)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "experiment": experiment,
        "environment": environment,
        "baseline": baseline,
        "variables": [],
        "methods": methods,
        "reports": {"report.md": report_rel_path} if report_rel_path else {},
    }
    if extra:
        envelope.update(extra)
    path = output_dir / "run.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_progress(
    output_dir: Path,
    *,
    status: str,
    done: int,
    total: int,
    phase: str = "",
    message: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "phase": phase,
        "done": done,
        "total": total,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (output_dir / "progress.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_progress(output_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((output_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
