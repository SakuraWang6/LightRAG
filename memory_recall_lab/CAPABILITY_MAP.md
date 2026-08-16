# 能力地图（Capability Map）

> 目的：把「代码能力」与「Git 分支/实验」解耦。能力是否存在由代码决定；能力是否启用由配置决定；实验用什么组合由 resolved config 决定。

## 1. Ingestion / Evidence Integrity（证据完整性与入库可靠性）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| atomic table chunking（表格保持原子 chunk） | fix/lightrag-platform-retrieval 链（aa3ebf11 ≡ 85ed4559） | main token_size.py table-aware path | ✅ | tokenizer | 否（默认行为） | ✅ |
| long-table row-safe split（按 JSON row 边界切分，非 token 窗口） | aa3ebf11 / 85ed4559 | main `_split_table_pieces` | ✅ | tokenizer | 否（默认行为） | ✅ |
| table-tail handling（切分尾部 piece 保留标题） | 816d2bec ≡ 5655dc8a | main `_table_title` 复制标题到每个 piece | ✅ | tokenizer | 否（默认行为） | ✅ |
| sidecar backfill（切分后 chunk 的 sidecar 回填） | 81a52d1d（fix/table-sidecar-backfill） | main | ✅ | table id 解析 | 否（默认行为） | ✅ |
| small-table preceding context（小表格保留前文 context = A2 行为） | 7e9e2f77（fix/table-context-preservation） | main `_table_with_preceding_context` | ✅ | tokenizer | ✅（representation.context 开关；当前默认开启） | ✅ |
| chunk integrity + KG context budget starvation 修复 | aa3ebf11 / 85ed4559 | main | ✅ | — | 否 | ✅ |

## 2. Retrieval Representation（检索表示）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| raw atomic table（A1：纯 JSON 表格，无上下文） | recall-a1-atomic-raw (8f84c648) | 实验分支 | ❌ | — | ✅（representation.table.raw） | 否 |
| preceding-context atomic table（A2：默认） | main 默认（7e9e2f77 增强） | main | ✅ | tokenizer | ✅（representation.table.preceding_context） | ✅（默认） |
| structured envelope（A3：Object Type/Table ID/Title/Columns 外壳） | recall-a3-structured-envelope (77ee36d9) | 实验分支 | ❌ | tokenizer | ✅（representation.table.structured_envelope） | 否 |
| table-view（C3：表级摘要视图） | recall-c3-table-row-view (96efaf5c) | recall-c3 / r1 | ❌ | `_table_views` | ✅（representation.table.table_view） | 否（R1 后建议默认开启） |
| row-view（C3：每行独立视图，含 cell-group 超长行切分） | recall-c3-table-row-view (96efaf5c) | recall-c3 / r1 | ❌ | `_table_views` | ✅（representation.table.row_view） | 否（R1 后建议默认开启） |
| Evidence Object ≠ Retrieval Representation 原则 | recall-c3 README | r1 | ❌（理念未代码化） | — | ✅ | ✅（架构原则） |

## 3. Candidate Retrieval（候选召回）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| dense retrieval | LightRAG 基础 | main | ✅ | vector storage | ✅（top_k/chunk_top_k/cosine threshold） | ✅ |
| explicit FACT/EQ/REF exact-id recall | fix/lightrag-platform-retrieval (ced281c4 ≡ 811f47ef) | main（fix/explicit-id-table-regression 限制到稳定 identifier） | ✅ | `_EXPLICIT_ID_RE`、vector + KV `search_values` | ✅（retrieval.exact_id.types） | ✅（默认 FACT/EQ/REF） |
| explicit-id 精确文本校验（KV search_values 兜底） | 41710ee0 | main | ✅ | text_chunks_db | 否 | ✅ |
| TBL/FIG exact-id recall | recall-b1-exact-id-table (2bcba866) | recall-r1（regex 扩展） | ❌ | `_EXPLICIT_ID_RE` | ✅（retrieval.exact_id.types += [TBL, FIG]） | 否（R1 建议默认开启） |
| content search（按 identifier 查 chunk 内容） | 41710ee0 | main | ✅ | KV backend | 否（内部机制） | ✅ |
| explicit-id 结果去重/前置（explicit + dense 合并） | ced281c4 链 | main（无 structured rank 版本）/ r1 | ✅（合并逻辑）/ ❌（r1 排名） | — | ✅ | ✅ |

## 4. Ranking（排序）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| vector similarity 排序（基础） | LightRAG 基础 | main | ✅ | embedding | ✅ | ✅ |
| built-in rerank（cross-encoder 等） | LightRAG 基础 | main | ✅ | rerank provider | ✅（enable_rerank） | 按配置 |
| structured rank（R1：FACT 命中 > TBL+row-view > TBL+table-view/raw > 其他；同层 lexical overlap） | recall-r1-structured-ranker (202346c3) | recall-r1 `_structured_rank` | ❌（硬编码在 operate.py） | `_structured_ids` / `_structured_candidate_type` / `_structured_overlap` | ✅（ranking.strategy=structured；当前无开关） | 否（默认应关，实验开启） |
| ranking audit（错误分类：同表错行/候选生成失败等） | recall-r1 (38b8cadc) | memory_recall_lab/audit_ranking.py | ❌ | recall_report.json | ✅（独立 CLI） | 实验工具 |

## 5. Evaluation（测评）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| memory_eval_tests 产品测评框架（端到端 ingest/retrieval/QA/归因） | memory-eval 链早期 | main | ✅ | memory_data_service | ✅（CLI/runner） | ✅ |
| eval reliability / observability 修复 | 5352ea1b | main | ✅ | — | 否 | ✅ |
| ingestion timeout 按页面缩放 | memory-eval 链（5513f649 起） | main（`pages*90`，上限 28800s） | ✅ | — | ✅（extra.ingestion_timeout_seconds） | ✅ |
| ingestion timeout 按 figure 数量缩放（VLM-heavy） | fix/lightrag-defects (47dfe6fa) | **仅 fix/lightrag-defects** | ❌ | manifest 解析 | ✅（extra.ingestion_timeout_seconds 优先） | 建议（缺失，需提取） |
| memory_recall_lab recall-only harness（run.py） | exp/recall-lab (fb949214) | recall-r1 | ❌ | memory_eval_tests 子模块 | ✅（--mode/--top-k/--skip-kg） | 实验工具 |
| Recall@1/3/5、MRR、mean-fact-MRR、gold-rank 分布 | fb949214 | recall-r1 retrieval.py | ❌ | memory_recall_lab | ✅ | 实验工具 |
| ranking.json（UI 友好 schema） | fb949214 | recall-r1 | ❌ | run 输出 | ✅ | 实验工具 |
| 本地对比 UI（server.py + static/） | fb949214 | recall-r1 | ❌ | runs 目录 | ✅ | 实验工具 |
| ranking audit（audit_ranking.py + ranking_audit.md/json） | recall-r1 (38b8cadc) | recall-r1 | ❌ | recall_report.json | ✅ | 实验工具 |
| eval 前端恒轮询 / 队列自动刷新 / 15s 派发 | memory-eval-framework (f0ea9bc0) | **仅 memory-eval-framework** | ❌（main 为条件轮询 + 60s） | WebUI | 否 | 待人工判断 |

## 6. Dataset / Benchmark（数据集）

| Capability | First introduced | Current best | In main? | Dependencies | Configurable? | Always-on? |
| --- | --- | --- | --- | --- | --- | --- |
| memory_data_service 合成数据集生成（manifest/facts/questions/objects/oracle） | memory-eval 链早期 | main | ✅ | schemas/storage/generators | ✅（CLI/API） | ✅ |
| 中文数据集 realism / 消歧能力 | daac5c13（fix/data-service-realism） | main | ✅ | chinese_docx_generator | ✅ | ✅ |
| 中文 multihop 表格 anchor 空格修复 | 71f8ec59（fix/data-service-spacing） | main | ✅ | generator | 否 | ✅ |
| rich document generation（rich-docx-v2，含表格/图片/公式/引用） | befff57d 起 | main | ✅ | generators | ✅ | ✅ |
| verify-en/zh-5p/20p 受控数据集 | 2026-08-15 起 | main 链 | ✅（代码）/ ❌（生成物 gitignored） | memory_data_service | ✅ | ✅ |

## 7. 缺失/需要提取的能力清单（不在 main）

| 能力 | 唯一来源 | 建议去向 |
| --- | --- | --- |
| recall-only harness（run/retrieval/server/UI/tests） | exp/recall-lab / recall 链 | memory_recall_lab 进入统一基线（Phase 3） |
| table-view / row-view representation | recall-c3 / r1 | lightrag chunker 可配置视图（representation.table.*） |
| TBL/FIG exact-id | recall-b1 / c3-exact-id / r1 | retrieval.exact_id.types 配置项 |
| structured ranker | recall-r1 operate.py | ranking strategy（默认关闭或独立模块） |
| ranking audit | recall-r1 | memory_recall_lab/audit/ |
| ingestion timeout 按 figure 缩放 | fix/lightrag-defects 47dfe6fa | memory_eval_tests/workflow.py（稳定 evaluation 修复） |
| eval 队列恒轮询/15s 派发 | memory-eval-framework f0ea9bc0 | 人工确认后提取小 commit |
