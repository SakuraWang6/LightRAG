from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from memory_data_service.schemas import OraclePayload
from memory_eval_tests.common.dataset_client import DatasetClient
from memory_eval_tests.offline.object_traceability import (
    SIDECAR_SUFFIXES,
    _load_blocks,
    _load_sidecar_objects,
    _normalize_evidence,
)


def audit_layout_preservation(
    *,
    dataset_source: str,
    parsed_dir: Path,
) -> dict[str, Any]:
    oracle = OraclePayload.model_validate(DatasetClient(dataset_source).oracle())
    blocks = _load_blocks(parsed_dir)
    block_ids = {block.get("blockid") for block in blocks if block.get("blockid")}

    blocks_with_positions = [block for block in blocks if block.get("positions")]
    meaningful_position_blocks = [
        block for block in blocks if _has_meaningful_position(block.get("positions"))
    ]
    page_position_blocks = [
        block for block in blocks if _has_page_or_bbox_position(block.get("positions"))
    ]
    normalized_block_text = _normalize_evidence(
        "\n".join(str(block.get("content", "")) for block in blocks)
    )
    complex_layout_text = _complex_layout_text_preservation(
        oracle,
        normalized_block_text,
    )
    object_position_reports = {}
    for object_type, (root_key, suffix) in SIDECAR_SUFFIXES.items():
        items = _load_sidecar_objects(parsed_dir, root_key, suffix)
        linked_to_positioned_block = 0
        linked_to_meaningful_position = 0
        for item in items.values():
            blockid = item.get("blockid")
            block = next((row for row in blocks if row.get("blockid") == blockid), None)
            if block is None:
                continue
            if block.get("positions"):
                linked_to_positioned_block += 1
            if _has_meaningful_position(block.get("positions")):
                linked_to_meaningful_position += 1
        object_position_reports[object_type] = {
            "sidecar_count": len(items),
            "linked_to_positioned_block": linked_to_positioned_block,
            "linked_to_positioned_block_rate": (
                linked_to_positioned_block / len(items) if items else 1.0
            ),
            "linked_to_meaningful_position": linked_to_meaningful_position,
            "linked_to_meaningful_position_rate": (
                linked_to_meaningful_position / len(items) if items else 1.0
            ),
        }

    oracle_objects_with_page = [
        obj for obj in oracle.objects if obj.page_start and obj.page_start > 0
    ]
    layout_accuracy_evaluable = bool(page_position_blocks)
    return {
        "dataset_source": dataset_source,
        "parsed_dir": str(parsed_dir),
        "oracle_objects": len(oracle.objects),
        "oracle_objects_with_page": len(oracle_objects_with_page),
        "oracle_page_metadata_coverage": (
            len(oracle_objects_with_page) / len(oracle.objects) if oracle.objects else 1.0
        ),
        "blocks": len(blocks),
        "block_ids": len(block_ids),
        "blocks_with_positions": len(blocks_with_positions),
        "position_coverage": len(blocks_with_positions) / len(blocks) if blocks else 0.0,
        "meaningful_position_blocks": len(meaningful_position_blocks),
        "meaningful_position_coverage": (
            len(meaningful_position_blocks) / len(blocks) if blocks else 0.0
        ),
        "page_or_bbox_position_blocks": len(page_position_blocks),
        "page_or_bbox_position_coverage": (
            len(page_position_blocks) / len(blocks) if blocks else 0.0
        ),
        "layout_accuracy_evaluable": layout_accuracy_evaluable,
        "layout_accuracy_note": (
            "Native DOCX sidecar currently exposes paraid positions with null ranges, "
            "so page/bbox layout accuracy cannot be verified from this sidecar."
            if not layout_accuracy_evaluable
            else ""
        ),
        "object_position_traceability": object_position_reports,
        "complex_layout_text_preservation": complex_layout_text,
        "passed": bool(blocks)
        and len(blocks_with_positions) == len(blocks)
        and all(
            item["linked_to_positioned_block_rate"] >= 1.0
            for item in object_position_reports.values()
        )
        and complex_layout_text["hit_rate"] >= 1.0,
    }


def _has_meaningful_position(positions: Any) -> bool:
    if not positions:
        return False
    for position in positions:
        if not isinstance(position, dict):
            continue
        range_value = position.get("range")
        if isinstance(range_value, list) and any(value is not None for value in range_value):
            return True
        for key in ("page", "bbox", "polygon", "rect"):
            if position.get(key):
                return True
    return False


def _has_page_or_bbox_position(positions: Any) -> bool:
    if not positions:
        return False
    for position in positions:
        if not isinstance(position, dict):
            continue
        for key in ("page", "bbox", "polygon", "rect"):
            if position.get(key):
                return True
    return False


def _complex_layout_text_preservation(
    oracle: OraclePayload,
    normalized_block_text: str,
) -> dict[str, Any]:
    targets = [
        obj
        for obj in oracle.objects
        if obj.object_type in {"layout_region", "textbox"}
        or any(label in obj.labels for label in ("column_text", "textbox", "floating_object"))
    ]
    hits = []
    for obj in targets:
        normalized_text = _normalize_evidence(obj.text or obj.title)
        hit = bool(normalized_text) and normalized_text in normalized_block_text
        hits.append(
            {
                "object_id": obj.object_id,
                "object_type": obj.object_type,
                "title": obj.title,
                "labels": obj.labels,
                "hit": hit,
            }
        )
    hit_count = sum(1 for item in hits if item["hit"])
    textbox_hits = [item for item in hits if item["object_type"] == "textbox"]
    textbox_hit_count = sum(1 for item in textbox_hits if item["hit"])
    return {
        "objects": len(hits),
        "hits": hit_count,
        "hit_rate": hit_count / len(hits) if hits else 1.0,
        "textbox_objects": len(textbox_hits),
        "textbox_hits": textbox_hit_count,
        "textbox_hit_rate": (
            textbox_hit_count / len(textbox_hits) if textbox_hits else 1.0
        ),
        "missed": [item for item in hits if not item["hit"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit parser layout and position preservation.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--parsed-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_layout_preservation(dataset_source=args.dataset, parsed_dir=args.parsed_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Blocks: {report['blocks']}")
        print(f"Position coverage: {report['position_coverage']:.3f}")
        print(f"Layout accuracy evaluable: {report['layout_accuracy_evaluable']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
