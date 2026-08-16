# 最终 Branch Cleanup Matrix（Phase 4）

> 生成时间：2026-08-17 · 统一基线：`main @ 2daaa40b`（capability integration merge `b979c5c8`）
> 状态更新（Phase 5A 2026-08-17）：18 个 safe 分支已实际删除本地 ref（`deleted-local`）；未执行 remote push；memory-eval-framework 仍 manual-review-pending。
> 前置条件：历史 run 已归档并通过 SHA256 校验（`/Users/sakura/RAG/LightRAG-experiment-archive/`）；9 个实验 milestone tags + 1 个 final-tip tag 已创建。

| Branch | In main? | Archive OK? | Tag OK? | Unique code? | Delete safe? | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| main | 自身 | n/a | n/a | n/a | 否（永久保留） | 统一基线 main @ 2daaa40b（integration merge b979c5c8） |
| refactor/recall-experiment-config | ✅（merge b979c5c8） | n/a | n/a | 无 | ✅ deleted-local | 已完整合入 main，无独有内容 |
| exp/recall-lab | ✅（harness 全部提取） | ✅ | ✅ exp/recall-a2-atomic-context → 4f8380d2 | 无 | ✅ deleted-local | harness/README/UI/tests 均已在 main；A2 = configs/a2_atomic_context.yaml |
| recall-a0-old-fixed-token | ❌（historical-only） | ✅ a0 run | ✅ exp/recall-a0-fixed-token → b140c9db | 无（历史复现走 tag） | ✅ deleted-local | A0 不可从当前代码复现，但已由 tag b140c9db 固定 |
| recall-a1-atomic-raw | ✅（preceding_context 开关） | ✅ a1 run | ✅ exp/recall-a1-atomic-raw → 8f84c648 | 无 | ✅ deleted-local | configs/a1_atomic_raw.yaml |
| recall-a3-structured-envelope | ✅（envelope 开关） | ✅ a3 run | ✅ exp/recall-a3-structured-envelope → cc55df85 | 无 | ✅ deleted-local | configs/a3_structured_envelope.yaml |
| recall-b0-dense-only | ✅（exact_id 开关） | ✅ b0 run | ✅ exp/recall-b0-dense-only → e06bba9a | 无 | ✅ deleted-local | configs/b0_dense_only.yaml |
| recall-b1-exact-id-table | ✅（exact_id types） | ✅ b1 run | ✅ exp/recall-b1-exact-id → 2bcba866 | 无 | ✅ deleted-local | configs/b1_exact_id.yaml |
| recall-c3-table-row-view | ✅（multi-view 开关） | ✅ c3 run | ✅ exp/recall-c3-table-row → 96efaf5c | 无 | ✅ deleted-local | configs/c3_table_row_view.yaml |
| recall-c3-table-row-view-exact-id | ✅ | ✅ r0 run | ✅ exp/recall-r0-c3-exact-id → 3c1ef2ca | 无 | ✅ deleted-local | configs/r0_c3_exact_id.yaml |
| recall-r1-structured-ranker | ✅（structured strategy） | ✅ r1 run | ✅ exp/recall-r1-structured → 202346c3；archive/recall-r1-final-tip → 2a6d6156 | 无 | ✅ deleted-local | configs/r1_structured_ranker.yaml |
| fix/data-service-realism | ✅（merge 7eeca0de） | n/a | n/a（merge commit 保留历史） | 无 | ✅ deleted-local | 无额外 tag 需求 |
| fix/data-service-spacing | ✅（merge 22715881） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/eval-service-reliability | ✅（merge b169e157） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/lightrag-platform-retrieval | ✅（merge 99712271） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/explicit-id-table-regression | ✅（merge 96cde1c8） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/table-sidecar-backfill | ✅（merge 392a913d） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/table-context-preservation | ✅（merge 4f8380d2） | n/a | n/a | 无 | ✅ deleted-local | 同上 |
| fix/lightrag-defects | ✅（3/4 等价 + 47dfe6fa 已提取为 b5c7fad6） | n/a | n/a（merge commit 保留历史） | 无 | ✅ deleted-local | git cherry 验证 4 个 commit 均已等价进入统一基线 |
| memory-eval-framework | ❌ | n/a | n/a | ✅ f0ea9bc0（always-poll / 15s dispatch） | ❌ 保留 | manual-review-pending，独立任务处理 |

## 结论

- **deleted-local（Phase 5A 已完成）**：refactor/recall-experiment-config、exp/recall-lab、8 个 recall 实验分支、7 个 fix 分支、fix/lightrag-defects —— 共 18 个本地 ref 已删除。
- **keep**：main。
- **manual-review**：memory-eval-framework。

> 注意：删除前仍建议先归档 `memory_eval_tests/runs` 中与 memory-eval-framework 相关的 3 个 run（git_commit 811f47ef / f0ea9bc0 / 816d2bec 相关），本阶段已整体归档，无遗漏。
