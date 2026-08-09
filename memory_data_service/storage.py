from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from memory_data_service.schemas import DatasetManifest, DatasetSummary, OraclePayload

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_GENERATED_ROOT = Path(
    os.getenv("MEMORY_EVAL_DATASETS_ROOT", str(PACKAGE_DIR / "generated"))
)


def ensure_root(root: Path = DEFAULT_GENERATED_ROOT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    payload = _to_jsonable(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _to_jsonable(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    if isinstance(payload, dict):
        return {key: _to_jsonable(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_to_jsonable(value) for value in payload]
    return payload


def load_manifest(dataset_path: Path) -> DatasetManifest:
    return DatasetManifest.model_validate(read_json(dataset_path / "manifest.json"))


def load_oracle(dataset_path: Path) -> OraclePayload:
    return OraclePayload.model_validate(read_json(dataset_path / "oracle.json"))


def list_datasets(root: Path = DEFAULT_GENERATED_ROOT) -> list[DatasetSummary]:
    if not root.exists():
        return []
    summaries: list[DatasetSummary] = []
    for dataset_path in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = dataset_path / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = load_manifest(dataset_path)
        summaries.append(
            DatasetSummary(
                dataset_id=manifest.dataset_id,
                tier=manifest.tier,
                profile=manifest.profile,
                pages=manifest.pages,
                path=str(dataset_path),
                created_at=manifest.created_at,
                files=[f.name for f in manifest.files],
            )
        )
    return summaries
