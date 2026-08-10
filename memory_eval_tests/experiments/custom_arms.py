"""Custom arm-based experiment: run a base experiment once per parameter arm.

The user picks a registered base experiment and defines parameter arms (for
example ``top_k=1,3,5,10,20`` or ``model=A,B``).  The runner takes the
cartesian product of the arms (capped) and executes the base experiment's
runner once per arm in a child directory, then aggregates each arm's methods
into the parent envelope so the console's MethodCompare view renders them side
by side.

Child baseline synthesis order (highest priority last):
``base.default_baseline -> parent context.baseline -> arm values``.  Arm values
override only the keys the designer declared; everything the user picked in the
wizard (model, num_ctx, ...) is inherited by every arm.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from memory_eval_tests.experiments.common import ExperimentSpec, RunContext
from memory_eval_tests.experiments.comparison_stats import paired_case_deltas

DEFAULT_MAX_ARMS = 8
MAX_ARMS_CAP = 16


def _parse_axes(raw: str) -> dict[str, list[str]]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("axes must be a JSON object of {paramKey: [values]}") from None
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("axes must be a non-empty JSON object")
    axes: dict[str, list[str]] = {}
    for key, values in parsed.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("arm axis keys must be non-empty strings")
        if not isinstance(values, list) or not values:
            raise ValueError(f"arm axis {key!r} needs a non-empty value list")
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                cleaned.append(text)
        if not cleaned:
            raise ValueError(f"arm axis {key!r} has no valid values")
        axes[key.strip()] = cleaned
    return axes


def _arm_label(values: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in values.items())


def _run_custom_arms(context: RunContext) -> dict[str, Any]:
    extra = context.extra
    base_id = str(extra.get("base_experiment") or "").strip()
    if not base_id:
        raise ValueError("base_experiment is required")
    from memory_eval_tests.experiments.registry import get_spec

    base = get_spec(base_id)
    if base.id == "custom_arms":
        raise ValueError("custom_arms cannot be its own base experiment")
    axes = _parse_axes(str(extra.get("axes") or "{}"))
    comparison_type = str(extra.get("comparison_type") or "").strip()
    comparison_plan: dict[str, Any] | None = None
    if comparison_type:
        from lightrag.api.eval_comparison import validate_plan

        comparison_plan = validate_plan(
            comparison_type=comparison_type,
            variables=axes,
            inputs=extra,
        )
        if comparison_type == "answer_model" and base.id != "frozen_prompt_llm_eval":
            raise ValueError(
                "answer_model comparisons must use frozen_prompt_llm_eval as base_experiment"
            )
        if comparison_type in {"embedding", "full_pipeline"} and base.id != "end_to_end_baseline":
            raise ValueError(
                f"{comparison_type} comparisons must use end_to_end_baseline as base_experiment"
            )
        if comparison_type == "retrieval_configuration" and base.id != "end_to_end_baseline":
            raise ValueError(
                "retrieval_configuration comparisons must use end_to_end_baseline as base_experiment"
            )
    max_arms = int(extra.get("max_arms") or DEFAULT_MAX_ARMS)
    if max_arms < 1 or max_arms > MAX_ARMS_CAP:
        raise ValueError(f"max_arms must be between 1 and {MAX_ARMS_CAP}")
    keys = list(axes)
    combos = list(itertools.product(*(axes[key] for key in keys)))
    if len(combos) > max_arms:
        raise ValueError(f"arm product {len(combos)} exceeds max_arms {max_arms}")

    total = len(combos)
    methods = []
    failures = 0
    report_lines = [
        "# 自定义实验（参数臂）",
        "",
        f"- 基座实验：`{base.id}`",
        f"- 臂数：{total}",
        "",
        "| 臂 | 状态 | 子报告 |",
        "|---|---|---|",
    ]
    for index, combo in enumerate(combos, start=1):
        arm_values = {key: value for key, value in zip(keys, combo)}
        child_arm_values = dict(arm_values)
        if comparison_type == "answer_model" and "answer_model" in child_arm_values:
            child_arm_values["model"] = child_arm_values.pop("answer_model")
        if comparison_type == "retrieval_configuration" and "retrieval_mode" in child_arm_values:
            child_arm_values["mode"] = child_arm_values.pop("retrieval_mode")
        child_baseline = {
            **dict(base.default_baseline),
            **dict(context.baseline),
            **child_arm_values,
        }
        label = _arm_label(arm_values)
        child_dir = context.output_dir / f"arm-{index}"
        child_context = RunContext(
            spec=base,
            dataset=context.dataset,
            output_dir=child_dir,
            baseline=child_baseline,
            environment=dict(context.environment),
            variables=[],
            run_id=f"{context.run_id}-arm-{index}",
            extra={
                **context.extra,
                "arm_overrides": json.dumps(arm_values, ensure_ascii=False, sort_keys=True),
            },
            runs_root=context.runs_root,
            started_at=context.started_at,
        )
        context.progress("running", index - 1, total, phase=f"arm {index}: {label}")
        try:
            payload = base.runner(child_context)
            arm_status = str(payload.get("status", "complete"))
        except Exception as exc:  # keep the run going; mark the arm failed
            arm_status = "failed"
            payload = {
                "methods": [],
                "report": "",
                "extra": {"error": f"{type(exc).__name__}: {exc}"},
            }
        if arm_status != "complete":
            failures += 1
        child_methods = payload.get("methods") or []
        if len(child_methods) == 1:
            method = dict(child_methods[0])
            method["method"] = f"{label}·{method.get('method', 'arm')}"
            method["label"] = f"{label} · {method.get('label', '')}".strip() or label
        else:
            method = {
                "method": label,
                "label": label,
                "params": arm_values,
                "summary": {"arms": 1},
                "results": [],
            }
        method["summary"] = {**method.get("summary", {}), "status": arm_status}
        methods.append(method)
        report_lines.append(f"| {label} | {arm_status} | arm-{index}/report.md |")
        child_report = payload.get("report")
        if child_report:
            child_dir.mkdir(parents=True, exist_ok=True)
            (child_dir / "report.md").write_text(child_report, encoding="utf-8")
        context.progress("running", index, total, phase=f"arm {index}: {label}")

    status = "failed" if failures else "complete"
    paired = paired_case_deltas(methods)
    if paired:
        report_lines.extend(
            [
                "",
                "## 配对 case 差异（相对首臂）",
                "",
                "| 候选臂 | 共享 case | 平均差异 | 胜/平/负 | 证据 |",
                "|---|---:|---:|---:|---|",
                *[
                    "| {candidate} | {case_count} | {mean_delta:.4f} | "
                    "{wins}/{ties}/{losses} | {evidence} |".format(**item)
                    for item in paired
                ],
            ]
        )
    report_lines.extend(
        [
            "",
            (
                f"**{failures}/{total} 臂失败**；为避免把不完整样本当作实验结果，"
                "任一臂失败时整体标记为 failed，失败臂见上表。"
                if failures
                else "全部臂完成。"
            ),
            "",
        ]
    )
    if comparison_plan:
        report_lines.extend(
            [
                "",
                f"- 比较模板：`{comparison_type}`",
                f"- 索引要求：`{comparison_plan['index_requirement']}`",
                "- 执行依赖："
                + ", ".join(f"`{item}`" for item in comparison_plan["execution_dependencies"]),
            ]
        )
    return {
        "methods": methods,
        "report": "\n".join(report_lines),
        "paired_case_deltas": paired,
        "status": status,
    }


spec = ExperimentSpec(
    id="custom_arms",
    label="自定义参数臂实验",
    description=(
        "选择基座实验并定义参数臂（笛卡尔积，默认 ≤8 臂、上限 16），"
        "逐臂在子目录运行并聚合对比。"
    ),
    runner=_run_custom_arms,
    kind="experiment",
    extra_schema={
        "base_experiment": "str",
        "axes": "str",
        "max_arms": "int",
        "comparison_type": "str",
        "frozen_context_run_id": "str",
        "environment_profile_id": "str",
        "environment_profile_version": "int",
    },
)
