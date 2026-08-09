"""Tests for dataset schema versioning and the offline integrity smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_data_service.schemas import (
    DATASET_SCHEMA_VERSION,
    DatasetManifest,
    OraclePayload,
    check_schema_version,
)
from memory_eval_tests.offline.integrity import audit_dataset_integrity

pytestmark = pytest.mark.offline


def _write_dataset(
    tmp_path: Path,
    *,
    manifest_schema_version: str | None = None,
    oracle_schema_version: str | None = None,
) -> Path:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = {
        "dataset_id": "smoke-test",
        "tier": "smoke",
        "pages": 1,
        "profile": "basic",
        "formats": ["docx"],
        "modalities": ["text"],
        "title": "Smoke",
        "files": [
            {
                "name": "doc.docx",
                "format": "docx",
                "path": "doc.docx",
                "size_bytes": 0,
                "status": "created",
            }
        ],
    }
    if manifest_schema_version:
        manifest["schema_version"] = manifest_schema_version
    (dataset / "doc.docx").write_bytes(b"")
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    oracle = {
        "dataset_id": "smoke-test",
        "facts": [
            {
                "fact_id": "FACT-1",
                "fact_type": "direct_numeric",
                "answer": "42",
                "expected_text": "FACT-1: 42",
                "section": "S1",
                "page": 1,
                "object_type": "text",
                "object_id_hint": "",
            }
        ],
        "questions": [
            {
                "id": "Q1",
                "question": "What is x?",
                "answer": "42",
                "question_type": "direct_numeric",
                "evidence_fact_ids": ["FACT-1"],
                "expected_behavior": "answer",
            }
        ],
        "objects": [],
        "relations": [],
    }
    if oracle_schema_version:
        oracle["schema_version"] = oracle_schema_version
    (dataset / "oracle.json").write_text(json.dumps(oracle), encoding="utf-8")
    (dataset / "facts.json").write_text(
        json.dumps({"facts": oracle["facts"]}), encoding="utf-8"
    )
    (dataset / "questions.json").write_text(
        json.dumps({"questions": oracle["questions"]}),
        encoding="utf-8",
    )
    (dataset / "objects.json").write_text(json.dumps({"objects": []}), encoding="utf-8")
    (dataset / "relations.json").write_text(
        json.dumps({"relations": []}), encoding="utf-8"
    )
    return dataset


def test_offline_integrity_smoke_passes(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path)
    report = audit_dataset_integrity(str(dataset))
    assert report["passed"] is True
    assert report["schema_version"] == DATASET_SCHEMA_VERSION
    assert report["schema_supported"] is True


def test_schema_version_defaults_to_current_for_unversioned_datasets(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(tmp_path)
    manifest = DatasetManifest.model_validate(
        json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    )
    oracle = OraclePayload.model_validate(
        json.loads((dataset / "oracle.json").read_text(encoding="utf-8"))
    )
    assert manifest.schema_version == DATASET_SCHEMA_VERSION
    assert oracle.schema_version == DATASET_SCHEMA_VERSION
    assert check_schema_version(None) == (True, DATASET_SCHEMA_VERSION)


def test_unsupported_schema_version_is_reported(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        manifest_schema_version="9.9",
        oracle_schema_version="9.9",
    )
    report = audit_dataset_integrity(str(dataset))
    assert report["schema_version"] == "9.9"
    assert report["schema_supported"] is False
    assert report["passed"] is False
    assert any("unsupported schema_version" in issue for issue in report["issues"])
    assert check_schema_version("9.9") == (False, "9.9")
