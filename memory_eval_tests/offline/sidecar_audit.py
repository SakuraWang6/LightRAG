from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class ParserCliError(RuntimeError):
    def __init__(self, *, cmd: list[str], returncode: int, stdout: str, stderr: str):
        super().__init__(f"parser CLI exited with status {returncode}")
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_parser_cli(
    input_file: Path,
    *,
    engine: str,
    output_dir: Path,
    force_reparse: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "lightrag.parser.cli",
        str(input_file),
        "--engine",
        engine,
        "-o",
        str(output_dir),
        "--preview",
        "0",
    ]
    if force_reparse:
        cmd.append("--force-reparse")
    started = time.perf_counter()
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ParserCliError(
            cmd=cmd,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    elapsed = time.perf_counter() - started
    parsed_dir = output_dir / f"{input_file.name}.parsed"
    if not parsed_dir.exists():
        stemmed = list(output_dir.glob("*.parsed"))
        if len(stemmed) == 1:
            parsed_dir = stemmed[0]
    if not parsed_dir.exists():
        raise FileNotFoundError(f"parser did not produce a parsed directory in {output_dir}")
    (parsed_dir / "parse_time_seconds.txt").write_text(f"{elapsed:.3f}\n", encoding="utf-8")
    if result.stdout:
        (parsed_dir / "parser_stdout.log").write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        (parsed_dir / "parser_stderr.log").write_text(result.stderr, encoding="utf-8")
    return parsed_dir


def audit_sidecar(parsed_dir: Path) -> dict[str, Any]:
    blocks_path = next(parsed_dir.glob("*.blocks.jsonl"), None)
    if blocks_path is None:
        raise FileNotFoundError(f"no *.blocks.jsonl found in {parsed_dir}")
    rows = [json.loads(line) for line in blocks_path.read_text(encoding="utf-8").splitlines()]
    meta = rows[0] if rows else {}
    blocks = [row for row in rows[1:] if row.get("type") == "content"]
    block_ids = {row.get("blockid") for row in blocks}
    declared_blocks = meta.get("blocks")
    block_issues = []
    if declared_blocks is not None and declared_blocks != len(blocks):
        block_issues.append(
            {
                "type": "block_count_mismatch",
                "declared": declared_blocks,
                "actual": len(blocks),
            }
        )
    for index, block in enumerate(blocks):
        if not block.get("blockid"):
            block_issues.append({"type": "missing_blockid", "row_index": index + 2})

    modality_stats = {}
    invalid_sidecar_refs = []
    missing_asset_refs = []
    for root_key, suffix in (
        ("tables", ".tables.json"),
        ("drawings", ".drawings.json"),
        ("equations", ".equations.json"),
    ):
        path = next(parsed_dir.glob(f"*{suffix}"), None)
        items = {}
        if path and path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload.get(root_key, {})
        linked = 0
        missing_blockid = 0
        for item_id, item in items.items():
            blockid = item.get("blockid")
            if not blockid:
                missing_blockid += 1
                invalid_sidecar_refs.append(
                    {"modality": root_key, "item_id": item_id, "problem": "missing_blockid"}
                )
            elif blockid in block_ids:
                linked += 1
            else:
                invalid_sidecar_refs.append(
                    {
                        "modality": root_key,
                        "item_id": item_id,
                        "problem": "unknown_blockid",
                        "blockid": blockid,
                    }
                )
            if root_key == "drawings" and item.get("path"):
                asset_path = parsed_dir / item["path"]
                if not asset_path.exists():
                    missing_asset_refs.append(
                        {"item_id": item_id, "path": item["path"], "problem": "missing_asset"}
                    )
        analyzed = sum(
            1
            for item in items.values()
            if (item.get("llm_analyze_result") or {}).get("status") == "success"
        )
        modality_stats[root_key] = {
            "count": len(items),
            "linked_to_block": linked,
            "missing_blockid": missing_blockid,
            "analysis_success": analyzed,
        }

    positioned = sum(1 for row in blocks if row.get("positions"))
    headings = sum(1 for row in blocks if row.get("heading"))
    passed = not block_issues and not invalid_sidecar_refs and not missing_asset_refs
    return {
        "parsed_dir": str(parsed_dir),
        "document_name": meta.get("document_name"),
        "parse_engine": meta.get("parse_engine"),
        "blocks": len(blocks),
        "headings": headings,
        "positioned_blocks": positioned,
        "position_coverage": positioned / len(blocks) if blocks else 0.0,
        "modalities": modality_stats,
        "block_ref_validation": {
            "declared_blocks": declared_blocks,
            "actual_blocks": len(blocks),
            "issues": block_issues,
        },
        "sidecar_ref_validation": {
            "invalid_refs": invalid_sidecar_refs,
            "missing_assets": missing_asset_refs,
            "invalid_ref_count": len(invalid_sidecar_refs),
            "missing_asset_count": len(missing_asset_refs),
        },
        "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit LightRAG sidecar output.")
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--engine", default="native")
    parser.add_argument("--output-dir", type=Path, default=Path("memory_eval_tests/runs/sidecar"))
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        parsed_dir = run_parser_cli(
            args.input_file,
            engine=args.engine,
            output_dir=args.output_dir,
            force_reparse=args.force_reparse,
        )
        report = audit_sidecar(parsed_dir)
    except Exception as exc:
        report = {
            "input_file": str(args.input_file),
            "engine": args.engine,
            "output_dir": str(args.output_dir),
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if isinstance(exc, ParserCliError):
            report.update(
                {
                    "returncode": exc.returncode,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "cmd": exc.cmd,
                }
            )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Parser audit failed: {report['error_type']}: {report['error']}")
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Sidecar: {report['parsed_dir']}")
        print(f"Blocks: {report['blocks']}")
        print(f"Position coverage: {report['position_coverage']:.3f}")
        print(json.dumps(report["modalities"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
