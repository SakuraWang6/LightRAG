"""Tests for the dataset service auth, deletion and pagination."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import memory_data_service.app as app_module

pytestmark = pytest.mark.offline


def _write_dataset(root, dataset_id: str) -> None:
    dataset = root / dataset_id
    dataset.mkdir(parents=True)
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "tier": "smoke",
                "pages": 1,
                "profile": "basic",
                "formats": ["docx"],
                "modalities": ["text"],
                "title": dataset_id,
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    (dataset / "oracle.json").write_text(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "facts": [],
                "questions": [],
                "objects": [],
                "relations": [],
            }
        ),
        encoding="utf-8",
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module, "DEFAULT_GENERATED_ROOT", tmp_path)
    return TestClient(app_module.app)


def test_auth_is_off_by_default_and_env_gated(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MEMORY_DATA_SERVICE_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    assert client.get("/datasets").status_code == 200

    monkeypatch.setenv("MEMORY_DATA_SERVICE_API_KEY", "secret")
    assert client.get("/datasets").status_code == 401
    assert client.get("/datasets", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/datasets", headers={"X-API-Key": "wrong"}).status_code == 401


def test_list_datasets_paginates(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MEMORY_DATA_SERVICE_API_KEY", raising=False)
    for index in range(3):
        _write_dataset(tmp_path, f"d{index}")
    client = _client(tmp_path, monkeypatch)
    payload = client.get("/datasets?limit=2&offset=1").json()
    assert payload["total"] == 3
    assert [item["dataset_id"] for item in payload["datasets"]] == ["d1", "d2"]


def test_delete_removes_dataset(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MEMORY_DATA_SERVICE_API_KEY", raising=False)
    _write_dataset(tmp_path, "d0")
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/datasets/d0").status_code == 200
    assert not (tmp_path / "d0").exists()
    assert client.get("/datasets/d0").status_code == 404
    assert client.delete("/datasets/d0").status_code == 404


def test_dataset_id_rejects_path_traversal() -> None:
    for bad in ("..", "../x", "a/b", "a\\b", ""):
        with pytest.raises(Exception):
            app_module._safe_dataset_id(bad)
    assert app_module._safe_dataset_id("rich-smoke-v1") == "rich-smoke-v1"
