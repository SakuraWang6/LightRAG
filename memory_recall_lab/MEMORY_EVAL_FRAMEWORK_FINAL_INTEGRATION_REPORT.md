# memory-eval-framework 最终集成报告

> 日期：2026-08-17 · 阶段：integration + verification + tag + push + local branch retirement

## Integration

```text
old main        = 60f90f22
merge commit    = 7c669461（merge: integrate reviewed memory eval framework improvements，--no-ff）
new main        = 7c669461（本报告提交并推送后 main HEAD 前移 1 个 docs commit，fork/main 与 local main 保持一致）

extraction commits（保留，未 squash）：
  358cd0e0 docs: add memory eval framework review
  d3001408 refactor(eval-report): focus report on results and failures
  ed245379 fix(eval-ui): keep evaluation queue state refreshed
  24279d0a perf(eval-jobs): make fallback dispatch interval configurable
  fabf27cb docs: add memory eval framework extraction report
```

merge 前边界检查：diff 仅涉及 eval/report/polling/dispatch/文档；无 `ReportDocument.tsx`、无 `_receipt`、无 Recall/Ranking/Chunking 修改。

## Tests（merge 后实际执行）

| Command | Result |
| --- | --- |
| `pytest tests/memory_eval tests/api/routes/test_eval_jobs.py tests/recall_lab -q` | 74 passed |
| `bun test src/features/eval/useEvalPolling.test.ts src/features/eval/utils.test.ts` | 16 passed |
| `bunx tsc --noEmit` | clean |
| `bunx eslint <eval 前端 4 文件>` | clean |

## Historical mapping

| Historical change | Final state |
| --- | --- |
| A EvalConsole polling | migrated（ed245379，hook + idle 15s） |
| B JobsView polling | migrated（ed245379，同一 hook） |
| C dispatch interval | migrated（24279d0a，常量 + env） |
| D sticky TOC | intentionally dropped |
| E/F report redesign | migrated（d3001408） |
| G tests | rewritten/migrated（d3001408，4 个 contract 测试） |
| receipt | not part of f0 |

## Report artifact smoke

用归档 run `evaluation-20260816-053254-b43f`（main 时代）数据在合并后的 main 上重建报告，结构验证通过：

```text
结果概览 / 检索指标 / 失败原因（含 coverage + question IDs）/ 未通过题目（expected vs actual）
无 **解读** 冗余散文
无逐题流程状态 summary 表
诊断数据未删除（diagnosis.json / case_trace.json 原样保留）
```

## Historical preservation

```text
archive/memory-eval-framework-final
→ f0ea9bc0（annotated tag，已创建并 push 到 fork；peeled target 验证 = f0ea9bc0…）
```

## Branch cleanup

```text
local memory-eval-framework：
  删除方式 -D（-d 拒绝：f0ea9bc0 不是 main 祖先）
  使用 -D 前验证：archive/memory-eval-framework-final → f0ea9bc0；f0 A/B/C/E/F/G 均有新实现或明确 drop；
  无剩余 valuable unique code（仅刻意丢弃的 sticky TOC）；2 个 f0 时代 run 已归档

local fix/memory-eval-framework-extraction：
  删除方式 -d（已合入 main）
```

## Remote

```text
fork/main                      = local main（fast-forward push，验证一致）
archive/memory-eval-framework-final → f0ea9bc0（已 push，peeled 验证通过）
fork/memory-eval-framework     = b2b6f591（未修改；b2b6f591 是 main 祖先，永久在历史中）
remote historical branch not deleted yet（safe-to-delete-remote，等待用户最终确认）
```

## Remaining item

```text
manual browser polling smoke: pending（本环境无 WebUI 交互；自动测试已通过，人工清单见 extraction report §4）
```
