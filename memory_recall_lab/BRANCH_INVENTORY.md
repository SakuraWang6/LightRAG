# LightRAG 分支清单（Branch Inventory）

> 生成时间：2026-08-16 · 分析方法：只读 Git 审计（`git branch -vv` / `git log --graph --all` / `git merge-base` / `git cherry` / `git diff`），未修改、未合并、未删除任何分支。

## 1. 仓库拓扑总览

```text
origin/main (2b67343b, 上游 HKUDS)
  └─ 本地 main (4f8380d2) = origin/main + memory-eval 工作链 + 7 个本地 fix merge
       ├─ fix/data-service-realism      → tip 是 main 祖先（已合入）
       ├─ fix/eval-service-reliability  → tip 是 main 祖先（已合入）
       ├─ fix/lightrag-platform-retrieval → tip 是 main 祖先（已合入）
       ├─ fix/data-service-spacing      → tip 是 main 祖先（已合入）
       ├─ fix/table-sidecar-backfill    → tip 是 main 祖先（已合入）
       ├─ fix/explicit-id-table-regression → tip 是 main 祖先（已合入）
       ├─ fix/table-context-preservation → tip 是 main 祖先（已合入）
       │
       ├─ fb949214 (recall harness 公共基座, 不在 main)
       │    ├─ recall-a0-old-fixed-token    (直接基于 fb949214)
       │    ├─ recall-a1-atomic-raw         (直接基于 fb949214)
       │    ├─ recall-a3-structured-envelope (fb949214 → 77ee36d9 → cc55df85)
       │    ├─ 2787b5bd (docs)
       │    │    ├─ exp/recall-lab           (→ 02b81123)
       │    │    ├─ recall-b0-dense-only     (→ e06bba9a)
       │    │    ├─ recall-b1-exact-id-table (→ 2bcba866)
       │    │    └─ recall-c3-table-row-view (→ 96efaf5c)
       │    │         └─ recall-c3-table-row-view-exact-id (R0, → 3c1ef2ca)
       │    │              └─ recall-r1-structured-ranker (→ 202346c3 → 38b8cadc → 2a6d6156)
       │
       └─ 99e9967b (memory-eval 链中段)
            ├─ memory-eval-framework (→ f0ea9bc0)
            └─ fix/lightrag-defects (→ 85ed4559 → 47dfe6fa → 811f47ef → 816d2bec)
```

## 2. 分支速查表

| Branch | Base | Unique commits (vs main) | Category | Superseded by | Needed code | Recommended action |
| --- | --- | --- | --- | --- | --- | --- |
| main | origin/main + memory-eval 链 | —（本地主线） | stable | — | — | 统一基线 |
| fix/data-service-realism | main 链 | 0 | dataset | 已入 main（merge 7eeca0de） | 无独有 | already-contained |
| fix/eval-service-reliability | main 链 | 0 | evaluation | 已入 main（merge b169e157） | 无独有 | already-contained |
| fix/lightrag-platform-retrieval | main 链 | 0 | stable-capability | 已入 main（merge 99712271） | 无独有 | already-contained |
| fix/data-service-spacing | main 链 | 0 | dataset | 已入 main（merge 22715881） | 无独有 | already-contained |
| fix/table-sidecar-backfill | main 链 | 0 | stable-fix | 已入 main（merge 392a913d） | 无独有 | already-contained |
| fix/explicit-id-table-regression | main 链 | 0 | stable-fix | 已入 main（merge 96cde1c8） | 无独有 | already-contained（与 R1 存在语义冲突，留历史即可） |
| fix/table-context-preservation | main 链 | 0 | stable-fix | 已入 main（merge 4f8380d2） | 无独有 | already-contained |
| fix/lightrag-defects | 99e9967b | 4（3 个等价 + 1 个独有） | mixed | 3/4 等价入 main；47dfe6fa 独有 | 47dfe6fa | extract → delete |
| memory-eval-framework | 99e9967b | 1（f0ea9bc0） | mixed | 部分被 main 后续 UI 重做取代 | always-poll / 15s 派发 | manual-review / extract |
| exp/recall-lab | main | 3（harness 全量） | evaluation（+A2 行为） | 未合入 main | 整个 memory_recall_lab | merge/extract |
| recall-a0-old-fixed-token | fb949214 | 2 | experiment (historical) | 被 A2 及后续取代 | 无（如复现需要 legacy adapter） | tag-and-delete |
| recall-a1-atomic-raw | fb949214 | 2 | experiment | 被 multi-view 取代 | 无（转为 representation 配置） | tag-and-delete → convert-to-config |
| recall-a3-structured-envelope | fb949214 | 3 | experiment | 被 multi-view 取代 | 无（转为 representation 配置） | tag-and-delete → convert-to-config |
| recall-b0-dense-only | 2787b5bd | 3 | experiment | 被 R0/R1 吸收 | 无（转为 retrieval 配置） | tag-and-delete → convert-to-config |
| recall-b1-exact-id-table | 2787b5bd | 3 | experiment | 被 R0/R1 吸收 | 无（转为 retrieval 配置） | tag-and-delete → convert-to-config |
| recall-c3-table-row-view | 2787b5bd | 3 | experiment（含稳定能力雏形） | 被 R0/R1 覆盖 | multi-view chunker（转为 representation 配置） | convert-to-config |
| recall-c3-table-row-view-exact-id (R0) | c3 | 4 | experiment | 被 R1 取代（作为 baseline 保留） | TBL/FIG exact-id（转为 retrieval 配置） | convert-to-config |
| recall-r1-structured-ranker | R0 | 7 | experiment（当前最优，含能力） | 未 supersede | structured ranker + ranking audit | keep-temporarily → convert-to-config |

## 3. 各分支详细分析

### 3.1 已完全合入 main 的分支（7 个）

这些分支的 tip 都是 `main` 的祖先（`git merge-base main <branch>` 等于分支 tip 本身），`main..branch` 为空，**没有任何独有 commit**：

| Branch | Tip | main 中的对应 merge |
| --- | --- | --- |
| fix/data-service-realism | daac5c13 | 7eeca0de |
| fix/eval-service-reliability | 5352ea1b | b169e157 |
| fix/lightrag-platform-retrieval | 41710ee0 | 99712271（含 aa3ebf11/ced281c4/5655dc8a） |
| fix/data-service-spacing | 71f8ec59 | 22715881 |
| fix/table-sidecar-backfill | 81a52d1d | 392a913d |
| fix/explicit-id-table-regression | 8f77c227 | 96cde1c8 |
| fix/table-context-preservation | 7e9e2f77 | 4f8380d2 |

结论：这 7 个分支只保留历史价值，可在打 tag 后删除。`fix/explicit-id-table-regression` 与 R1 存在语义冲突（main 将 explicit-id 限制在稳定 FACT/EQ/REF，而 R1 重新开放 TBL/FIG），但该限制已随 main 保留，不需要再从分支取回。

### 3.2 fix/lightrag-defects（816d2bec）

- Base：99e9967b（memory-eval 链中段）。
- 4 个独有 commit（相对 main）：
  - `85ed4559` Fix table chunk integrity and KG context budget starvation —— **patch 等价于 main 中 aa3ebf11**（`git cherry` 判定已合入）。
  - `47dfe6fa` Scale ingestion timeout by figure count for VLM-heavy datasets —— **独有，main 中无等价实现**（main 的 `_ingestion_timeout_seconds` 仍为 `min(max(5400, pages*90), 28800)`，未计入 figure 数量）。
  - `811f47ef` Add explicit-id recall and extraction fidelity safeguards —— **等价于 main 中 ced281c4**。
  - `816d2bec` Make table-tail and explicit-id evidence reliably retrievable —— **等价于 main 中 5655dc8a**。
- 分类：mixed（3 个 stable-fix 已入 main；1 个 evaluation 工具修复独有）。
- 建议：把 47dfe6fa 以正常 commit 提取到整理分支（属 evaluation tooling，不进 LightRAG 核心）；其余 3 个 commit 已在 main，分支打 tag 后可删除。

### 3.3 memory-eval-framework（f0ea9bc0）

- Base：99e9967b。独有 commit 只有 `f0ea9bc0`。
- 内容：eval 前端三处改动（EvalConsole / JobsView 恒轮询、ReportDocument 常显 TOC）+ `eval_jobs.py` 派发循环 60s→15s + `llm_analysis.py` 报告精简 + `workflow.py` receipt 简化。
- 与 main 的关系：main 在该 commit 之后独立演进过同一批文件（TOC rail 重做、队列 UI、报告结构重排），`f0ea9bc0` 的 ReportDocument 改动与 main 现行 hover-reveal 设计冲突，属于 superseded；但 **always-poll 与 15s 派发循环在 main 中仍不存在**（main 是 `if (!hasActiveRuns) return` + `sleep(60)`），属于 main 缺失的小型 UX/可靠性改进。
- 注意：`fork/memory-eval-framework` 远端 ref 停在 b2b6f591，本地 f0ea9bc0 未推送。
- 建议：manual-review；如确认需要，把「队列恒轮询 + 派发间隔」作为独立小 commit 提取；其余丢弃。

### 3.4 exp/recall-lab（02b81123）

- Base：main（4f8380d2）→ fb949214 → 2787b5bd → 02b81123。
- 独有内容（相对 main）：`memory_recall_lab/` 全部（run.py 584 行、retrieval.py 219 行、server.py 198 行、static/index.html 624 行、README）+ `tests/recall_lab/`（3 个测试文件）+ `.gitignore` 2 行。
- **A2 行为**：exp/recall-lab 的 chunker 与 main 完全一致（atomic table + preceding context + title），README 明确把 exp/recall-lab 标记为 A2 臂。因此「实验基础设施」与「A2 行为」确实混在一起，但 A2 行为本身就是 main 的默认实现，无需单独保留。
- 02b81123 只追加 README 17 行（记录 A/B/C3 消融结果表）。
- 建议：merge/extract —— harness（run/retrieval/server/UI/tests）应进入统一代码基线；A2 配置用 YAML 表达即可。

### 3.5 recall 实验链（A0/A1/A3/B0/B1/C3/R0/R1）

所有分支共享同一 harness 基座，实验差异**只在两个文件**：

| 分支 | 实验差异 | 实现方式 |
| --- | --- | --- |
| recall-a0 | legacy fixed-token 分块 | `token_size.py`：`if False:` 关闭 table-aware 路径（硬编码 hack） |
| recall-a1 | atomic raw（无前文/标题） | `token_size.py`：`_table_title` 返回空、`_table_with_preceding_context` 只返回表格 |
| recall-a3 | structured envelope | `token_size.py`：每个 table piece 外包「Object Type/Table ID/Title/Columns」 |
| recall-b0 | dense-only | `operate.py`：显式置空 explicit-chunks（硬编码 hack） |
| recall-b1 | TBL/FIG exact-id | `operate.py`：`_EXPLICIT_ID_RE` 增加 `TBL\|FIG` |
| recall-c3 | table-view + row-view | `token_size.py`：`_table_views` 取代 raw 输出，保留 row-safe 切分 |
| recall-c3-exact-id (R0) | C3 + TBL/FIG exact-id | 上述两者叠加 |
| recall-r1 | structured ranker | `operate.py`：`_structured_rank` 无条件套在 `_get_vector_context` 三个返回路径上；新增 `audit_ranking.py` |

这些分支的「能力」与「开关」耦合在代码里，没有任何 YAML/配置表达实验组合。README 中 A0/A1/A2/A3/B0/B1/C3/R0/R1 的结果表即各分支对应 run 的结果。

## 4. 实验结果资产（runs）现状

### 4.1 memory_recall_lab/runs/（11 个 run，全部 gitignored）

`.gitignore` 第 69-70 行忽略 `memory_recall_lab/runs/*`，各分支只跟踪 `.gitkeep`。**所有 run 结果仅存在于本地工作区，未进入任何 commit。**

每个 run 目录包含：`run.json`（schema 3.0，含 `execution_manifest.code.git_commit`、dataset manifest/oracle sha256、参数表、runtime snapshot）、`recall_report.json`（per-question 指标 + 候选）、`ranking.json`、`report.md`、`ingestion_receipt.json`、`index_receipt.json`、`events.jsonl`、`run.log` 等。

| run | git_commit（即代码状态） | table_cell R@1/R@3/R@5 | MRR | overall R@1 |
| --- | --- | --- | --- | --- |
| a0-old-fixed-token-top20 | b140c9db | 57.1 / 85.7 / 85.7 % | 0.729 | 55.4 % |
| a1-atomic-raw-top20 | 8f84c648 | 0 / 0 / 0 % | 0.105 | 29.2 % |
| a3-structured-envelope-top20.partial | 77ee36d9 | failed（partial） | — | — |
| a3-structured-envelope-top20 | cc55df85 | 0 / 14.3 / 14.3 % | 0.194 | 28.5 % |
| b0-dense-only-top20 | e06bba9a | 0 / 28.6 / 57.1 % | 0.222 | 32.3 % |
| b1-exact-id-table-top20 | 2bcba866 | 0 / 28.6 / 100 % | 0.283 | 33.8 % |
| baseline-current-naive-top20.partial | 4f8380d2 | running（未完成） | — | — |
| baseline-current-naive-top20（=A2 基线） | 4f8380d2 | 0 / 28.6 / 57.1 % | 0.222 | 32.3 % |
| c3-table-row-view-top20 | 96efaf5c | 14.3 / 14.3 / 14.3 % | 0.265 | 30.0 % |
| c3-row-view-exact-id-table-top20 (R0) | 3c1ef2ca | 14.3 / 85.7 / 100 % | 0.481 | 30.0 % |
| r1-structured-ranker-top20 | 202346c3 | **100 / 100 / 100 %** | **1.000** | 40.0 % |

要点：

- `git_commit` 与实验分支 tip 精确对应（r1 对应 202346c3，即 structured ranker 代码 commit，而非最后两个文档 commit）。
- run.json **没有记录 branch、dirty status 和 resolved config**（`git_dirty` 字段缺失，无 capability 级配置）。重构后需要补齐。
- R1 的 `ranking_audit.json` / `ranking_audit.md` 在 c3-exact-id 与 r1 两个 run 目录中存在（由 audit_ranking.py 生成）。
- 数据集为 verify-en-20p（本地 `memory_data_service/generated/verify-en-20p`，gitignored）。

### 4.2 memory_eval_tests/runs/（27 个 run，全部 gitignored）

同样被 `.gitignore` 忽略（第 67-68 行），只跟踪 `.gitkeep`。run.json 记录了 `execution_manifest.code.git_commit`，可映射到 memory-eval 链 / fix/lightrag-defects / main 的 commit：

- 2026-08-12 之前的 run：9294b468、c3eb0270、4a745980、e5adbf62、3a8ee117、b110f917（memory-eval 早期链）。
- 2026-08-13：5513f649、b2b6f591、99e9967b。
- 2026-08-14：811f47ef（fix/lightrag-defects 分支）、f0ea9bc0（memory-eval-framework 分支）。
- 2026-08-15 之后：392a913d、816d2bec（fix/lightrag-defects）、22715881、96cde1c8、4f8380d2（main 链）。

即部分重要 eval 结果跑在 fix/lightrag-defects 与 memory-eval-framework 分支上；这两条分支删除前必须先归档这些 run 目录。

## 5. 关键结论

1. main 已包含全部 7 个 fix 分支 + fix/lightrag-defects 的 3/4 等价 commit，**没有任何关键 correctness fix 丢失**。
2. 唯一仍不在 main 的稳定能力类代码：`memory_recall_lab` harness（exp/recall-lab）、C3 multi-view chunker、R1 structured ranker/audit、47dfe6fa timeout 修复、memory-eval-framework 的 always-poll 细节。
3. 8 个 recall 实验分支本质是「同一套 harness + 单个文件上的实验开关」，是最典型的 convert-to-config 对象。
4. 所有实验 run 结果都是 gitignored 的本地文件，删除任何分支前必须先行归档。
5. `fork/memory-eval-framework`（b2b6f591）与本地 memory-eval-framework（f0ea9bc0）不一致，说明本地部分 commit 未推送，归档时以本地为准。
