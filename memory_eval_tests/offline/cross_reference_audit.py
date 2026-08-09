from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from lightrag.chunker.paragraph_semantic import chunking_by_paragraph_semantic
from memory_data_service.schemas import DatasetManifest, OraclePayload
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.common.evidence import (
    blocks_path,
    load_blocks,
    normalize_evidence,
)
from memory_eval_tests.offline.chunk_traceability import CharacterTokenizer


def audit_cross_references(
    *,
    dataset_source: str,
    parsed_dir: Path,
    chunk_token_size: int = 800,
) -> dict[str, Any]:
    client = DatasetClient(dataset_source)
    manifest = DatasetManifest.model_validate(client.manifest())
    oracle = OraclePayload.model_validate(client.oracle())
    docx_file = _select_docx(client, manifest)
    docx_fields = _inspect_docx_ref_fields(docx_file)

    blocks = load_blocks(parsed_dir)
    block_text = "\n".join(str(block.get("content", "")) for block in blocks)
    normalized_block_text = normalize_evidence(block_text)
    chunks = _load_chunks(parsed_dir, chunk_token_size)
    normalized_chunks = [normalize_evidence(str(chunk.get("content", ""))) for chunk in chunks]

    ref_objects = [
        obj
        for obj in oracle.objects
        if obj.object_type == "reference"
        and ("cross_reference" in obj.labels or "table_cross_reference" in obj.labels)
    ]
    object_hits = []
    for obj in ref_objects:
        normalized_text = normalize_evidence(obj.text or obj.title)
        chunk_hit = bool(normalized_text) and any(
            normalized_text in chunk for chunk in normalized_chunks
        )
        block_hit = bool(normalized_text) and normalized_text in normalized_block_text
        object_hits.append(
            {
                "object_id": obj.object_id,
                "title": obj.title,
                "labels": obj.labels,
                "block_hit": block_hit,
                "chunk_hit": chunk_hit,
            }
        )

    field_hits = []
    for bookmark in docx_fields["ref_field_bookmarks"]:
        normalized_bookmark = normalize_evidence(bookmark)
        field_hits.append(
            {
                "bookmark": bookmark,
                "has_bookmark_target": bookmark in docx_fields["bookmark_names"],
                "sidecar_text_hit": normalized_bookmark in normalized_block_text,
                "chunk_hit": any(normalized_bookmark in chunk for chunk in normalized_chunks),
            }
        )

    ref_relations = [rel for rel in oracle.relations if rel.relation_type == "refers_to"]
    valid_ref_relations = [
        rel for rel in ref_relations if rel.source_id and rel.target_id
    ]
    return {
        "dataset_source": dataset_source,
        "parsed_dir": str(parsed_dir),
        "docx_file": str(docx_file),
        "docx_ref_fields": docx_fields["ref_field_count"],
        "docx_ref_field_bookmarks": docx_fields["ref_field_bookmarks"],
        "docx_bookmarks": len(docx_fields["bookmark_names"]),
        "ref_field_target_rate": (
            sum(1 for item in field_hits if item["has_bookmark_target"]) / len(field_hits)
            if field_hits
            else 1.0
        ),
        "ref_field_sidecar_hit_rate": (
            sum(1 for item in field_hits if item["sidecar_text_hit"]) / len(field_hits)
            if field_hits
            else 1.0
        ),
        "ref_field_chunk_hit_rate": (
            sum(1 for item in field_hits if item["chunk_hit"]) / len(field_hits)
            if field_hits
            else 1.0
        ),
        "ref_field_hits": field_hits,
        "oracle_cross_reference_objects": len(ref_objects),
        "oracle_cross_reference_block_hit_rate": (
            sum(1 for item in object_hits if item["block_hit"]) / len(object_hits)
            if object_hits
            else 1.0
        ),
        "oracle_cross_reference_chunk_hit_rate": (
            sum(1 for item in object_hits if item["chunk_hit"]) / len(object_hits)
            if object_hits
            else 1.0
        ),
        "oracle_cross_reference_hits": object_hits,
        "oracle_refers_to_relations": len(ref_relations),
        "oracle_refers_to_relation_validity": (
            len(valid_ref_relations) / len(ref_relations) if ref_relations else 1.0
        ),
        "passed": docx_fields["ref_field_count"] > 0
        and all(item["has_bookmark_target"] for item in field_hits)
        and all(item["sidecar_text_hit"] for item in field_hits)
        and all(item["chunk_hit"] for item in field_hits)
        and all(item["block_hit"] and item["chunk_hit"] for item in object_hits),
    }


def _select_docx(client: DatasetClient, manifest: DatasetManifest) -> Path:
    for generated_file in manifest.files:
        if generated_file.format == "docx" and generated_file.status == "created":
            return client.local_file(generated_file.name)
    raise FileNotFoundError(f"dataset {manifest.dataset_id} has no created DOCX file")


def _inspect_docx_ref_fields(docx_file: Path) -> dict[str, Any]:
    with zipfile.ZipFile(docx_file) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    ref_field_bookmarks = re.findall(r"\bREF\s+([A-Za-z0-9_]+)\s+\\h", document_xml)
    bookmark_names = re.findall(r'w:name="([^"]+)"', document_xml)
    return {
        "ref_field_count": len(ref_field_bookmarks),
        "ref_field_bookmarks": ref_field_bookmarks,
        "bookmark_names": bookmark_names,
    }


def _load_chunks(parsed_dir: Path, chunk_token_size: int) -> list[dict[str, Any]]:
    blocks_path = blocks_path(parsed_dir)
    blocks = load_blocks(blocks_path)
    merged_content = "\n".join(str(block.get("content", "")) for block in blocks)
    return chunking_by_paragraph_semantic(
        CharacterTokenizer(),
        merged_content,
        chunk_token_size,
        blocks_path=str(blocks_path),
        chunk_overlap_token_size=80,
        references_tail_n=0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit DOCX and sidecar cross-reference preservation.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--parsed-dir", required=True, type=Path)
    parser.add_argument("--chunk-token-size", type=int, default=800)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_cross_references(
        dataset_source=args.dataset,
        parsed_dir=args.parsed_dir,
        chunk_token_size=args.chunk_token_size,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"DOCX REF fields: {report['docx_ref_fields']}")
        print(f"REF field sidecar hit rate: {report['ref_field_sidecar_hit_rate']:.3f}")
        print(f"REF object chunk hit rate: {report['oracle_cross_reference_chunk_hit_rate']:.3f}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
