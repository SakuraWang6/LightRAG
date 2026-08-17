# memory-eval-framework 专项评审报告（f0ea9bc0）

> 评审日期：2026-08-17 · 只读评审：未 merge、未 cherry-pick、未修改任何分支/tag、未 push。
> 第一事实来源：`git show f0ea9bc0`；逐项映射到当前 `main @ 60f90f22`。`git diff main...memory-eval-framework` 仅作参考（其中部分 workflow.py 差异来自 main 侧 5352ea1b，方向相反）。

## 1. Executive Decision

| Change | Decision |
| --- | --- |
| A EvalConsole always-poll | **KEEP** |
| B JobsView always-poll | **KEEP** |
| C dispatch 60s→15s | **KEEP**（latency tuning，可选低优先级） |
| D ReportDocument 常显 TOC | **DROP** |
| E llm_analysis 报告精简 | **KEEP**（并入报告重构，数据未丢失） |
| F workflow 聚焦式报告 | **KEEP** |
| G 报告测试 | **KEEP behavior / rewrite test** |

总体：`f0ea9bc0` 中 6/7 项仍适用于当前 main，1 项（TOC）无证据支持应丢弃。建议提取为 2 个主 commit（+1 个可选微 commit），并针对 A/B/C 补齐验证。

## 2. Git / Historical Context

```text
main                     = 60f90f22
memory-eval-framework    = f0ea9bc0
fork/memory-eval-framework = b2b6f591
merge-base               = 99e9967b
main..memory-eval-framework = 仅 f0ea9bc0
memory-eval-framework..main  = 34 commits
```

main 自 merge-base 后触碰相关文件仅 `5352ea1b`（eval reliability/observability）与 `b5c7fad6`（figure timeout）。代码级核对：两者均未实现 f0ea9bc0 的任何一项行为（见 §4–§9），**不存在被 main 后续提交 supersede 的情况**。

## 3. Decision Matrix

| Change | Problem exists? | Evidence | Decision | Recommended action |
| --- | --- | --- | --- | --- |
| A EvalConsole always-poll | 是（窄窗口） | 代码数据流；无测试/日志 | KEEP | 提取 + 轮询策略测试 |
| B JobsView always-poll | 是（同类型） | 代码数据流；独立数据源 | KEEP | 提取（建议统一 hook） |
| C dispatch 60s→15s | 是（恢复/hold-gate 路径） | 代码；事件驱动主路径已即时 | KEEP（latency tuning） | 常量化 + 可选测试 |
| D sticky TOC | 无证据 | 主观 UX；main 刻意保留 hover 设计 | DROP | 不提取 |
| E llm_analysis 精简 | 是（信息重复） | 真实 report 对比 | KEEP | 随 F 一并提取 |
| F workflow 聚焦报告 | 是（可读性/行动性） | 真实 report 对比 + run 证据 | KEEP | 提取 + 测试改写 |
| G 报告测试 | — | 分支侧测试存在；main 无 | KEEP behavior / rewrite | 结构级断言重写 |

## 4. Change A — EvalConsole Always-Poll

- **Historical behavior**：`if (!hasActiveRuns) return` + 5s 轮询；idle 时完全不 poll。
- **Current main behavior**：不变（EvalConsole.tsx 175-181 行）。
- **数据流验证**：
  1. `hasActiveRuns` 由本地 state `runs`（`listEvalRuns()` 返回）计算，status ∈ {`running`,`queued`} 即 active。
  2. poll 停止后无其他自动刷新机制：无 react-query/SSE/WebSocket/焦点刷新；EvalConsole 在 App 的 TabsContent 中**保持挂载**（切换 tab 仅 `hidden`，不卸载）。
  3. queued 仍使 `hasActiveRuns=true`（排队中持续轮询）。
  4. 真实缺口：队列**完全为空**后轮询停止；此后由其他客户端/自动队列新增的任务，在当前 tab 需手动刷新才可见。同页内 mutation（wizard `onStarted` → `handleRefresh`×2、手动 refresh、run 操作）都会触发 `loadRuns()`，因此缺口主要是“外部到达”。
  5. 15s idle 成本：`/eval/runs` 走 `eval_index.scan_runs` 扫描缓存，单请求成本低；浏览器对隐藏 tab 的 timer 还会节流。
- **风险**：低（3 行改动）；双组件同时 mount 时存在既有双轮询（见 B）。
- **Decision**：KEEP。验证计划见 §11（3 个用例的 fake-timer 测试或人工验证）。

## 5. Change B — JobsView Always-Poll

- **Current main**：`if (!hasActive) return` + 5s（JobsView.tsx 96-107 行）；`hasActive` 覆盖 {`claiming`,`running`,`cancelling`,`pending`}。
- **数据源**：`listEvalJobs()`（独立于 runs 列表）；jobs 页面由 EvalConsole 在 `view==='jobs'` 时早返回渲染，**EvalConsole 仍保持挂载**，active 时会出现双轮询（既有问题，非本 commit 引入）。
- **结论**：与 A 同一类 correctness 缺口，单独数据源、单独生命周期，需单独判断——结论 **KEEP**。
- **设计建议（本阶段不实现）**：两个视图不应复制两份 interval policy；提取统一 hook（如 `useEvalPolling(active, load, 5000, 15000)`）到 `features/eval/`，并考虑 JobsView 挂载时暂停 EvalConsole 轮询以避免双轮询。

## 6. Change C — Dispatch Loop 60s→15s

- **Current main**：`eval_jobs.py:836` `time.sleep(60)`。
- **派发语义（代码验证）**：提交 run/dataset job 后立即 `_dispatch()`（890/987 行附近）；job 完成/失败 `_record_exit` 后立即 `_dispatch()`；`resume_pending_jobs` 立即 `_dispatch()`。60s 循环是**恢复/兜底**：进程重启后的 pending 恢复、`MEMORY_EVAL_WAIT_FOR_RUN` hold gate 释放检测（`_hold_blocks` 只在循环中被重新评估）。
- **结论**：`60→15` 改善的是 hold-gate 释放 / 异常恢复的 worst-case 启动延迟（约 60s→15s），**属 latency / UX tuning，不是 correctness fix**；事件驱动主路径本已即时。
- **成本**：每轮 dispatch 只读若干 JSON + 锁，毫秒级；15s 无明显 DB/CPU 压力。
- **Decision**：KEEP（低优先级）。建议提取 `DISPATCH_INTERVAL_SECONDS = 15` 常量便于测试与配置；现有 `tests/api/routes/test_eval_jobs.py` 已直接调用 `_dispatch` 测派发逻辑（无需 sleep 15s），可加一个常量断言。

## 7. Change D — ReportDocument 常显 TOC

- **Current main**：hover-reveal 窄轨（tocHover、w-11↔w-64）。该设计来自 merge-base 之前的 commit（56f3bffa/c7b3080c 形成 rail + hover），main 后续未改。
- **判断**：纯 UX preference；无 bug 证据、无用户反馈、无 accessibility 记录；sticky w-56 会挤压正文且小屏行为未验证。
- **Decision**：**DROP**。不并入 report commit（两者是不同问题）。

## 8. Report Redesign Review（E + F 合并评审）

### 真实产物对比（归档 run）

| | main 时代 report.md（evaluation-20260816-053254-b43f @ 96cde1c8） | f0ea9bc0 时代 report.md（evaluation-20260814-030144-5308 / -051451-846c） |
| --- | --- | --- |
| 概览 | 结果概览 | 结果概览 |
| 归因 | 失败归因（cause 计数 + 覆盖率） | 失败原因（按 cause 分组，带题号） |
| 流程 | 流程级归因 + `**解读**` 长篇 + 逐题流程状态表 | （删除） |
| 失败明细 | 无 expected-vs-actual 表 | **未通过题目表（题号/类型/归因/期望/实际）** |
| 检索指标 | 无 | 有 summary 时输出（该 run 未触发） |
| LLM 分析 | 总体分析 + 未通过题目分析 | 同 |

### 逐项判断

- 信息重复：`**解读**` cross-tab 散文与失败归因/逐题状态重复 → **应删除**（E-解读）。
- `逐题流程状态` 表：其数据在 `diagnosis.json` / `case_trace.json` / 逐题详情中保留；从 markdown summary 移除是**重新组织**而非删除 → **KEEP 删除**（E-逐题表），若担心纯文本阅读，可改为折叠而非删除（实现选项）。
- retrieval metrics：f0 在有 summary 时输出，main 的 retrieval.py 已具备 summary → **KEEP 新增**（F-检索指标）。
- `失败原因` grouping（带题号）：比 main 的 cause 计数更可行动 → **KEEP**（F-分组）。建议保留“可归因覆盖率”一行（f0 丢弃了它，属于小信息损失；作为细粒度修正保留）。
- `未通过题目` 表（expected vs actual）：main 完全没有 → **KEEP**（F-失败表），是报告最大增量。
- 首要任务判断：report.md 面向**人工快速评审**；完整机器记录由 case_trace/diagnosis/json 承担 → 精简不降可观测性。

**Decision**：总体方向 KEEP；细粒度：删除解读散文 ✅、删除/折叠逐题状态表 ✅、新增检索指标 ✅、失败分组 ✅、失败明细表 ✅、保留覆盖率 ✅。

## 9. Change G — 报告测试

- 分支侧 `test_report_markdown_focuses_on_results_and_failures` 断言具体文本（含“| 1 / 3”、“检索未命中（1 题）”、“平均召回@K”等）。
- 判断：测的是真实 contract（分组、题号、指标、无旧散文），但**文本耦合偏 brittle**。
- 建议：**KEEP behavior / rewrite test**——按结构断言（存在 `## 失败原因`、`## 未通过题目`；失败题号集合正确；expected/actual 行存在；给定 retrieval summary 时含指标；覆盖率行保留），减少对排版空格/格式的依赖。

## 10. Test Gap Analysis

- always-poll / 15s dispatch：**无专门测试、无截图、无行为时间线**（已搜索 tests 与前端 eval 目录）。
- 已有基建：bun test（`features/eval/utils.test.ts` 存在）、Python `tests/api/routes/test_eval_jobs.py`（直接调用 `_dispatch`，无需 sleep）。
- 结论：A/B/C 若要 KEEP，必须补最小验证（§11），否则降级为 NEEDS_TEST。

## 11. Minimal Extraction Plan（设计，不实现）

### Commit 1 — `fix(eval-ui): keep evaluation queue state refreshed`

- 文件：`lightrag_webui/src/features/eval/EvalConsole.tsx`、`JobsView.tsx`。
- 可选重构（本阶段仅设计）：提取 `useEvalPolling(active, load, activeMs=5000, idleMs=15000)`，两个视图共用；JobsView 挂载时暂停 EvalConsole 轮询以消除双轮询。
- 测试（bun + fake timers）：Case 1 active→~5s；Case 2 idle→~15s；Case 3 active A 结束→idle 仍低频 poll→queued B 出现无需手动刷新。人工验证清单：提交任务后不刷新自动出现、结束后页面自动进入 completed、外部新增任务在 idle 下 ≤15s 出现。

### Commit 2 — `refactor(eval-report): focus evaluation report on results and failures`

- 文件：`memory_eval_tests/workflow.py`（`_report_markdown(answer, diagnosis, retrieval)` + 保留覆盖率）、`memory_eval_tests/llm_analysis.py`（删 `**解读**`、逐题状态表或折叠）、`tests/memory_eval/test_product_evaluation.py`（结构级断言重写）。
- 回归：任一完整 run 的 report.md 人工对比（expected-vs-actual 表、检索指标、失败分组）。

### Commit 3（可选微 commit）— `perf(eval-jobs): poll dispatch loop every 15s`

- 文件：`lightrag/api/eval_jobs.py`（`DISPATCH_INTERVAL_SECONDS` 常量，默认 15）。
- 语义明确标注为 latency tuning（hold-gate 释放/恢复路径）；若严格限制 2 commit，可并入 Commit 1 并注明该边界差异。

### 明确丢弃

- Change D（sticky TOC）：不提取。
- `**解读**` 散文：删除（随 Commit 2）。
- Receipt：**不属于 f0ea9bc0**，不提取（已确认 f0ea9bc0 未改 `_receipt`；差异来自 main 侧 5352ea1b）。

## 12. Branch / Remote Lifecycle Recommendation

- `f0ea9bc0` 目前仅存在于本地 branch 引用；其 2 个完整 run 已在 `/Users/sakura/RAG/LightRAG-experiment-archive/` 归档。
- 建议：删除本地 branch **之前**创建 `archive/memory-eval-framework-final` → `f0ea9bc0`（本阶段不创建）；提取完成后本地 branch 可删。
- fork `memory-eval-framework`（b2b6f591）：b2b6f591 已是 main 的祖先（永久在 main 历史中），无需 tag；提取完成后 fork ref 可删（用户确认后，禁止 force push 覆盖）。
- 不推荐用 branch 长期保存：历史状态用 tag + archive 更合适。

## 13. Risks

| 风险 | 说明 |
| --- | --- |
| A/B/C 无历史行为证据 | 需按 §11 补验证后再落地，否则分类降级为 NEEDS_TEST |
| 双轮询 | EvalConsole 在 jobs 视图仍挂载；提取 hook 时一并处理 |
| report 文本耦合测试 | 改写为结构断言 |
| TOC 丢弃主观性 | 若你明确反馈 hover 难用，可单独重新评审 |
| dispatch 常量默认值 | 15s 为经验值；建议可配置（env 覆盖） |
| fork 差异 | 本地 f0ea9bc0 vs fork b2b6f591，按用户确认单独处理，不做任何同步 |

## 附：最终回答摘要

1. A：KEEP　2. B：KEEP　3. 是（同一类 correctness：队列空后停止轮询）　4. 建议统一 hook（设计）　5. 15s 合理（成本低、浏览器节流）　6. C：KEEP（低优先级）　7. latency tuning　8. 已有事件驱动主路径；循环仅恢复/兜底，可常量化　9. D：DROP　10. 报告方向 KEEP　11. `**解读**` 删除 ✅　12. 逐题状态表从 summary 移除/折叠（数据保留在 artifact）　13. 检索指标应进 summary ✅　14. 失败分组 KEEP ✅　15. 未通过题目表 KEEP ✅　16. G：KEEP behavior / rewrite　17-19. 测试计划见 §11　20. 提取文件见 §11　21. 2 主 commit + 1 可选微 commit　22. 见 §11　23. 丢弃 D、解读散文、receipt　24. 无（5352ea1b/b5c7fad6 未 supersede 任何一项）　25. 是，receipt 不属于 f0ea9bc0　26. 本地 branch：提取 + tag 后删除　27. fork branch：提取后用户确认删除（b2b6f591 已在 main 历史）　28. 建议 tag　29. `archive/memory-eval-framework-final` → f0ea9bc0　30. 先做 extraction implementation（按 §11），A/B/C 落地时附带最小验证。
