from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lightrag.chunker.paragraph_semantic import chunking_by_paragraph_semantic

from memory_data_service.schemas import OraclePayload
from memory_eval_tests.dataset_client import DatasetClient


SIDECAR_SUFFIXES = {
    "table": ("tables", ".tables.json"),
    "figure": ("drawings", ".drawings.json"),
    "equation": ("equations", ".equations.json"),
}


def audit_object_traceability(
    dataset_source: str,
    parsed_dir: Path,
    *,
    max_facts: int | None = None,
    chunk_token_size: int = 800,
) -> dict[str, Any]:
    oracle = OraclePayload.model_validate(DatasetClient(dataset_source).oracle())
    blocks = _load_blocks(parsed_dir)
    block_ids = {block.get("blockid") for block in blocks}
    block_text = "\n".join(str(block.get("content", "")) for block in blocks)
    chunk_block_ids = _chunk_ref_block_ids(parsed_dir, chunk_token_size)

    sidecar = {
        object_type: _load_sidecar_objects(parsed_dir, root_key, suffix)
        for object_type, (root_key, suffix) in SIDECAR_SUFFIXES.items()
    }
    oracle_counts = Counter(obj.object_type for obj in oracle.objects)
    fact_hits = []
    sampled_facts = _limit_items(oracle.facts, max_facts)
    for fact in sampled_facts:
        haystack = block_text
        if fact.object_type in sidecar:
            haystack += "\n" + "\n".join(_object_text(item) for item in sidecar[fact.object_type].values())
        normalized_haystack = _normalize_evidence(haystack)
        hit = (
            fact.fact_id in haystack
            or fact.answer in haystack
            or fact.expected_text in haystack
            or _normalize_evidence(fact.answer) in normalized_haystack
            or _normalize_evidence(fact.expected_text) in normalized_haystack
        )
        fact_hits.append(
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "object_type": fact.object_type,
                "hit": hit,
                "answer": fact.answer,
            }
        )

    object_reports = {}
    for object_type in ("table", "figure", "equation"):
        expected = oracle_counts.get(object_type, 0)
        observed = len(sidecar[object_type])
        linked = sum(1 for item in sidecar[object_type].values() if item.get("blockid") in block_ids)
        chunk_linked = sum(
            1
            for item in sidecar[object_type].values()
            if item.get("blockid") in chunk_block_ids
        )
        object_reports[object_type] = {
            "oracle_count": expected,
            "sidecar_count": observed,
            "preservation_rate": min(observed, expected) / expected if expected else 1.0,
            "linked_to_block": linked,
            "linked_to_block_rate": linked / observed if observed else 1.0,
            "linked_to_chunk": chunk_linked,
            "linked_to_chunk_rate": chunk_linked / observed if observed else 1.0,
        }

    hit_count = sum(1 for item in fact_hits if item["hit"])
    relation_report = _oracle_relation_report(oracle)
    return {
        "dataset_source": dataset_source,
        "parsed_dir": str(parsed_dir),
        "blocks": len(blocks),
        "block_ids": len(block_ids),
        "chunk_ref_block_ids": len(chunk_block_ids),
        "object_traceability": object_reports,
        "oracle_relation_traceability": relation_report,
        "facts": len(fact_hits),
        "total_facts": len(oracle.facts),
        "max_facts": max_facts,
        "fact_evidence_hits": hit_count,
        "fact_evidence_hit_rate": hit_count / len(fact_hits) if fact_hits else 1.0,
        "missed_facts": [item for item in fact_hits if not item["hit"]],
        "passed": all(
            report["preservation_rate"] >= 1.0
            and report["linked_to_block_rate"] >= 1.0
            and report["linked_to_chunk_rate"] >= 1.0
            for report in object_reports.values()
        )
        and relation_report["caption_link_rate"] >= 1.0
        and relation_report["reference_target_rate"] >= 1.0,
    }


def _load_blocks(parsed_dir: Path) -> list[dict[str, Any]]:
    blocks_path = parsed_dir if parsed_dir.is_file() else _blocks_path(parsed_dir)
    rows = [json.loads(line) for line in blocks_path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("type") == "content"]


def _blocks_path(parsed_dir: Path) -> Path:
    blocks_path = next(parsed_dir.glob("*.blocks.jsonl"), None)
    if blocks_path is None:
        raise FileNotFoundError(f"no *.blocks.jsonl found in {parsed_dir}")
    return blocks_path


def _chunk_ref_block_ids(parsed_dir: Path, chunk_token_size: int) -> set[str]:
    blocks_path = _blocks_path(parsed_dir)
    blocks = _load_blocks(blocks_path)
    merged_content = "\n".join(str(block.get("content", "")) for block in blocks)
    chunks = chunking_by_paragraph_semantic(
        CharacterTokenizer(),
        merged_content,
        chunk_token_size,
        blocks_path=str(blocks_path),
        chunk_overlap_token_size=80,
        references_tail_n=0,
    )
    ref_ids: set[str] = set()
    for chunk in chunks:
        for ref in (chunk.get("sidecar") or {}).get("refs", []):
            if ref.get("type") == "block" and ref.get("id"):
                ref_ids.add(ref["id"])
    return ref_ids


def _load_sidecar_objects(parsed_dir: Path, root_key: str, suffix: str) -> dict[str, Any]:
    path = next(parsed_dir.glob(f"*{suffix}"), None)
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get(root_key, {})


def _object_text(item: dict[str, Any]) -> str:
    values = [
        item.get("id", ""),
        item.get("heading", ""),
        item.get("content", ""),
        item.get("caption", ""),
    ]
    return "\n".join(str(value) for value in values if value)


def _normalize_evidence(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("{", "")
        .replace("}", "")
        .replace(" ", "")
        .replace("\n", "")
        .lower()
    )


class CharacterTokenizer:
    def encode(self, content: str) -> list[int]:
        return [ord(char) for char in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(token) for token in tokens)


def _oracle_relation_report(oracle: OraclePayload) -> dict[str, Any]:
    object_ids = {obj.object_id for obj in oracle.objects}
    fact_ids = {fact.fact_id for fact in oracle.facts}
    relation_counts = Counter(relation.relation_type for relation in oracle.relations)
    caption_relations = [
        relation
        for relation in oracle.relations
        if relation.relation_type == "caption_of"
    ]
    valid_caption_relations = [
        relation
        for relation in caption_relations
        if relation.source_id in object_ids and relation.target_id in object_ids
    ]
    reference_relations = [
        relation
        for relation in oracle.relations
        if relation.relation_type == "refers_to"
    ]
    valid_reference_relations = [
        relation
        for relation in reference_relations
        if relation.source_id in object_ids
        and (relation.target_id in object_ids or relation.target_id in fact_ids)
    ]
    return {
        "relation_counts": dict(sorted(relation_counts.items())),
        "caption_relations": len(caption_relations),
        "valid_caption_relations": len(valid_caption_relations),
        "caption_link_rate": (
            len(valid_caption_relations) / len(caption_relations)
            if caption_relations
            else 1.0
        ),
        "reference_relations": len(reference_relations),
        "valid_reference_relations": len(valid_reference_relations),
        "reference_target_rate": (
            len(valid_reference_relations) / len(reference_relations)
            if reference_relations
            else 1.0
        ),
    }


def _limit_items(items: list[Any], max_items: int | None) -> list[Any]:
    if max_items is None or max_items <= 0 or len(items) <= max_items:
        return items
    if max_items == 1:
        return [items[0]]
    step = (len(items) - 1) / (max_items - 1)
    indexes = sorted({round(index * step) for index in range(max_items)})
    return [items[index] for index in indexes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare oracle objects with LightRAG sidecar objects.")
    parser.add_argument("--dataset", required=True, help="Dataset directory, manifest path, or dataset HTTP URL.")
    parser.add_argument("--parsed-dir", required=True, type=Path)
    parser.add_argument("--max-facts", type=int)
    parser.add_argument("--chunk-token-size", type=int, default=800)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_object_traceability(
        args.dataset,
        args.parsed_dir,
        max_facts=args.max_facts,
        chunk_token_size=args.chunk_token_size,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Dataset: {report['dataset_source']}")
        print(f"Blocks: {report['blocks']}")
        print(json.dumps(report["object_traceability"], ensure_ascii=False, indent=2))
        print(f"Fact evidence hit rate: {report['fact_evidence_hit_rate']:.3f}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
