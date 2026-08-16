# Recall 实验体系迁移报告（Phase 3）

> 日期：2026-08-17 · 分支：`refactor/recall-experiment-config`（基于 main 4f8380d2）
> 目标：把已验证的 Recall 实验能力从 Git 分支迁移为「统一代码能力 + YAML 配置 + 统一运行器 + 可复现 Run Metadata」。本阶段未删除任何分支/tag，未 force push，未修改 main。

## 1. 提取了哪些能力

| 能力 | 状态 |
| --- | --- |
| recall-only evaluation harness（run/retrieval/server/UI/tests） | ✅ 已并入统一基线 |
| config infrastructure（YAML 加载/校验/resolved/CLI override） | ✅ 已建立 |
| multi-view（table-view / row-view）可配置表示 | ✅ 已配置化 |
| exact-id identifier 类型可配置（FACT/EQ/REF/TBL/FIG） | ✅ 已配置化 |
| structured rank（R1 ranking strategy） | ✅ 已从 operate.py 解耦 |
| ranking audit | ✅ 已移到实验工具层 |
| run metadata（branch / git_dirty / resolved config） | ✅ 已记录 |
| figure-aware ingestion timeout（47dfe6fa） | ✅ 已单独提取 |

## 2. 每项能力来源

| Capability | Source branch / commit | New location | Config |
| --- | --- | --- | --- |
| recall harness | exp/recall-lab（fb949214/2787b5bd/02b81123） | memory_recall_lab/{run,retrieval,server,static,README}.py + tests/recall_lab | — |
| config loader | 新建（Phase 3.2） | memory_recall_lab/config.py | YAML schema |
| table-view / row-view | recall-c3-table-row-view（96efaf5c） | lightrag/chunker/token_size.py `_table_views`（env 开关，默认关） | representation.table.table_view / row_view |
| structured envelope | recall-a3-structured-envelope（77ee36d9/cc55df85） | lightrag/chunker/token_size.py `_table_envelope`（env 开关，默认关） | representation.table.structured_envelope |
| preceding-context 开关（A1） | recall-a1-atomic-raw（8f84c648） | lightrag/chunker/token_size.py（env 开关，默认开） | chunking.table.preceding_context |
| TBL/FIG exact-id | recall-b1-exact-id-table（2bcba866）/ c3-exact-id（3c1ef2ca） | lightrag/operate.py `_explicit_id_re()`（env 类型列表） | retrieval.exact_id.types |
| structured rank | recall-r1-structured-ranker（202346c3） | lightrag/ranking/structured.py + operate.py strategy hook | ranking.strategy=structured |
| ranking audit | recall-r1-structured-ranker（38b8cadc） | memory_recall_lab/audit/ranking.py | evaluation.save_ranking_audit |
| run metadata 扩展 | 新建（Phase 3.7） | memory_eval_tests/artifacts.py（branch/dirty，向后兼容） | run.json code/experiment 段 |
| figure-aware timeout | fix/lightrag-defects（47dfe6fa） | memory_eval_tests/workflow.py | — |

## 3. 历史实验映射

| 历史实验 | 历史 branch / commit | 新配置 | 说明 |
| --- | --- | --- | --- |
| A0 | recall-a0-old-fixed-token（b140c9db） | configs/a0_fixed_token.yaml | `historical: true, legacy_mode: true, reproducible_from_current_code: false`；runner 拒绝执行，指向 git commit b140c9db |
| A1 | recall-a1-atomic-raw（8f84c648） | configs/a1_atomic_raw.yaml | raw + 无 preceding context |
| A2 | exp/recall-lab（4f8380d2 基线） | configs/a2_atomic_context.yaml | = main 默认行为 |
| A3 | recall-a3-structured-envelope（cc55df85） | configs/a3_structured_envelope.yaml | structured envelope |
| B0 | recall-b0-dense-only（e06bba9a） | configs/b0_dense_only.yaml | exact_id disabled |
| B1 | recall-b1-exact-id-table（2bcba866） | configs/b1_exact_id.yaml | + TBL/FIG |
| C3 | recall-c3-table-row-view（96efaf5c） | configs/c3_table_row_view.yaml | table view + row view |
| R0 | recall-c3-table-row-view-exact-id（3c1ef2ca） | configs/r0_c3_exact_id.yaml | C3 + 全量 exact-id，无 structured rank |
| R1 | recall-r1-structured-ranker（202346c3） | configs/r1_structured_ranker.yaml | C3 + 全量 exact-id + structured rank |

所有配置均在 `memory_recall_lab/configs/` 下，由 `load_config()` 严格校验；实验语义由 capability 字段表达，无任何 `if experiment == "R1"` 式代码。

## 4. Regression Results

统一条件：`verify-en-20p`、`naive`、`top_k=20`、`chunk_top_k=20`、`skip_kg=true`、embedding `bge-m3`。新旧 run 均使用独立隔离工作区。

| 实验 | 历史 table_cell R@1/R@3/R@5 / MRR | 新配置 run 同指标 | 逐题 gold rank | table_cell top-10 候选 ID |
| --- | --- | --- | --- | --- |
| A1 | 0 / 0 / 0 / 0.1054 | 0 / 0 / 0 / 0.1054 | 全等 | 全等 |
| A2 | 0 / 28.6 / 57.1 / 0.2216 | 0 / 28.6 / 57.1 / 0.2216 | 全等 | —（smoke） |
| A3 | 0 / 14.3 / 14.3 / 0.1939 | 0 / 14.3 / 14.3 / 0.1939 | 全等 | —（smoke） |
| B0 | 0 / 28.6 / 57.1 / 0.2216 | 0 / 28.6 / 57.1 / 0.2216 | 全等 | —（smoke） |
| B1 | 0 / 28.6 / 100 / 0.2833 | 0 / 28.6 / 100 / 0.2833 | 全等 | —（smoke） |
| C3 | 14.3 / 14.3 / 14.3 / 0.2653 | 14.3 / 14.3 / 14.3 / 0.2653 | 全等 | 全等 |
| R0 | 14.3 / 85.7 / 100 / 0.4810 | 14.3 / 85.7 / 100 / 0.4810 | 全等 | 全等 |
| R1 | **100 / 100 / 100 / 1.000** | **100 / 100 / 100 / 1.000** | 全等 | 全等 |

对比方法：对每个 run 对，按 question_id 对齐 `recall_report.json`，比较全量题目的 `gold_rank_by_fact` 排序结果，以及 table_cell 题目的前 10 个候选 `chunk_id`。A1/C3/R0/R1 全部一致；A2/A3/B0/B1 逐题 gold rank 全部一致。结论：**行为等价，而非仅汇总指标碰巧相同**。

### R1 硬门禁

新架构 `r1_structured_ranker.yaml` 复现：

```text
table_cell cases = 7
Recall@1 = 100%  Recall@3 = 100%  Recall@5 = 100%  MRR = 1.0
overall R@1/R@3/R@5 = 0.4 / 0.7308 / 0.9
```

与历史 run（git_commit 202346c3）逐项一致，且 7 道 table_cell 题的 gold rank 全部为 1、top-10 候选 ID 完全相同。ranking_audit.json / ranking_audit.md 已随 run 生成。

## 5. 测试结果

直接相关套件全部通过：

| 套件 | 结果 |
| --- | --- |
| tests/recall_lab（harness + config + structured rank + audit） | 27 passed |
| tests/chunker（含新增 multi-view 测试） | 299 passed |
| tests/llm/test_explicit_id_recall.py（含新增类型配置测试） | 10 passed |
| tests/memory_eval/test_product_evaluation.py（含新增 figure-timeout 测试） | 17 passed |
| 全量 tests（排除 49 个可选依赖模块） | 4485 passed / 407 failed（失败全部为基线环境问题：spaCy 模型未装、setup 向导交互、pgtable/neo4j/milvus/opensearch/redis 缺驱动、voyageai/bedrock 缺凭证、网络类 parser 测试；已在 main 4f8380d2 独立 worktree 复测确认同批失败） |

## 6. Remaining Risks

| 风险 | 状态 |
| --- | --- |
| A0 legacy fixed-token 复现 | 采用历史 commit/tag 方案，未写回当前 chunker；`a0_fixed_token.yaml` 为文档性配置，runner 拒绝执行 |
| memory-eval-framework polling（always-poll / 15s dispatch） | 未提取，保持 `manual review pending`，作为独立后续任务 |
| 历史 run 归档 | `memory_recall_lab/runs/`（11 个）与 `memory_eval_tests/runs/`（27 个）均为 gitignored 本地资产，branch cleanup 前必须先归档；另有 `/Users/sakura/RAG/memory_recall_lab/runs/` 的 isolated 存储副本可作补充 |
| R1 依赖 row-view marker | structured ranker 依赖 view chunk 的 `Object Type: Table Row` 标记；若未来关闭 multi-view，structured strategy 将退化为仅 FACT tier（配置校验已强制 structured 需要 table_view 或 row_view） |
| 旧 run 无 resolved config | 历史 run 未记录 resolved config；本次迁移未修改旧 run，需要时以 `reconstructed: true` 补 metadata |

## 7. 下一步允许删除的 branch candidate（仅建议，未执行）

在完成历史 run 归档、为历史 milestone 打 annotated tag、并经人工确认后，可按 BRANCH_CLEANUP_PLAN.md 执行：

- 已完全合入 main 的 7 个 fix 分支（data-service-realism / data-service-spacing / eval-service-reliability / lightrag-platform-retrieval / explicit-id-table-regression / table-sidecar-backfill / table-context-preservation）→ `already-contained`
- 8 个 recall 实验分支（a0/a1/a3/b0/b1/c3/c3-exact-id/r1）→ `exp/recall-*` tag 后删除
- fix/lightrag-defects → 提取 47dfe6fa 后删除（其余 3 commit 已在 main）
- memory-eval-framework → 人工评审 f0ea9bc0 后再定
- exp/recall-lab → harness 已并入统一基线后，tag 后删除

**本阶段未执行任何删除/打 tag 操作。**

## 8. 本次实际修改的文件与 commit

```text
2aafa9cd docs: add recall experiment consolidation audit and plan
3b293928 feat(recall-lab): add retrieval-only evaluation harness
4844cdbe feat(recall-lab): add config-driven experiment runner
7687ff4d feat(retrieval): add configurable table multi-view representation
9206c3c5 feat(retrieval): make explicit identifier types configurable
753ea6c2 feat(ranking): add configurable structured ranking strategy
c676134b feat(recall-lab): add remaining historical experiment configs
b5c7fad6 fix(eval): scale ingestion timeout for VLM-heavy datasets
87e8d4ce docs(recall-lab): document config-driven experiment usage
```

修改的核心文件：`memory_recall_lab/{config.py, run.py, retrieval.py, server.py, audit/ranking.py, configs/*.yaml, README.md}`、`lightrag/chunker/token_size.py`、`lightrag/operate.py`、`lightrag/ranking/{__init__,structured}.py`、`memory_eval_tests/{artifacts.py, workflow.py}`、`tests/recall_lab/*`、`tests/chunker/test_table_multi_view_chunks.py`、`tests/llm/test_explicit_id_recall.py`、`tests/memory_eval/test_product_evaluation.py`、`.gitignore`。

新增回归 run（gitignored，本地保留）：`memory_recall_lab/runs/*.config-v1/`（A1/A2/A3/B0/B1/C3/R0/R1 共 8 个）。
