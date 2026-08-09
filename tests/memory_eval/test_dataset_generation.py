"""Tests for dataset generation overwrite semantics."""

from __future__ import annotations

import pytest

from memory_data_service.schemas import DatasetCreateRequest, DatasetManifest

pytestmark = pytest.mark.offline


def _request(dataset_id: str) -> DatasetCreateRequest:
    return DatasetCreateRequest(
        tier="smoke",
        profile="rich",
        pages=1,
        formats=["docx"],
        modalities=["text"],
        dataset_id=dataset_id,
    )


def test_generate_refuses_overwrite_without_force(monkeypatch, tmp_path) -> None:
    import memory_data_service.generators as generators

    dataset_id = "dup"
    (tmp_path / dataset_id).mkdir(parents=True)
    (tmp_path / dataset_id / "manifest.json").write_text("{}", encoding="utf-8")
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        tier="smoke",
        pages=1,
        profile="basic",
        formats=["docx"],
        modalities=["text"],
        title="T",
    )
    monkeypatch.setattr(
        generators,
        "generate_rich_dataset",
        lambda request, root=tmp_path: manifest,
    )

    with pytest.raises(ValueError, match="already exists"):
        generators.generate_dataset(_request(dataset_id), root=tmp_path)
    result = generators.generate_dataset(
        _request(dataset_id), root=tmp_path, force=True
    )
    assert result.dataset_id == dataset_id


def test_generate_allows_fresh_dataset(monkeypatch, tmp_path) -> None:
    import memory_data_service.generators as generators

    manifest = DatasetManifest(
        dataset_id="fresh",
        tier="smoke",
        pages=1,
        profile="basic",
        formats=["docx"],
        modalities=["text"],
        title="T",
    )
    monkeypatch.setattr(
        generators,
        "generate_rich_dataset",
        lambda request, root=tmp_path: manifest,
    )
    assert generators.generate_dataset(_request("fresh"), root=tmp_path) is manifest
