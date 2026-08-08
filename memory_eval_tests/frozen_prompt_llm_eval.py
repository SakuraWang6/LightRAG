"""Evaluate a hosted chat model against previously frozen LightRAG prompts."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from memory_eval_tests.answer_eval import score_answer
from memory_eval_tests.dataset_client import DatasetClient


def _split_prompt(prompt: str) -> tuple[str, str]:
    marker = "\n\n---User Query---\n"
    if marker not in prompt:
        raise ValueError("Frozen prompt is missing the LightRAG user-query marker")
    return tuple(prompt.split(marker, 1))  # type: ignore[return-value]


def _chat_completion(*, base_url: str, api_key: str, model: str, prompt: str) -> str:
    system_prompt, user_query = _split_prompt(prompt)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "temperature": 0,
        "max_tokens": 768,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Hosted model request failed with HTTP {error.code}: {detail}") from error
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"Hosted model returned no choices: {str(body)[:500]}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"Hosted model returned non-text content: {str(message)[:500]}")
    return content


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else None


def _report(
    *, args: argparse.Namespace, frozen: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    total = len(rows)
    return {
        "model": args.model,
        "base_url": args.base_url,
        "retrieval": "frozen KG mix context",
        "top_k": frozen["top_k"],
        "chunk_top_k": frozen["chunk_top_k"],
        "max_total_tokens": frozen["max_total_tokens"],
        "cases": total,
        "complete": total == len(frozen["prompts"]),
        "answer_accuracy": sum(row["exact_match"] for row in rows) / total if total else 0.0,
        "groundedness": sum(row["grounded"] for row in rows) / total if total else 0.0,
        "hallucination_rate": sum(row["hallucinated"] for row in rows) / total if total else 0.0,
        "citation_accuracy": sum(row["citation_correct"] for row in rows) / total if total else 0.0,
        "abstention_accuracy": _average(rows, "abstention_correct"),
        "results": rows,
    }


def _write_checkpoint(
    *, args: argparse.Namespace, frozen: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_report(args=args, frozen=frozen, rows=rows), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--base-url", default="https://api.chatanywhere.tech/v1")
    parser.add_argument("--api-key-env", default="LIGHTRAG_PROJECT_OPENAI_API_KEY")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        parser.error(f"Set {args.api_key_env}; do not pass the key as an argument")

    frozen = json.loads(args.prompts.read_text(encoding="utf-8"))
    facts = {
        fact["fact_id"]: fact
        for fact in DatasetClient(str(args.dataset)).oracle().get("facts", [])
    }
    rows: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        rows = json.loads(args.output.read_text(encoding="utf-8")).get("results", [])
    completed_ids = {row["question_id"] for row in rows}
    pending = [item for item in frozen["prompts"] if item["question_id"] not in completed_ids]
    if args.max_cases > 0:
        pending = pending[: args.max_cases]
    for index, item in enumerate(pending, start=1):
        answer = _chat_completion(
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            prompt=item["prompt"],
        )
        evidence = [facts[fact_id] for fact_id in item.get("evidence_fact_ids", []) if fact_id in facts]
        scores = score_answer(
            answer_text=answer,
            expected=str(item.get("expected", "")),
            question=item,
            # Evidence/citation availability is frozen with the prompt; it is
            # intentionally invariant to the generation model in this experiment.
            evidence_facts=evidence,
            references_blob=item["prompt"],
        )
        rows.append({"question_id": item["question_id"], "answer": answer, **scores})
        _write_checkpoint(args=args, frozen=frozen, rows=rows)
        print(f"[{index}/{len(pending)}] {item['question_id']}", flush=True)

    report = _report(args=args, frozen=frozen, rows=rows)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
