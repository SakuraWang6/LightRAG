# memory-eval-framework Minimal Extraction Report

> 日期：2026-08-17 · 实现分支：`fix/memory-eval-framework-extraction`（基于 main 60f90f22）
> 原则：不是把 main 变回 f0ea9bc0，而是按评审结论，以当前 main 架构重新、最小化实现仍然成立的能力。未 merge、未 cherry-pick、未删除/创建 tag、未 push。

## 1. Implementation Summary

```text
implemented: report redesign, queue polling, dispatch interval
dropped:     sticky TOC（评审 DROP）
deferred:    none（dispatch 以常量化 + env 覆盖形式实现）
receipt:     not part of f0ea9bc0 — untouched
```

## 2. Historical Change Mapping

| f0 Change | Review Decision | New implementation | Commit | Status |
| --- | --- | --- | --- | --- |
| A EvalConsole always-poll | KEEP | `useEvalPolling` hook（active 5s / idle 15s），`enabled: view !== 'jobs'` 避免双轮询 | ed245379 | implemented |
| B JobsView always-poll | KEEP | 同一 hook 接入 JobsView（独立数据源） | ed245379 | implemented |
| C dispatch 60s→15s | KEEP（latency） | `_DISPATCH_INTERVAL_SECONDS = 15` + `MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS` env（无效值回退、≤0 钳到 1） | 24279d0a | implemented |
| D ReportDocument sticky TOC | DROP | 未修改 `ReportDocument.tsx` | — | intentionally dropped |
| E llm_analysis 精简 | KEEP | 删除 `**解读**` 与 `### 逐题流程状态`；compact cross-tab 保留于确定性 fallback | d3001408 | implemented |
| F workflow 聚焦报告 | KEEP | 结果概览 + 检索指标 + 可归因覆盖率 + 失败原因分组（带题号）+ 未通过题目表（期望/实际） | d3001408 | implemented |
| G 报告测试 | KEEP behavior / rewrite | 4 个 contract/structure 断言测试（替换旧的文本耦合测试） | d3001408 | implemented |

## 3. Report Changes

- **新增**：`## 检索指标`（retrieval summary 存在时）、`## 失败原因`（按 cause 分组、带 question IDs）、`## 未通过题目` 表（题号/类型/归因/期望/实际）、未通过题数。
- **保留**：结果概览、可归因覆盖率、trace-unavailable 提示；完整诊断仍由 `diagnosis.json` / `case_trace.json` / 逐题详情承担。
- **删除**：`**解读**` 散文、`### 逐题流程状态` 表（从 summary 移除，数据未删）、旧 `_diagnosis_markdown`。
- **真实 report 验证**：用归档 run `evaluation-20260816-053254-b43f`（main 时代）的 answer/diagnosis/retrieval 数据重建新报告，结构为 `结果概览 → 检索指标 → 失败原因 → 未通过题目`，覆盖率为 100%，失败题表含 expected/actual；与旧报告（失败归因 + 流程级归因 + 逐题状态）及 f0 报告（失败原因 + 未通过题目）对比确认：可行动信息增加，无底层数据丢失，覆盖率未回退。

## 4. Polling Changes

```text
policy      : active → 5000ms，idle → 15000ms（不再停止）
shared hook : useEvalPolling（named constants EVAL_POLL_ACTIVE_MS / EVAL_POLL_IDLE_MS）
双轮询      : EvalConsole 在 view === 'jobs' 时 enabled=false，JobsView 独立轮询
测试        : bun —— evalPollInterval 纯函数（active/idle/custom 3 例）
人工验证    : checklist 见下（未在真实浏览器执行，本环境无 WebUI 交互）
```

人工验证清单：

1. 打开 Eval UI，等待队列为空；不刷新页面，从其他 tab/client 创建 evaluation → 确认 ≤15s + 请求延迟内出现。
2. 创建 A、B 两个任务；A 完成后确认 B 无需手动刷新继续更新。
3. 切到 jobs 视图，确认 EvalConsole 不再发起 runs 请求（避免双轮询）。
4. 隐藏 tab 后浏览器会节流 timer（正常行为，无逻辑错误）。

## 5. Dispatch

```text
implemented : 是（非 DEFER）
interval    : 默认 15s（原 60s），常量 _DISPATCH_INTERVAL_SECONDS
配置方式    : MEMORY_EVAL_DISPATCH_INTERVAL_SECONDS（遵循模块既有 MEMORY_EVAL_* 调优模式）
语义        : latency tuning（recovery / hold-gate 路径）；submit/completion/resume 已直接 _dispatch()
测试        : default=15、env=5、env=0→1（clamp）、env=abc→15（fallback）
```

## 6. Explicit Drops

```text
Sticky TOC（ReportDocument w-56 always-visible）
→ intentionally dropped（无 correctness 问题、无用户反馈证据、main hover rail 为主动设计）

Receipt（_receipt）
→ not part of f0ea9bc0（差异来自 main 侧 5352ea1b）；完全未修改
```

## 7. Tests

| Command | Result |
| --- | --- |
| `pytest tests/memory_eval/test_product_evaluation.py` | 21 passed（17 既有 + 4 新报告 contract 测试） |
| `pytest tests/api/routes/test_eval_jobs.py` | 14 passed（12 既有 + 2 新 dispatch interval 测试） |
| `pytest tests/recall_lab` | 27 passed（确认 recall 体系未被触碰） |
| `pytest tests/memory_eval`（目录） | 33 passed |
| `bun test src/features/eval/useEvalPolling.test.ts` | 3 passed |
| `bun test src/features/eval/utils.test.ts` | 13 passed（既有） |
| `bunx tsc --noEmit` | clean |
| `bunx eslint <4 个前端文件>` | clean |

已知环境性失败（与本次无关）：全量套件中可选依赖模块（postgres/neo4j/milvus/opensearch/redis/anthropic/bedrock 等）在 main 基线同样失败。

## 8. Branch Lifecycle Readiness

```text
memory-eval-framework 剩余差异：
  - ReportDocument sticky TOC：唯一未迁移代码，评审结论 intentionally dropped
  - 其余 A/B/C/E/F/G 均已在 fix/memory-eval-framework-extraction 以新架构实现

unique valuable code remaining：无（除刻意丢弃的 TOC）
historical tag 条件：已满足（建议 archive/memory-eval-framework-final → f0ea9bc0，待用户确认后创建）
delete-local 条件：tag 创建 + 用户确认后可删
delete-fork 条件：用户确认后可删（b2b6f591 已是 main 祖先，永久在历史中）
```

## 附：本阶段 commit 列表

```text
358cd0e0 docs: add memory eval framework review
d3001408 refactor(eval-report): focus report on results and failures
ed245379 fix(eval-ui): keep evaluation queue state refreshed
24279d0a perf(eval-jobs): make fallback dispatch interval configurable
```

全部位于 `fix/memory-eval-framework-extraction`，未 merge 到 main。
