"""Tests for the dataset service auth, deletion and pagination."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import memory_data_service.app as app_module
from memory_data_service.generators import generate_dataset
from memory_data_service.schemas import DatasetCreateRequest
from memory_data_service.storage import list_datasets, load_oracle

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


def test_list_datasets_keeps_legacy_display_name_empty(tmp_path) -> None:
    _write_dataset(tmp_path, "legacy")

    summary = list_datasets(tmp_path)[0]

    assert summary.dataset_id == "legacy"
    assert summary.display_name == ""


def test_custom_legacy_document_title_becomes_dataset_name(tmp_path) -> None:
    manifest = generate_dataset(
        DatasetCreateRequest(
            dataset_id="legacy-named",
            title="Legacy named decision set",
            profile="basic",
            pages=1,
            formats=["docx"],
            modalities=["text"],
        ),
        root=tmp_path,
    )

    assert manifest.display_name == "Legacy named decision set"
    assert manifest.title == "Legacy named decision set"


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
        with pytest.raises(HTTPException):
            app_module._safe_dataset_id(bad)
    assert app_module._safe_dataset_id("rich-smoke-v1") == "rich-smoke-v1"


def test_chinese_rich_dataset_contains_chinese_oracle_and_provenance(tmp_path) -> None:
    manifest = generate_dataset(
        DatasetCreateRequest(
            dataset_id="zh-rich-smoke",
            display_name="中文富文档测评集",
            language="zh",
            profile="rich",
            pages=5,
            formats=["docx"],
            modalities=["text", "tables", "figures", "equations"],
        ),
        root=tmp_path,
    )

    oracle = load_oracle(tmp_path / manifest.dataset_id)
    assert manifest.language == "zh"
    assert manifest.display_name == "中文富文档测评集"
    assert manifest.title == "中文富文档测评集"
    assert manifest.generation_provenance is not None
    assert manifest.generation_provenance.input_parameters["language"] == "zh"
    assert oracle.language == "zh"
    assert any(question.question_type == "direct_numeric" for question in oracle.questions)
    assert any("是多少" in question.question for question in oracle.questions)
    assert any(question.question_type == "equation" for question in oracle.questions)
    assert any(question.question_type == "figure_caption" for question in oracle.questions)
    # Gold table rows must carry the same unit as the oracle answer, otherwise
    # a model that reads "27.5" from the cell is marked wrong against "27.5 ms".
    table_answers = [q.answer for q in oracle.questions if q.question_type == "table_cell"]
    assert table_answers
    assert all(any(unit in answer for unit in ("ms", "次/秒", "%")) for answer in table_answers)
    assert any(fact.fact_type == "governance_owner" for fact in oracle.facts)
    assert any(question.id == "Q-RELEASE-GATE-0004" for question in oracle.questions)
    assert any(question.question_type == "cross_document" for question in oracle.questions)
    # Regression: repeated multi-hop questions must anchor the page in the
    # question text, otherwise identical queries collapse to one retrieval
    # context and one cached answer (observed as 4 identical wrong answers).
    multihop = [q for q in oracle.questions if q.id.startswith("Q-MULTIHOP-")]
    assert multihop and all(
        f"第 {int(q.id.rsplit('-', 1)[1])} 页" in q.question for q in multihop
    )
    release_gates = [q for q in oracle.questions if q.id.startswith("Q-RELEASE-GATE-")]
    assert release_gates and all(
        f"第 {int(q.id.rsplit('-', 1)[1])} 页" in q.question for q in release_gates
    )
    texts = [q.question for q in oracle.questions]
    assert len(texts) == len(set(texts))
    assert oracle.objects
    assert (tmp_path / manifest.dataset_id / "zh-rich-smoke.docx").exists()


def test_english_rich_dataset_contains_operational_dependencies(tmp_path) -> None:
    manifest = generate_dataset(
        DatasetCreateRequest(
            dataset_id="northstar-rich-smoke",
            display_name="Northstar operational decision set",
            language="en",
            profile="rich",
            pages=5,
            formats=["docx"],
            modalities=["text", "tables", "figures", "equations"],
        ),
        root=tmp_path,
    )

    oracle = load_oracle(tmp_path / manifest.dataset_id)
    assert manifest.display_name == "Northstar operational decision set"
    assert manifest.generation_provenance is not None
    assert manifest.generation_provenance.template_version == "rich-docx-v2"
    assert any(fact.fact_type == "governance_owner" for fact in oracle.facts)
    assert any(question.id == "Q-RELEASE-GATE-0004" for question in oracle.questions)
    release_gates = [q for q in oracle.questions if q.id.startswith("Q-RELEASE-GATE-")]
    assert release_gates and all(
        f"retrieval cell {int(q.id.rsplit('-', 1)[1]):04d}" in q.question.lower()
        for q in release_gates
    )
    texts = [q.question for q in oracle.questions]
    assert len(texts) == len(set(texts))
