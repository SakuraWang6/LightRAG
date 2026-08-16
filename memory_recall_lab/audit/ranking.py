"""Ranking-error audit for recall-lab runs.

Reads ``recall_report.json`` and expands every ``table_cell`` question into an
audit table that separates candidate-generation success from ranking errors.
The output is intentionally rule-based and does not require a live model.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_TABLE_ID_RE = re.compile(r"\bTBL-\d+\b", re.IGNORECASE)
_FACT_ID_RE = re.compile(r"\bFACT-\d+\b", re.IGNORECASE)
_FIGURE_ID_RE = re.compile(r"\bFIG-\d+\b", re.IGNORECASE)


def _ids(text: str, pattern: re.Pattern[str]) -> list[str]:
    return list(dict.fromkeys(match.group(0).upper() for match in pattern.finditer(text)))


def _candidate_table_id(candidate: dict[str, Any]) -> str | None:
    for key in ("table_id", "candidate_table_id"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    text = str(candidate.get("content_excerpt") or candidate.get("content") or "")
    ids = _ids(text, _TABLE_ID_RE)
    return ids[0] if ids else None


def _candidate_type(candidate: dict[str, Any]) -> str:
    text = str(candidate.get("content_excerpt") or candidate.get("content") or "")
    if "Object Type: Table Row" in text:
        return "row_view"
    if "Object Type: Table" in text:
        return "table_view"
    if "<table" in text:
        return "raw"
    return "other"


def _candidate_fact_ids(candidate: dict[str, Any]) -> list[str]:
    text = str(candidate.get("content_excerpt") or candidate.get("content") or "")
    return _ids(text, _FACT_ID_RE)


def _classify_case(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    gold_ranks = [
        int(candidate["rank"])
        for candidate in candidates
        if candidate.get("matched_fact_ids")
    ]
    if not gold_ranks:
        return {
            "gold_rank": None,
            "category": "retrieval_miss",
            "ahead": [],
        }

    gold_rank = min(gold_ranks)
    gold_candidate = next(
        candidate for candidate in candidates if candidate["rank"] == gold_rank
    )
    query_table_ids = _ids(query, _TABLE_ID_RE)
    gold_table_id = query_table_ids[0] if query_table_ids else _candidate_table_id(gold_candidate)
    gold_fact_ids = _candidate_fact_ids(gold_candidate)

    ahead = candidates[: gold_rank - 1]
    annotated: list[dict[str, Any]] = []
    for candidate in ahead:
        table_id = _candidate_table_id(candidate)
        annotated.append(
            {
                "rank": candidate["rank"],
                "candidate_type": _candidate_type(candidate),
                "candidate_table_id": table_id,
                "candidate_fact_id": (_candidate_fact_ids(candidate) or [None])[0],
                "exact_table_id_match": bool(table_id and table_id in query_table_ids),
                "exact_fact_id_match": bool(
                    set(_candidate_fact_ids(candidate)) & set(_ids(query, _FACT_ID_RE))
                ),
            }
        )

    wrong_table = [
        item
        for item in annotated
        if item["candidate_table_id"]
        and gold_table_id
        and item["candidate_table_id"] != gold_table_id
    ]
    same_table_wrong_row = [
        item
        for item in annotated
        if not wrong_table
        and item["candidate_type"] == "row_view"
        and item["candidate_table_id"] == gold_table_id
    ]
    representation_competition = [
        item
        for item in annotated
        if not wrong_table
        and not same_table_wrong_row
        and item["candidate_type"] in {"table_view", "raw"}
        and item["candidate_table_id"] == gold_table_id
    ]

    category = "other_ahead"
    if gold_rank == 1:
        category = "rank_one_no_error"
    elif wrong_table:
        category = "wrong_table"
    elif same_table_wrong_row:
        category = "same_table_wrong_row"
    elif representation_competition:
        category = "representation_competition"
    elif len(annotated) >= 2:
        category = "duplicate_sibling"

    return {
        "gold_rank": gold_rank,
        "gold_table_id": gold_table_id,
        "gold_fact_ids": gold_fact_ids,
        "category": category,
        "ahead": annotated,
    }


def audit_report(report: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    gold_ranks: list[int] = []
    for question in report.get("results") or []:
        if question.get("question_type") != "table_cell":
            continue
        classified = _classify_case(
            str(question.get("question") or ""),
            question.get("candidates") or [],
        )
        classified["question_id"] = question.get("question_id")
        classified["question"] = question.get("question")
        classified["expected_fact_ids"] = question.get("expected_fact_ids")
        cases.append(classified)
        category_counts[classified["category"]] += 1
        if classified["gold_rank"] is not None:
            gold_ranks.append(classified["gold_rank"])

    return {
        "question_type": "table_cell",
        "cases": len(cases),
        "category_distribution": dict(sorted(category_counts.items())),
        "gold_rank_distribution": {
            "1": sum(rank == 1 for rank in gold_ranks),
            "2": sum(rank == 2 for rank in gold_ranks),
            "3": sum(rank == 3 for rank in gold_ranks),
            "4_5": sum(4 <= rank <= 5 for rank in gold_ranks),
            "6_10": sum(6 <= rank <= 10 for rank in gold_ranks),
            "11_plus": sum(rank >= 11 for rank in gold_ranks),
            "miss": sum(1 for case in cases if case["gold_rank"] is None),
        },
        "cases": cases,
    }


def _markdown(audit: dict[str, Any]) -> str:
    labels = {
        "rank_one_no_error": "Rank 1, no ranking error",
        "wrong_table": "Type A: wrong table",
        "same_table_wrong_row": "Type B: correct table, wrong row",
        "representation_competition": "Type C: representation competition",
        "duplicate_sibling": "Type D: duplicate/sibling candidates",
        "other_ahead": "Other ahead",
        "retrieval_miss": "Retrieval miss",
    }
    lines = [
        "# Table-cell Ranking Error Audit",
        "",
        f"- 题数：{len(audit['cases'])}",
        "",
        "## 错误分类",
        "",
        "| 类型 | 题数 |",
        "| --- | ---: |",
    ]
    for category, count in audit["category_distribution"].items():
        lines.append(f"| {labels.get(category, category)} | {count} |")
    lines.extend(["", "## Gold rank 分布", ""])
    for key, label in (
        ("1", "Rank 1"),
        ("2", "Rank 2"),
        ("3", "Rank 3"),
        ("4_5", "Rank 4-5"),
        ("6_10", "Rank 6-10"),
        ("11_plus", "Rank 11+"),
        ("miss", "未命中"),
    ):
        lines.append(f"- {label}：{audit['gold_rank_distribution'].get(key, 0)}")
    lines.extend(["", "## 逐题", ""])
    for case in audit["cases"]:
        rank = case.get("gold_rank")
        lines.append(
            f"### {case.get('question_id')} · {labels.get(case.get('category'), case.get('category'))}"
        )
        lines.append("")
        lines.append(f"- Gold rank：{rank if rank else '未命中'}")
        lines.append(f"- Query：{case.get('question')}")
        if case.get("ahead"):
            lines.append("")
            lines.append("| 前序 Rank | 类型 | Table ID | Fact ID | TBL 精确匹配 |")
            lines.append("| ---: | --- | --- | --- | --- |")
            for item in case["ahead"]:
                lines.append(
                    f"| {item['rank']} | {item['candidate_type']} | "
                    f"{item['candidate_table_id'] or '—'} | "
                    f"{item['candidate_fact_id'] or '—'} | "
                    f"{'是' if item['exact_table_id_match'] else '否'} |"
                )
        lines.append("")
    return "\n".join(lines)


def write_ranking_audit(run_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Write ``ranking_audit.json`` and ``ranking_audit.md`` into a run dir."""
    run_dir = Path(run_dir)
    audit = audit_report(report)
    markdown = _markdown(audit)
    (run_dir / "ranking_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "ranking_audit.md").write_text(markdown + "\n", encoding="utf-8")
    return audit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Audit table-cell ranking errors")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report_path = args.run_dir / "recall_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    audit = write_ranking_audit(args.run_dir, report)
    output = args.output or (args.run_dir / "ranking_audit.md")
    markdown = _markdown(audit)
    output.write_text(markdown + "\n", encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
