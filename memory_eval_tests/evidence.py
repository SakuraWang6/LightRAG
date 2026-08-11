"""Shared sidecar and evidence-text helpers for product evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_evidence(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("{", "")
        .replace("}", "")
        .replace(" ", "")
        .replace("\n", "")
        .lower()
    )


def blocks_path(parsed_dir: Path) -> Path:
    blocks_path = next(parsed_dir.glob("*.blocks.jsonl"), None)
    if blocks_path is None:
        raise FileNotFoundError(f"no *.blocks.jsonl found in {parsed_dir}")
    return blocks_path


def load_blocks(parsed_dir: Path) -> list[dict[str, Any]]:
    blocks_file = parsed_dir if parsed_dir.is_file() else blocks_path(parsed_dir)
    rows = [
        json.loads(line)
        for line in blocks_file.read_text(encoding="utf-8").splitlines()
    ]
    return [row for row in rows if row.get("type") == "content"]
