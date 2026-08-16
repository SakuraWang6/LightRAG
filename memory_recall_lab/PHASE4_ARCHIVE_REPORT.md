# Phase 4 Archive Report

> 生成时间：2026-08-17 · 仓库：`/Users/sakura/RAG/LightRAG` · 统一基线：`main @ b979c5c8`

## 1. Archive

| 项目 | 值 |
| --- | --- |
| 归档 run 总数 | 47（recall_lab 19 + memory_eval_tests 28） |
| 归档文件数 | 1354 |
| 总大小 | 391,334,826 bytes（约 373 MB；tar.gz 99 MB） |
| Archive 目录 | `/Users/sakura/RAG/LightRAG-experiment-archive/` |
| 便携归档 | `/Users/sakura/RAG/LightRAG-experiment-archive-2026-08-17.tar.gz` |
| 便携归档 SHA256 | `fcb43bfde22b192bc1f8714d0326a5f6856e2a977c17b9a9e6786b2a099b4405` |
| archive_manifest.json SHA256 | `0be2b3183edc762141f6d36fd4398ae93676c2d98275b3af02703439d6e493c7` |
| Verification | source↔archive 全树逐文件 SHA256 一致；124 个关键结果文件 0 mismatch；SHA256SUMS 124/124 通过；0 missing；0 duplicate |
| partial / failed runs | 10（见验证报告 §3：A3 partial、A2 partial、8 个 failed eval runs） |
| missing assets | 0（A0–R1 的关键文件全部真实存在，无补造） |

## 2. Historical Tags（全部 annotated）

| Tag | Commit | Experiment | Historical run |
| --- | --- | --- | --- |
| exp/recall-a0-fixed-token | b140c9db | A0 legacy fixed-token | a0-old-fixed-token-top20 |
| exp/recall-a1-atomic-raw | 8f84c648 | A1 atomic raw | a1-atomic-raw-top20 |
| exp/recall-a2-atomic-context | 4f8380d2 | A2 atomic + context | baseline-current-naive-top20 |
| exp/recall-a3-structured-envelope | cc55df85 | A3 envelope | a3-structured-envelope-top20 |
| exp/recall-b0-dense-only | e06bba9a | B0 dense-only | b0-dense-only-top20 |
| exp/recall-b1-exact-id | 2bcba866 | B1 exact-id | b1-exact-id-table-top20 |
| exp/recall-c3-table-row | 96efaf5c | C3 multi-view | c3-table-row-view-top20 |
| exp/recall-r0-c3-exact-id | 3c1ef2ca | R0 baseline | c3-row-view-exact-id-table-top20 |
| **exp/recall-r1-structured** | **202346c3** | **R1 structured ranking** | r1-structured-ranker-top20 |
| archive/recall-r1-final-tip | 2a6d6156 | R1 final branch state（audit/docs 之后） | n/a |

要点：

- **R1 tag 精确指向 202346c3**（产生历史 R1 run 的代码状态），而非 branch tip 2a6d6156。
- **A0 tag 精确指向 b140c9db**，A0 已具备「不依赖 branch 的历史复现条件」。
- 7 个普通 fix 分支未额外打 tag：原 commit + main merge commit 已保留在 Git 历史中（`no additional tag required`）。

## 3. Regression（Merge 前后）

### Merge 前（refactor/recall-experiment-config @ 1f351a0b）

```text
tests/recall_lab + tests/chunker + tests/llm/test_explicit_id_recall.py
+ tests/memory_eval/test_product_evaluation.py = 353 passed

R1 merge-gate smoke（verify-en-20p / naive / top20 / skip-kg）：
table_cell R@1/R@3/R@5 = 1.0 / 1.0 / 1.0，MRR = 1.0
7/7 table_cell gold rank = 1
```

### Merge 后（main @ b979c5c8）

```text
tests/recall_lab = 27 passed（config loader + structured rank + audit）

R1 post-merge smoke：table_cell R@1/R@3/R@5 = 1.0 / 1.0 / 1.0，MRR = 1.0
```

行为等价：merge 前后 R1 均为 100/100/100/1.0。

## 4. Main Integration

| 项目 | 值 |
| --- | --- |
| 旧 main commit | 4f8380d2 |
| Merge commit | b979c5c8（`merge: integrate config-driven recall experiment framework`） |
| 新 main commit | b979c5c8 |
| 策略 | 正常 merge（--no-ff），保留 Phase 3 全部 11 个 capability commit，未 squash |

Phase 3 capabilities（harness / config runner / multi-view / exact-id types / structured rank / audit / run metadata / figure-aware timeout）已正式进入 main。

## 5. Remaining Branches

```text
safe-to-delete（用户确认后）：refactor/recall-experiment-config、exp/recall-lab、
  recall-a0/a1/a3/b0/b1/c3/c3-exact-id/r1、7 个 fix 分支、fix/lightrag-defects（共 18）
keep：main
manual-review：memory-eval-framework（always-poll / 15s dispatch 未评审）
```

详见 `FINAL_BRANCH_CLEANUP.md`。

## 6. Remaining Risks

| 风险 | 说明 |
| --- | --- |
| memory-eval-framework | f0ea9bc0 未提取/未合并/未删除，保持 manual-review-pending |
| remote refs | `fork/memory-eval-framework`（b2b6f591）与本地（f0ea9bc0）不一致；本地 main（b979c5c8）领先 fork/main（b33c6b08）与 origin/main（2b67343b）；均未推送 |
| 归档位置 | archive 在 `/Users/sakura/RAG/LightRAG-experiment-archive/`（项目外），Phase 5 前建议确认长期存放位置与备份 |
| 远端 tag 未 push | 10 个本地 annotated tags 均未推送（按约定等待确认） |
| 历史 run 无 resolved config | 旧 run 未记录 resolved config；如需可补 `reconstructed: true` metadata，本次未伪造 |
