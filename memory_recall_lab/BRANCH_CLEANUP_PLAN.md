# 分支清理计划（BRANCH_CLEANUP_PLAN）

> 阶段：Phase 2 设计。**在用户明确确认前，不删除、不 force-push、不 rebase 任何本地或远程分支。**

> Phase 4 更新（2026-08-17）：统一基线已由 `refactor/recall-experiment-config` 更新为 `main @ b979c5c8`；
> 历史 run 已归档（`/Users/sakura/RAG/LightRAG-experiment-archive/`）并通过 SHA256 校验；
> 9 个 `exp/recall-*` + 1 个 `archive/recall-r1-final-tip` annotated tags 已创建。
> 最终逐分支矩阵见 `FINAL_BRANCH_CLEANUP.md`；执行删除前仍等待用户确认。

## A. Merge / Extract（需要把独有能力并入统一基线）

| Branch | 提取内容 | 去向 |
| --- | --- | --- |
| exp/recall-lab | memory_recall_lab harness（run/retrieval/server/static/README）+ tests/recall_lab | 统一基线的 `memory_recall_lab/` |
| recall-c3-table-row-view | `_table_views` multi-view representation（配置化） | lightrag chunker + config |
| recall-c3-table-row-view-exact-id | TBL/FIG exact-id 配置项 | retrieval.exact_id.types |
| recall-r1-structured-ranker | `_structured_rank` 转为 ranking strategy；`audit_ranking.py` 进 memory_recall_lab/audit/ | ranking 层 + 实验工具 |
| fix/lightrag-defects | 47dfe6fa（ingestion timeout 按 figure 缩放） | memory_eval_tests/workflow.py |
| memory-eval-framework | （人工确认后）always-poll / 15s 派发小 commit | WebUI eval 前端 |

## B. Convert to Config（能力已统一，差异只是开关）

| Branch | 对应配置 |
| --- | --- |
| recall-a1-atomic-raw | a1_atomic_raw.yaml（raw=true, preceding_context=false） |
| exp/recall-lab 的 A2 行为 | a2_atomic_context.yaml（= main 默认） |
| recall-a3-structured-envelope | a3_structured_envelope.yaml |
| recall-b0-dense-only | b0_dense_only.yaml（exact_id.enabled=false） |
| recall-b1-exact-id-table | b1_exact_id.yaml（types 含 TBL/FIG） |
| recall-c3-table-row-view | c3_table_row_view.yaml |
| recall-c3-table-row-view-exact-id | r0_c3_exact_id.yaml |
| recall-r1-structured-ranker | r1_structured_ranker.yaml |
| recall-a0-old-fixed-token | a0_fixed_token.yaml（`historical: true, legacy_mode: true`；若无法安全复现则只记录 commit/tag） |

## C. Historical Tag Then Delete（保留历史、停止开发）

建议 annotated tag（在删除前创建，指向各分支 tip 或关键 run 对应 commit）：

```text
exp/recall-a0-fixed-token        → recall-a0-old-fixed-token (b140c9db)
exp/recall-a1-atomic-raw         → recall-a1-atomic-raw (8f84c648)
exp/recall-a2-atomic-context     → exp/recall-lab (02b81123)
exp/recall-a3-structured-envelope → recall-a3-structured-envelope (cc55df85)
exp/recall-b0-dense-only         → recall-b0-dense-only (e06bba9a)
exp/recall-b1-exact-id           → recall-b1-exact-id-table (2bcba866)
exp/recall-c3-table-row          → recall-c3-table-row-view (96efaf5c)
exp/recall-r0-c3-exact-id        → recall-c3-table-row-view-exact-id (3c1ef2ca)
exp/recall-r1-structured         → recall-r1-structured-ranker (2a6d6156)
```

已合入 main 的 7 个 fix 分支同样建议打轻量 tag（如 `fix/table-sidecar-backfill` → commit 81a52d1d）后删除，便于审计。

## D. Already Contained / Obsolete（最优先删除候选）

| Branch | 状态 |
| --- | --- |
| fix/data-service-realism | 完全在 main（merge 7eeca0de） |
| fix/data-service-spacing | 完全在 main（merge 22715881） |
| fix/eval-service-reliability | 完全在 main（merge b169e157） |
| fix/lightrag-platform-retrieval | 完全在 main（merge 99712271） |
| fix/explicit-id-table-regression | 完全在 main（merge 96cde1c8） |
| fix/table-sidecar-backfill | 完全在 main（merge 392a913d） |
| fix/table-context-preservation | 完全在 main（merge 4f8380d2） |

## 清理前置条件（硬性检查）

1. ✅ 先归档 `memory_recall_lab/runs/`（11 个 run）与 `memory_eval_tests/runs/`（27 个 run）——它们全是 gitignored 的本地文件，是唯一结果来源。
2. ✅ 先创建全部 annotated tags（指向分支 tip 与关键 run 的 git_commit）。
3. ✅ 先在 `refactor/recall-experiment-config` 完成能力提取并用 9 个 YAML 表达全部实验臂。
4. ✅ R1 复现 table_cell R@1/R@3/R@5 = 100%、MRR = 1.0 后才能继续清理。
5. ✅ `fork/memory-eval-framework` 远端 ref（b2b6f591）与本地不一致，推送/清理前先确认。

## 最终保留分支建议

```text
main                                  # 统一基线
refactor/recall-experiment-config     # 当前开发分支（完成后并入 main）
（可选）临时保留 exp/recall-lab        # 直到 harness 完整提取完成
```

## 迁移顺序（汇总）

```text
Step 1  确认 main 已包含所有 correctness fixes（已完成：7/7 fix 分支 + defects 3/4）
Step 2  从 exp/recall-lab 提取 evaluation infrastructure
Step 3  从 C3 提取 multi-view capability
Step 4  从 exact-id 分支提取通用 identifier retrieval
Step 5  从 R1 提取 structured ranking capability
Step 6  统一为 config-driven experiments + run metadata
Step 7  重新运行关键 regression（A1/C3/R0/R1）
Step 8  为历史 milestone 打 tag
Step 9  归档 runs 目录后列出可安全删除的 branch
Step 10 人工确认后删除
```

## 风险清单

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| runs 目录丢失 | 高 | 清理前先 tar/复制到安全位置，并考虑纳入版本控制（或至少记录 manifest sha + git_commit） |
| structured rank 移出核心路径导致 R1 无法复现 | 高 | 迁移时保留等价 ranking strategy，并以 R1 smoke 作为回归门禁 |
| A0 legacy 行为重新进入默认路径 | 中 | legacy adapter / tag 方案，不写回默认 chunker |
| fork 远端与本地 ref 不一致 | 中 | 删除/推送前先 `git fetch fork` 核对 |
| memory-eval-framework UI 改动被误删 | 低 | 先人工评审 f0ea9bc0，确认是否提取 |
