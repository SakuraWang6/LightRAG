from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetClient:
    source: str

    def manifest(self) -> dict[str, Any]:
        if self.source.startswith("http://") or self.source.startswith("https://"):
            return self._get_json(self.source.rstrip("/"))
        path = Path(self.source)
        if path.is_dir():
            path = path / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def oracle(self) -> dict[str, Any]:
        if self.source.startswith("http://") or self.source.startswith("https://"):
            return self._get_json(self.source.rstrip("/") + "/oracle")
        path = Path(self.source)
        if path.is_file():
            path = path.parent
        return json.loads((path / "oracle.json").read_text(encoding="utf-8"))

    def local_file(self, name: str) -> Path:
        if self.source.startswith("http://") or self.source.startswith("https://"):
            raise ValueError("HTTP datasets do not expose local file paths")
        path = Path(self.source)
        if path.is_file():
            path = path.parent
        target = path / name
        if not target.exists():
            raise FileNotFoundError(target)
        return target

    @staticmethod
    def _get_json(url: str) -> dict[str, Any]:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
