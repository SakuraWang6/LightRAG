"""Build immutable answer-model comparison inputs from controlled I2 traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


def freeze_final_contexts(*, parent_run_dir: Path, output_path: Path) -> dict[str, Any]:
    run = json.loads((parent_run_dir / "run.json").read_text(encoding="utf-8"))
    cases = json.loads((parent_run_dir / "case_trace.json").read_text(encoding="utf-8")).get("cases") or []
    prompts: list[dict[str, Any]] = []
    for case in cases:
        final = case.get("final_context") or {}
        oracle = case.get("oracle") or {}
        if final.get("status") != "observed":
            continue
        system_prompt = final.get("system_prompt")
        user_query = final.get("user_query") or oracle.get("question")
        if not isinstance(system_prompt, str) or not isinstance(user_query, str):
            continue
        prompt = f"{system_prompt}\n\n---User Query---\n{user_query}"
        prompts.append(
            {
                "question_id": case.get("question_id"),
                "question": oracle.get("question"),
                "expected": oracle.get("answer"),
                "evidence_fact_ids": oracle.get("evidence_fact_ids") or [],
                "final_context": final.get("content"),
                "system_prompt": system_prompt,
                "user_query": user_query,
                "prompt": prompt,
            }
        )
    if not prompts:
        raise ValueError("parent run has no observed final-context traces to freeze")
    decoding = {
        key: (run.get("baseline") or {}).get(key)
        for key in ("temperature", "num_predict", "num_ctx")
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "parent_run_id": run.get("run_id"),
        "dataset_fingerprint": ((run.get("execution_manifest") or {}).get("dataset") or {}).get("manifest_sha256"),
        "case_ids": [item["question_id"] for item in prompts],
        "decoding": decoding,
        "seed": (run.get("baseline") or {}).get("seed", {"value": "unknown", "reason": "run has no declared seed"}),
        "prompts": prompts,
    }
    payload["input_hash"] = _hash({key: payload[key] for key in ("dataset_fingerprint", "case_ids", "decoding", "seed", "prompts")})
    payload["generation_parameters_hash"] = _hash(decoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
