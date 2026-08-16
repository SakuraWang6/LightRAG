# Phase 5A Local Branch Cleanup Report

> 生成时间：2026-08-17 · 操作范围：仅本地 branch 删除；无 remote push、无 tag 修改、无代码修改。

## 1. Preflight

| 项目 | 值 |
| --- | --- |
| 操作前 current branch | main |
| 操作前 main HEAD | b7879b69（文档修正后 bb0392e8；Phase 5A 报告提交后 09119852；Phase 5B 报告提交后 2daaa40b） |
| git dirty status | 干净（0 变更） |
| Archive 存在 | ✅ `/Users/sakura/RAG/LightRAG-experiment-archive/` + tar 均存在 |
| Archive tar SHA256 | `fcb43bfde22b192bc1f8714d0326a5f6856e2a977c17b9a9e6786b2a099b4405` ✅ |
| archive_manifest.json SHA256 | `0be2b3183edc762141f6d36fd4398ae93676c2d98275b3af02703439d6e493c7` ✅ |
| 10 个 annotated tags | 全部存在且指向正确 commit（见 §4） |

## 2. Deleted Branches（18 个）

| Branch | Delete mode | Reason | Historical preservation |
| --- | --- | --- | --- |
| fix/data-service-realism | `-d` | 已合入 main（merge 7eeca0de） | merge commit 保留 |
| fix/data-service-spacing | `-d` | 已合入 main（merge 22715881） | merge commit 保留 |
| fix/eval-service-reliability | `-d` | 已合入 main（merge b169e157） | merge commit 保留 |
| fix/lightrag-platform-retrieval | `-d` | 已合入 main（merge 99712271） | merge commit 保留 |
| fix/explicit-id-table-regression | `-d` | 已合入 main（merge 96cde1c8） | merge commit 保留 |
| fix/table-sidecar-backfill | `-d` | 已合入 main（merge 392a913d） | merge commit 保留 |
| fix/table-context-preservation | `-d` | 已合入 main（merge 4f8380d2） | merge commit 保留 |
| fix/lightrag-defects | `-D after verification` | `-d` 拒绝（non-merged）；85ed4559/811f47ef/816d2bec 等价已入 main，47dfe6fa 内容等价迁移为 b5c7fad6（main workflow.py 含 `_dataset_figure_count` + 43200 cap） | 提取 commit b5c7fad6 + main 历史 |
| recall-a0-old-fixed-token | `-D after verification` | `-d` 拒绝（historical-only）；tag `exp/recall-a0-fixed-token`→b140c9db 验证通过、A0 run 已归档、a0 配置在 main | tag + archive + a0_fixed_token.yaml |
| recall-a1-atomic-raw | `-D after verification` | `-d` 拒绝；tag→8f84c648、archive、config 均验证 | tag + archive + a1_atomic_raw.yaml |
| recall-a3-structured-envelope | `-D after verification` | 同上（tag→cc55df85） | tag + archive + a3_structured_envelope.yaml |
| recall-b0-dense-only | `-D after verification` | 同上（tag→e06bba9a） | tag + archive + b0_dense_only.yaml |
| recall-b1-exact-id-table | `-D after verification` | 同上（tag→2bcba866） | tag + archive + b1_exact_id.yaml |
| recall-c3-table-row-view | `-D after verification` | 同上（tag→96efaf5c） | tag + archive + c3_table_row_view.yaml |
| recall-c3-table-row-view-exact-id | `-D after verification` | 同上（tag→3c1ef2ca） | tag + archive + r0_c3_exact_id.yaml |
| recall-r1-structured-ranker | `-D after verification` | `-d` 拒绝；`exp/recall-r1-structured`→202346c3 与 `archive/recall-r1-final-tip`→2a6d6156 均验证通过、R1 run 已归档、r1 配置在 main | 2 个 tag + archive + r1_structured_ranker.yaml |
| exp/recall-lab | `-D after verification` | `-d` 拒绝；harness/UI/README/tests 全部已在 main，A2 由 a2 配置表达 | main 内 harness + tag exp/recall-a2-atomic-context |
| refactor/recall-experiment-config | `-d` | 已完整合入 main（merge b979c5c8） | merge commit + 11 个 capability commit 保留 |

> 说明：6 个 recall 中间分支的删除前验证脚本最初用短 hash 与 `git rev-parse tag^{}` 的完整 hash 比较，产生误报 MISMATCH；随后用完整 40 位 hash 复验全部 OK（见 §4）。该误报不影响删除安全性（删除前全局完整 hash 校验已通过，且 archive/config 均验证存在）。

## 3. Retained Branches

| Branch | 保留原因 |
| --- | --- |
| main | 统一基线（HEAD 2daaa40b，含 integration merge b979c5c8） |
| memory-eval-framework | 含未评审独有行为（f0ea9bc0：always-poll / 15s dispatch），manual-review-pending |

## 4. Historical Tags Verification（删除后）

| Tag | Target commit | Status |
| --- | --- | --- |
| exp/recall-a0-fixed-token | b140c9db… | ✅ |
| exp/recall-a1-atomic-raw | 8f84c648… | ✅ |
| exp/recall-a2-atomic-context | 4f8380d2… | ✅ |
| exp/recall-a3-structured-envelope | cc55df85… | ✅ |
| exp/recall-b0-dense-only | e06bba9a… | ✅ |
| exp/recall-b1-exact-id | 2bcba866… | ✅ |
| exp/recall-c3-table-row | 96efaf5c… | ✅ |
| exp/recall-r0-c3-exact-id | 3c1ef2ca… | ✅ |
| exp/recall-r1-structured | 202346c3… | ✅ |
| archive/recall-r1-final-tip | 2a6d6156… | ✅ |

## 5. Archive Verification

```text
archive path : /Users/sakura/RAG/LightRAG-experiment-archive/
tar          : /Users/sakura/RAG/LightRAG-experiment-archive-2026-08-17.tar.gz
tar SHA256   : fcb43bfde22b192bc1f8714d0326a5f6856e2a977c17b9a9e6786b2a099b4405
manifest SHA256: 0be2b3183edc762141f6d36fd4398ae93676c2d98275b3af02703439d6e493c7
status       : source↔archive 全树一致；124 关键文件 0 mismatch；SHA256SUMS 124/124
```

## 6. Main Verification

```text
current main HEAD : 2daaa40b（phase 5b report commit；integration merge b979c5c8）
9 个 configs/*.yaml : 全部存在
lightrag/ranking/structured.py : 存在
memory_recall_lab/config.py : 存在
memory_recall_lab/audit/ranking.py : 存在
tests（删除后）: tests/recall_lab + tests/llm/test_explicit_id_recall.py = 37 passed
git status : 干净
```

## 7. Remote Status

```text
No remote push performed.
No remote branch deleted.
No tag pushed.

origin/main                 = 2b67343b
fork/main                   = b33c6b08
fork/memory-eval-framework  = b2b6f591
```

本地 main（2daaa40b）领先 origin/main；已与 fork/main 同步；本地 memory-eval-framework（f0ea9bc0）与 fork/memory-eval-framework（b2b6f591）不一致。本阶段未修改任何远端 ref。
