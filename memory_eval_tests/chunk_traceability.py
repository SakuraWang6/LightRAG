from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lightrag.chunker.paragraph_semantic import chunking_by_paragraph_semantic

from memory_data_service.schemas import OraclePayload
from memory_eval_tests.dataset_client import DatasetClient
from memory_eval_tests.object_traceability import _normalize_evidence


class CharacterTokenizer:
    def encode(self, content: str) -> list[int]:
        return [ord(char) for char in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(token) for token in tokens)


def audit_chunk_traceability(
    *,
    dataset_source: str,
    parsed_dir: Path,
    chunk_token_size: int = 800,
    max_facts: int | None = None,
) -> dict[str, Any]:
    oracle = OraclePayload.model_validate(DatasetClient(dataset_source).oracle())
    blocks_path = next(parsed_dir.glob("*.blocks.jsonl"), None)
    if blocks_path is None:
        raise FileNotFoundError(f"no *.blocks.jsonl found in {parsed_dir}")

    blocks = _load_blocks(blocks_path)
    block_ids = {block["blockid"] for block in blocks if block.get("blockid")}
    merged_content = "\n".join(str(block.get("content", "")) for block in blocks)
    chunks = chunking_by_paragraph_semantic(
        CharacterTokenizer(),
        merged_content,
        chunk_token_size,
        blocks_path=str(blocks_path),
        chunk_overlap_token_size=80,
        references_tail_n=0,
    )

    chunks_with_sidecar = [chunk for chunk in chunks if chunk.get("sidecar")]
    invalid_refs: list[dict[str, Any]] = []
    for chunk in chunks_with_sidecar:
        for ref in chunk.get("sidecar", {}).get("refs", []):
            if ref.get("type") != "block" or ref.get("id") not in block_ids:
                invalid_refs.append(
                    {
                        "chunk_order_index": chunk.get("chunk_order_index"),
                        "ref": ref,
                    }
                )

    fact_hits = []
    chunk_texts = [str(chunk.get("content", "")) for chunk in chunks]
    normalized_chunks = [_normalize_evidence(text) for text in chunk_texts]
    sampled_facts = _limit_items(oracle.facts, max_facts)
    for fact in sampled_facts:
        normalized_answer = _normalize_evidence(fact.answer)
        normalized_expected = _normalize_evidence(fact.expected_text)
        hit_indexes = [
            index
            for index, text in enumerate(chunk_texts)
            if fact.fact_id in text
            or fact.answer in text
            or fact.expected_text in text
            or normalized_answer in normalized_chunks[index]
            or normalized_expected in normalized_chunks[index]
        ]
        fact_hits.append(
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "object_type": fact.object_type,
                "hit": bool(hit_indexes),
                "chunk_indexes": hit_indexes,
            }
        )

    hit_count = sum(1 for item in fact_hits if item["hit"])
    caption_hits = _object_text_hits(
        oracle.objects,
        normalized_chunks,
        object_type="caption",
    )
    reference_hits = _object_text_hits(
        oracle.objects,
        normalized_chunks,
        object_type="reference",
    )
    return {
        "dataset_source": dataset_source,
        "parsed_dir": str(parsed_dir),
        "blocks": len(blocks),
        "chunks": len(chunks),
        "chunks_with_sidecar": len(chunks_with_sidecar),
        "chunk_sidecar_coverage": len(chunks_with_sidecar) / len(chunks) if chunks else 0.0,
        "invalid_ref_count": len(invalid_refs),
        "invalid_refs": invalid_refs,
        "facts": len(fact_hits),
        "total_facts": len(oracle.facts),
        "max_facts": max_facts,
        "chunk_fact_hits": hit_count,
        "chunk_fact_hit_rate": hit_count / len(fact_hits) if fact_hits else 1.0,
        "missed_facts": [item for item in fact_hits if not item["hit"]],
        "caption_objects": caption_hits["objects"],
        "caption_chunk_hits": caption_hits["hits"],
        "caption_chunk_hit_rate": caption_hits["hit_rate"],
        "missed_caption_objects": caption_hits["missed"],
        "reference_objects": reference_hits["objects"],
        "reference_chunk_hits": reference_hits["hits"],
        "reference_chunk_hit_rate": reference_hits["hit_rate"],
        "missed_reference_objects": reference_hits["missed"],
        "passed": bool(chunks)
        and not invalid_refs
        and hit_count == len(fact_hits)
        and caption_hits["hits"] == caption_hits["objects"]
        and reference_hits["hits"] == reference_hits["objects"],
    }


def _load_blocks(blocks_path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in blocks_path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("type") == "content"]


def _limit_items(items: list[Any], max_items: int | None) -> list[Any]:
    if max_items is None or max_items <= 0 or len(items) <= max_items:
        return items
    if max_items == 1:
        return [items[0]]
    step = (len(items) - 1) / (max_items - 1)
    indexes = sorted({round(index * step) for index in range(max_items)})
    return [items[index] for index in indexes]


def _object_text_hits(
    objects: list[Any],
    normalized_chunks: list[str],
    *,
    object_type: str,
) -> dict[str, Any]:
    candidates = [obj for obj in objects if obj.object_type == object_type]
    missed = []
    hits = 0
    for obj in candidates:
        text = obj.text or obj.title
        normalized_text = _normalize_evidence(text)
        hit = bool(normalized_text) and any(
            normalized_text in chunk for chunk in normalized_chunks
        )
        if hit:
            hits += 1
        else:
            missed.append(
                {
                    "object_id": obj.object_id,
                    "object_type": obj.object_type,
                    "title": obj.title,
                }
            )
    return {
        "objects": len(candidates),
        "hits": hits,
        "hit_rate": hits / len(candidates) if candidates else 1.0,
        "missed": missed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit chunk -> sidecar.refs -> blockid traceability.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--parsed-dir", required=True, type=Path)
    parser.add_argument("--chunk-token-size", type=int, default=800)
    parser.add_argument("--max-facts", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_chunk_traceability(
        dataset_source=args.dataset,
        parsed_dir=args.parsed_dir,
        chunk_token_size=args.chunk_token_size,
        max_facts=args.max_facts,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Chunks: {report['chunks']}")
        print(f"Chunk sidecar coverage: {report['chunk_sidecar_coverage']:.3f}")
        print(f"Chunk fact hit rate: {report['chunk_fact_hit_rate']:.3f}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
