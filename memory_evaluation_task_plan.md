# LightRAG Memory Evaluation 任务计划书

## 1. 背景与目标

本任务的目标是评估 LightRAG 是否适合作为“超大单富文本文档”的底层 Document Memory / Agentic Retrieval 基线。

核心应用场景：

- 输入是一份很长的单文档，例如论文、技术文档、白皮书、标准规范、系统设计说明书。
- 文档包含复杂富文本结构：章节层级、段落、表格、图片、caption、公式、交叉引用、页眉页脚、脚注、引用、附录、术语表等。
- 期望系统能回答问题，并且满足：
  - 答案准确。
  - 能溯源到文档证据。
  - 能处理多跳检索和跨章节推理。
  - 能识别“不存在于文档中”的问题并拒答。
  - 能评估 LightRAG 的 Document Representation 是否接近 Document Memory，而不是只停留在 Vector Retrieval。

当前结论：

离线评估闭环已经从 skeleton 升级到可复现多规模 baseline：数据服务能生成带 object graph oracle 的 rich DOCX，评估框架能一键运行 integrity、native parser sidecar、layout/cross-reference audit、object traceability、chunk traceability、sidecar retrieval baseline 和 performance audit。当前已验证历史 12/20/200/1000 页，3000 页 stress 已完成结构全量与 evidence/retrieval 抽样验证；新增严格复杂版面审计后，最新 12 页 smoke 已暴露 LightRAG native DOCX parser 未抽取 VML floating textbox 文本的问题。

真实在线链路已完成多轮 DOCX smoke：远端 OpenAI-compatible LLM + 本地 `bge-m3` embedding 已跑通过一次带 KG 的基线；随后切换为本地 Ollama 轻量链路。当前稳定本地配置为 `qwen3:8b` query/keyword/extract、`gemma3:4b` VLM、`bge-m3:latest` embedding。`qwen3:8b` skip-KG 已完成：Evidence Recall@5=0.9412，MRR=0.9412，answer accuracy=0.8611，citation accuracy=0.9444，hallucination rate=0.1667。`qwen3:8b` full-KG 在 900s role timeout 下也已完成 ingest 与评估：KG ingest 耗时约 3752s，Evidence Recall@5=1.0，MRR=1.0，但 answer accuracy 降至 0.8056，groundedness=0.75，hallucination rate=0.25。初步结论是 KG 提升了证据召回，但在当前本地 8B + mix 生成配置下会引入更多近邻事实干扰，答案质量未必提升。仍未覆盖 Docling/MinerU、稳定 PDF 路径、200/1000/3000 页在线性能评估。

## 2. 当前目录与职责

### 2.1 数据生成服务

目录：

```text
memory_data_service/
```

职责：

- 生成 synthetic rich documents。
- 生成 oracle ground truth。
- 生成 manifest、facts、questions。
- 通过 CLI 或 FastAPI 提供数据集。

已经实现：

- `memory_data_service/app.py`
  - FastAPI 服务。
- `memory_data_service/cli.py`
  - 命令行入口。
- `memory_data_service/schemas.py`
  - 数据模型。
- `memory_data_service/generators/docx_generator.py`
  - 第一版 DOCX 生成器。
- `memory_data_service/generators/rich_docx_generator.py`
  - 第二版 rich DOCX 生成器，包含 object graph oracle。

### 2.2 评估测试框架

目录：

```text
memory_eval_tests/
```

职责：

- 只消费数据服务生成的数据。
- 不直接生成文档。
- 对 parser、sidecar、index、retrieval、answer、traceability、performance 进行评估。

已经实现：

- `dataset_client.py`
  - 从本地目录或 HTTP API 读取 manifest/oracle/files。
- `integrity.py`
  - 校验 manifest、oracle、facts、questions 的 schema 和一致性。
- `sidecar_audit.py`
  - 调用 `python -m lightrag.parser.cli`，审计 LightRAG parser sidecar。
- `layout_audit.py`
  - 审计 parser sidecar 的 position/layout 覆盖率与对象到 positioned block 的桥接。
- `cross_reference_audit.py`
  - 审计 DOCX REF field、bookmark、sidecar/chunk 中交叉引用文本的保留率。
- `index_runner.py`
  - 预留 LightRAG API 文档导入入口。
- `retrieval_eval.py`
  - 预留 retrieval 指标计算入口。
- `answer_eval.py`
  - 预留 answer/citation 指标计算入口。
- `report.py`
  - 支持 Markdown、JSON、CSV 报告输出。
- `offline_runner.py`
  - 一键运行离线评估套件，并输出 JSON/Markdown 报告。
- `readiness_report.py`
  - 基于已有评估 JSON 生成 Document Memory readiness 可读结论。
- `comparison_report.py`
  - 汇总多个 parser/retrieval/answer JSON 报告，生成 parser/mode 对比表。
- `api_preflight.py`
  - 检查 LightRAG API、Ollama、LLM/embedding 环境变量是否满足在线评估前置条件。

### 2.3 环境文件

已经实现：

```text
memory_eval_env.yml
```

环境名：

```text
lightrag-memory-eval
```

推荐运行方式：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval <command>
```

## 3. 当前已经完成的工作

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Conda 环境 | 已完成 | 已创建 `lightrag-memory-eval`，并安装 LightRAG editable 依赖。 |
| 环境锁定文件 | 已完成 | 已新增 `memory_eval_env.yml`。 |
| 数据服务目录 | 已完成 | 已新增 `memory_data_service/`。 |
| FastAPI 数据服务 | 已完成第一版 | 已实现 dataset 创建、列表、manifest、oracle、文件下载接口。 |
| CLI 数据生成 | 已完成第一版 | 支持 `generate`、`list`、`serve`。 |
| DOCX 数据生成 | 已完成增强版 | 新增 `rich` profile，能生成章节树、TOC/updateFields、页眉页脚、合并表头表格、跨页长表格、信息图、caption、OMML 公式、脚注/尾注、REF/CITATION/BIBLIOGRAPHY 字段、双栏 section、VML floating textbox、引用段落、术语表、附录、干扰事实。 |
| PDF 转换 | 部分完成 | 已接入 LibreOffice，但本机 `/opt/homebrew/bin/soffice` 崩溃，PDF 被标记为 skipped。 |
| Oracle 生成 | 已完成第二版 | 已生成 `facts.json`、`questions.json`、`objects.json`、`relations.json`、`oracle.json`，oracle 已包含第一版 document object graph。 |
| 数据完整性测试 | 已完成第一版 | 能校验 manifest/oracle/facts/questions 一致性。 |
| LightRAG sidecar 审计 | 已完成第二版 | 能调用 native parser，统计 blocks/tables/drawings/equations/positions，并逐项验证 sidecar refs 与 drawing assets。 |
| 报告输出 | 已完成第一版 | 支持 Markdown、JSON、CSV。 |
| 离线一键评估 | 已完成第二版 | `offline_runner.py` 可串联 integrity、sidecar、layout、cross-reference、object、chunk、retrieval、performance。 |
| Retrieval 评估 | 已完成在线第一版 | 已实现 sidecar backend 与 LightRAG API backend；当前本地 `qwen3:8b` skip-KG DOCX smoke Evidence Recall@5=0.9412、MRR=0.9412。 |
| Answer 评估 | 已完成在线第一版 | 已实现 exact/oracle answer、numeric/unit、formula、table cell、abstention、citation、groundedness、hallucination 指标；当前本地 `qwen3:8b` skip-KG DOCX smoke answer accuracy=0.8611。 |
| Performance 评估 | 已完成离线第一版 | 已实现 dataset size、sidecar size、parse time、blocks、objects、chunks 等离线指标；ingest/query/storage/内存指标仍未完成。 |
| Object traceability | 已完成第一版 | 已实现 oracle object 与 LightRAG sidecar object 的 preservation/link-to-block 对照。 |
| Chunk traceability | 已完成离线第一版 | 已调用 LightRAG paragraph-semantic chunker，验证 chunk -> sidecar.refs -> blockid 与 chunk fact coverage。 |
| Layout audit | 已完成严格第一版 | 已验证 native sidecar position 覆盖率、对象到 positioned block 桥接，并新增复杂版面文本保留审计；当前已发现 VML floating textbox 文本未被 native parser 提取。page/bbox accuracy 因 native sidecar 无页码/bbox 暂不可评估。 |
| Cross-reference audit | 已完成离线第一版 | 已验证 DOCX REF field、bookmark target、sidecar text hit、chunk hit 与 oracle reference object hit。 |
| Scale report | 已完成第一版 | 已实现 smoke/medium/large/stress 多规模 Markdown/JSON/CSV 汇总。 |
| Readiness report | 已完成第一版 | 已生成可读结论，当前判断 LightRAG native DOCX 离线结构记忆为 partial，端到端 Document Memory 尚未证明。 |
| Comparison report | 已完成第一版 | 已能聚合多个 retrieval/parser/answer JSON，当前已生成 sidecar retrieval 多规模对比报告。 |
| API preflight | 已完成第一版 | 已能输出在线评估前置条件报告；当前 LLM、Ollama embedding 与 LightRAG API 均已通过 preflight。 |
| Docling/MinerU 测试 | 未完成 | 需要对应 parser 服务配置后再接入。 |

## 3.1 最新在线重跑记录

| Run | 配置 | KG | 结果摘要 | 报告 |
| --- | --- | --- | --- | --- |
| `rich-smoke-v1-local-gemma-vlm-skipkg` | `qwen3:4b-instruct` query/keyword、`gemma3:4b` VLM、`bge-m3` embedding、`docx:native-iteP!` | 跳过 | Recall@5=0.9412，MRR=0.9412，answer accuracy=0.8056，formula=0.0000，citation=0.9444，hallucination=0.1944 | `memory_eval_tests/runs/online/rich-smoke-v1-local-gemma-vlm-skipkg/online_report.md` |
| `rich-smoke-v1-local-qwen8b-kg` | `qwen3:8b` query/keyword/extract、`gemma3:4b` VLM、`bge-m3` embedding、`docx:native-iteP` | 开启 | ingest 失败，解析与 VLM 分析已完成，KG extraction 在 chunk 阶段触发 timeout；当时 role timeout 仍为 240s | 未生成完整问答报告 |
| `rich-smoke-v1-local-qwen8b-skipkg` | `qwen3:8b` query/keyword、`gemma3:4b` VLM、`bge-m3` embedding、`docx:native-iteP!` | 跳过 | Recall@5=0.9412，MRR=0.9412，answer accuracy=0.8611，formula=0.5000，citation=0.9444，hallucination=0.1667 | `memory_eval_tests/runs/online/rich-smoke-v1-local-qwen8b-skipkg/online_report.md` |
| `rich-smoke-v1-local-qwen8b-kg-timeout900` | `qwen3:8b` query/keyword/extract、`gemma3:4b` VLM、`bge-m3` embedding、`docx:native-iteP` | 开启 | ingest 成功，耗时约 3752s；Recall@5=1.0000，MRR=1.0000，answer accuracy=0.8056，formula=0.5000，citation=0.9444，hallucination=0.2500 | `memory_eval_tests/runs/online/rich-smoke-v1-local-qwen8b-kg-timeout900/online_report.md` |

## 4. 当前生成的数据是什么样的

当前已生成新版 rich smoke 数据集：

```text
memory_data_service/generated/rich-smoke-v1/
```

包含文件：

```text
rich-smoke-v1.docx
manifest.json
facts.json
questions.json
objects.json
relations.json
oracle.json
FIG-0004.png
FIG-0008.png
FIG-0012.png
```

当前规模：

- pages: 12
- facts: 27
- questions: 36
- objects: 197
- relations: 253
- table objects: 5
- figure objects: 3
- equation objects: 2
- footnote objects: 1
- endnote objects: 1
- cross-page long table objects: 1
- PDF: skipped，因为当前默认只请求 DOCX，且本机 LibreOffice 不稳定。

`manifest.json` 记录数据集元信息：

- dataset id
- tier
- pages
- formats
- modalities
- 文件路径
- 文件大小
- PDF 是否 skipped
- profile

`facts.json` / `oracle.json` 中的一个 fact 示例：

```json
{
  "fact_id": "FACT-00001",
  "fact_type": "direct_numeric",
  "answer": "9021 QMU",
  "expected_text": "FACT-00001: The authoritative calibration limit for Retrieval Cell 0001 is 9021 QMU. This value supersedes the provisional value 9034 QMU and the legacy value 9000 QMU.",
  "section": "Chapter 1: Adaptive Retrieval Control Area / Section 1.1: Retrieval Cell Group 0001",
  "page": 1,
  "object_type": "text",
  "object_id_hint": "OBJ-000004"
}
```

`questions.json` / `oracle.json` 中的一个 question 示例：

```json
{
  "id": "Q-FACT-00001",
  "question": "What is the authoritative calibration limit for Retrieval Cell 0001?",
  "answer": "9021 QMU",
  "question_type": "direct_numeric",
  "evidence_fact_ids": ["FACT-00001"],
  "expected_behavior": "answer"
}
```

`objects.json` 中记录文档对象：

```json
{
  "object_id": "OBJ-000001",
  "object_type": "document",
  "title": "LightRAG Synthetic Rich Memory Document",
  "text": "Synthetic rich technical document with oracle object graph.",
  "section": "document",
  "page_start": 1,
  "page_end": 12,
  "parent_id": "",
  "labels": []
}
```

`relations.json` 中记录对象关系：

```json
{
  "relation_id": "REL-000001",
  "source_id": "OBJ-000001",
  "target_id": "OBJ-000002",
  "relation_type": "contains"
}
```

当前数据已经完成的新增复杂结构：

- 已加入 Word `TOC` field 与 `updateFields` 设置，但仍需真实 Word/LibreOffice 渲染后确认页码更新。
- 已加入第一版 Word `CITATION` field 和 `BIBLIOGRAPHY` field。
- 已加入双栏 section、column control 段落和 VML floating textbox。
- 已加入脚注/尾注 package part、正文 reference 和 relationship。

当前数据仍然需要继续增强：

- TOC 已写入 Word `TOC` field，但当前生成阶段不会自动更新渲染页码，需要在 Word/LibreOffice 中 update field 或后续补 field update 流程。
- 脚注/尾注已升级为 DOCX package 中的 `word/footnotes.xml` / `word/endnotes.xml` 与正文 reference，但还需要更多真实文档中的多脚注、多尾注和长注释场景。
- 交叉引用已加入第一版 Word `REF` field，但还需要覆盖 figure/equation/section 等更多 field 类型。
- 表格已支持合并表头和第一版跨页长表格，但还需要更复杂的跨页断行、重复表头和嵌套表格。
- 图片已包含文字和数值，但还没有复杂图表或 OCR 质量分级。
- 公式已是 OMML，但还需要更多公式结构，例如分式、上下标、矩阵。
- Citation/Bibliography field 已加入第一版 synthetic 控件，但还需要覆盖真实 Zotero/Word citation、多条 citation、不同 CSL 样式等变体。
- 复杂版面已加入双栏和 VML floating textbox 第一版；当前 native parser 已暴露 textbox 文本丢失，还需要更多真实图文混排、跨页对象、嵌入文本框变体。
- 长度 tier 已能生成 20/200/1000/3000 页可控模板，但内容仍是 synthetic benchmark，不等价于真实论文/白皮书的排版多样性。

## 5. 已跑过的验证结果

### 5.1 数据完整性

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.integrity memory_data_service/generated/rich-smoke-v1 --json
```

旧版 `smoke-rich` 结果：

- passed: true
- facts: 32
- questions: 30
- DOCX created
- PDF skipped

新版 `rich-smoke-v1` 结果：

- passed: true
- profile: rich
- pages: 12
- facts: 27
- questions: 36
- objects: 197
- relations: 253
- generation_time_seconds: 2.962
- generation_peak_memory_mb: 5.431
- object types: appendix, caption, document, endnote, equation, figure, footnote, glossary_term, layout_region, paragraph, reference, section, table, textbox
- object labels include: bullet_control, nested_bullet_control, section_summary, local_conclusion, cross_page_long_table, cross_reference, footnote, endnote, two_column_layout, textbox, floating_object, citation_field, bibliography_field
- relation types: caption_of, contains, contradicts, defines, distracts, mentions, refers_to, supports
- rich_docx_features: footnotes_part=true, endnotes_part=true, footnote_reference=true, endnote_reference=true, ref_cross_reference_field=true, citation_field=true, bibliography_field=true, two_column_section=true, textbox=true, update_fields_setting=true
- rich_density_checks: 20 checks passed，包括事实/问题/对象/关系密度、表格/图片/公式对象密度、干扰标签、章节摘要、局部结论、脚注/尾注、复杂版面、文本框、浮动对象、Citation/Bibliography field 和跨页长表格。
- DOCX created
- PDF skipped，因为默认只请求 DOCX，本机 LibreOffice 路径仍不稳定

### 5.2 LightRAG native parser sidecar 审计

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.sidecar_audit memory_data_service/generated/rich-smoke-v1/rich-smoke-v1.docx --engine native --output-dir memory_eval_tests/runs/sidecar --json
```

旧版 `smoke-rich` 结果：

- blocks: 26
- headings: 26
- positioned_blocks: 26
- position_coverage: 1.0
- tables: 4
- tables linked_to_block: 4
- drawings: 3
- drawings linked_to_block: 3
- equations: 0

`equations=0` 的原因：

当前生成器写入的是公式文本，例如 `E_{4} = P_{4} * T_{4} / eta`，不是 Word 原生 equation object。LightRAG native parser 因此不会把它识别为 equation sidecar object。

新版 `rich-smoke-v1` 已修复公式问题，结果：

- blocks: 15
- positioned_blocks: 15
- position_coverage: 1.0
- tables: 5
- tables linked_to_block: 5
- drawings: 4
- drawings linked_to_block: 4
- equations: 2
- equations linked_to_block: 2
- sidecar_ref_validation: invalid_ref_count=0, missing_asset_count=0
- block_ref_validation: declared_blocks=15, actual_blocks=15, issues=0

说明：

`drawings=4` 中包含 native parser 对 VML floating textbox 的 drawing 记录，但该记录没有抽取出文本框正文。因此 parser sidecar 基础引用链路通过，并不等价于复杂版面语义完整保留。

### 5.3 Object Traceability 审计

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.object_traceability --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/sidecar/rich-smoke-v1.docx.parsed --json
```

结果：

- table preservation_rate: 1.0
- table linked_to_block_rate: 1.0
- figure preservation_rate: 1.0
- figure linked_to_block_rate: 1.0
- equation preservation_rate: 1.0
- equation linked_to_block_rate: 1.0
- table linked_to_chunk_rate: 1.0
- figure linked_to_chunk_rate: 1.0
- equation linked_to_chunk_rate: 1.0
- caption_link_rate: 1.0
- reference_target_rate: 1.0
- fact_evidence_hit_rate: 1.0

### 5.4 Chunk Traceability 审计

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.chunk_traceability --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/sidecar/rich-smoke-v1.docx.parsed --json
```

结果：

- blocks: 15
- chunks: 63
- chunks_with_sidecar: 63
- chunk_sidecar_coverage: 1.0
- invalid_ref_count: 0
- chunk_fact_hit_rate: 1.0
- caption_chunk_hit_rate: 1.0
- reference_chunk_hit_rate: 1.0

说明：

当前是离线 chunk traceability，调用的是 LightRAG paragraph-semantic chunker，验证 chunk -> sidecar.refs -> blockid 这一段链路。真实 API ingest 后的 storage chunk traceability 仍需后续验证。

### 5.4.1 Layout / Position 审计

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.layout_audit --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/offline/rich-smoke-v1/sidecar/rich-smoke-v1.docx.parsed --json
```

结果：

- oracle_page_metadata_coverage: 1.0
- blocks: 15
- position_coverage: 1.0
- table linked_to_positioned_block_rate: 1.0
- figure linked_to_positioned_block_rate: 1.0
- equation linked_to_positioned_block_rate: 1.0
- meaningful_position_coverage: 0.0
- page_or_bbox_position_coverage: 0.0
- layout_accuracy_evaluable: false
- complex_layout_text_preservation.hit_rate: 0.5
- complex_layout_text_preservation.textbox_hit_rate: 0.0
- passed: false

说明：

LightRAG native DOCX sidecar 当前输出的是 `paraid` position，占位存在但 range 为 null，也没有 page/bbox。因此本框架已经能审计 position coverage 和 object -> positioned block traceability；但 page-level 或 bbox-level layout accuracy 需要 Docling/MinerU/PDF parser 或 native parser 输出更丰富坐标后才能评估。

新增严格复杂版面审计后，当前 native parser 暴露了一个明确问题：双栏中的普通 column control 文本能被抽取，但 VML floating textbox `TEXTBOX-0001` 的文本没有进入 blocks/chunks。这个问题对富文本文档 memory 很关键，因为实际技术文档经常把关键提示、边注、callout 放在文本框或浮动对象里。

### 5.4.2 Cross-reference 审计

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.cross_reference_audit --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/offline/rich-smoke-v1/sidecar/rich-smoke-v1.docx.parsed --json
```

结果：

- docx_ref_fields: 2
- docx_bookmarks: 4
- ref_field_target_rate: 1.0
- ref_field_sidecar_hit_rate: 1.0
- ref_field_chunk_hit_rate: 1.0
- oracle_cross_reference_objects: 3
- oracle_cross_reference_block_hit_rate: 1.0
- oracle_cross_reference_chunk_hit_rate: 1.0
- oracle_refers_to_relation_validity: 1.0

说明：

当前生成器有 2 个真实 Word `REF` fields 指向表格 bookmark，并有 3 个 oracle cross-reference objects。审计结果说明 Word field 的 bookmark target、sidecar 文本保留、chunk 文本保留都能闭合。后续仍需扩展 figure/equation/section REF field 类型。

### 5.5 Sidecar Retrieval Baseline

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.retrieval_eval --backend sidecar --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/sidecar/rich-smoke-v1.docx.parsed --mode sidecar --top-k 5
```

结果：

- cases: 34
- average_recall: 0.9853
- mrr: 0.6147
- context_precision: 0.2118
- object_hit_rate: 0.5588
- full_recall_cases: 33

说明：

这是离线 lexical sidecar baseline，不代表 LightRAG LLM/RAG 的最终表现。它用于验证 oracle、sidecar、问题集和指标计算是否能闭合。

### 5.6 Performance Audit

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.performance_audit --dataset memory_data_service/generated/rich-smoke-v1 --parsed-dir memory_eval_tests/runs/sidecar/rich-smoke-v1.docx.parsed --json
```

结果：

- dataset_size_bytes: 456629
- sidecar_size_bytes: 81119
- parse_time_seconds: 0.722
- generation_peak_memory_mb: 5.431
- blocks: 15
- tables: 5
- drawings: 4
- equations: 2
- chunks: 63
- chunk_sidecar_coverage: 1.0
- chunk_fact_hit_rate: 1.0

### 5.7 Offline Runner

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/rich-smoke-v1 --engine native --top-k 5 --force-reparse --json
```

结果：

- passed: false
- output_dir: `memory_eval_tests/runs/offline/rich-smoke-v1`
- reports: integrity、sidecar、layout、cross_reference、object_traceability、chunk_traceability、retrieval_sidecar、performance
- markdown_report: `memory_eval_tests/runs/offline/rich-smoke-v1/report.md`

说明：

严格版 offline runner 失败是预期发现项，不是测试框架异常。失败原因来自 `layout_audit.passed=false`：native parser 未保留 VML floating textbox 文本，且 native sidecar 缺少 page/bbox 坐标。其他 integrity、sidecar ref、cross-reference、object traceability、chunk traceability 与 sidecar retrieval baseline 仍可正常产出报告。

### 5.8 默认 20 页 smoke tier 验证

说明：

5.8 至 5.11 的多规模结果是在严格复杂版面文本保留审计加入之前生成的历史 baseline，仍可用于观察 parser/chunk/retrieval 随规模变化的趋势；但不能说明 floating textbox、page/bbox layout accuracy 已通过。用最新 rich generator 重新跑这些 tier 时，若仍使用 native DOCX parser，预计会像 12 页 smoke 一样在严格 layout audit 上暴露 textbox 文本丢失。

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx --dataset-id rich-smoke-20p-v1
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/rich-smoke-20p-v1 --engine native --top-k 5 --force-reparse --json
```

结果：

- passed: true
- pages: 20
- generation_time_seconds: 0.728
- facts: 45
- questions: 60
- objects: 308
- relations: 401
- parser sidecar: 18 blocks, 7 tables, 5 drawings, 4 equations
- chunks: 86
- chunk_sidecar_coverage: 1.0
- chunk_fact_hit_rate: 1.0
- retrieval cases: 58
- Evidence Recall@5: 0.8879
- MRR: 0.5198
- Context Precision: 0.1966
- Object Hit Rate: 0.5345
- output_dir: `memory_eval_tests/runs/offline/rich-smoke-20p-v1`

### 5.9 200 页 medium tier 验证

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier medium --formats docx --dataset-id rich-medium-200p-v1
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/rich-medium-200p-v1 --engine native --top-k 5 --force-reparse --json
```

结果：

- passed: true
- pages: 200
- generation_time_seconds: 2.132
- facts: 465
- questions: 605
- objects: 2976
- relations: 3910
- parser sidecar: 126 blocks, 67 tables, 50 drawings, 40 equations
- chunks: 626
- chunk_sidecar_coverage: 1.0
- chunk_fact_hit_rate: 1.0
- object fact_evidence_hit_rate: 1.0
- retrieval cases: 603
- Evidence Recall@5: 0.4511
- MRR: 0.3356
- Context Precision: 0.1068
- Object Hit Rate: 0.4909
- output_dir: `memory_eval_tests/runs/offline/rich-medium-200p-v1`

解释：

medium 数据集证明 parser、object traceability、chunk traceability 在 200 页富文本文档上仍能闭合；同时离线 lexical sidecar retrieval 明显下降，可作为后续真实 LightRAG `mix/hybrid/local/global/naive` 检索能力对比的压力基线。

### 5.10 1000 页 large tier 验证

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier large --formats docx --dataset-id rich-large-1000p-v1
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/rich-large-1000p-v1 --engine native --top-k 5 --force-reparse --json
```

结果：

- passed: true
- pages: 1000
- generation_time_seconds: 10.301
- facts: 2327
- questions: 3020
- objects: 14827
- relations: 19492
- parser sidecar: 606 blocks, 334 tables, 250 drawings, 200 equations
- chunks: 3020
- chunk_sidecar_coverage: 1.0
- chunk_fact_hit_rate: 1.0
- object fact_evidence_hit_rate: 1.0
- retrieval cases: 3018
- Evidence Recall@5: 0.3666
- MRR: 0.2993
- Context Precision: 0.0899
- Object Hit Rate: 0.4924
- dataset_size_bytes: 33029259
- sidecar_size_bytes: 5372865
- parse_time_seconds: 1.269
- output_dir: `memory_eval_tests/runs/offline/rich-large-1000p-v1`

解释：

large 数据集证明千页 DOCX 的 native parser sidecar、object traceability 与 chunk traceability 可以离线闭合；但 sidecar lexical retrieval 在超长单文档上显著退化，后续真实 LightRAG API 检索评估应重点观察 `mix/hybrid/local/global` 是否能提升多跳 evidence recall 和 object hit rate。

### 5.11 3000 页 stress tier 抽样验证

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier stress --formats docx --dataset-id rich-stress-3000p-v1
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/rich-stress-3000p-v1 --engine native --top-k 5 --max-cases 500 --max-facts 1000 --force-reparse --json
```

结果：

- passed: true
- pages: 3000
- generation_time_seconds: 51.867
- facts: 6984
- questions: 9061
- objects: 44457
- relations: 58452
- parser sidecar: 1806 blocks, 1001 tables, 750 drawings, 600 equations
- chunks: 8998
- chunk_sidecar_coverage: 1.0
- sampled chunk_fact_hit_rate: 1.0 over 1000 / 6984 facts
- sampled object fact_evidence_hit_rate: 1.0 over 1000 / 6984 facts
- sampled retrieval cases: 500 / 9059 answerable questions
- Evidence Recall@5: 0.3060
- MRR: 0.2246
- Context Precision: 0.0744
- Object Hit Rate: 0.4870
- dataset_size_bytes: 99309998
- sidecar_size_bytes: 16121281
- parse_time_seconds: 2.728
- output_dir: `memory_eval_tests/runs/offline/rich-stress-3000p-v1`

说明：

全量 stress retrieval 曾运行超过数分钟，因此已为 offline runner 增加 `--max-cases` 与 `--max-facts`。当前 stress 结论是“结构全量 + evidence/retrieval 抽样”通过；后续若要全量 stress retrieval，需要把 sidecar retrieval 改成倒排索引、并行评分或预计算 term vectors。

### 5.12 多规模 scale report

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.scale_report memory_data_service/generated/rich-smoke-v1 memory_data_service/generated/rich-smoke-20p-v1 memory_data_service/generated/rich-medium-200p-v1 memory_data_service/generated/rich-large-1000p-v1 memory_data_service/generated/rich-stress-3000p-v1 --output memory_eval_tests/runs/scale_report.md --format markdown
```

输出：

- `memory_eval_tests/runs/scale_report.md`

结论：

- 12 页：Evidence Recall@5 = 0.9853
- 20 页：Evidence Recall@5 = 0.8879
- 200 页：Evidence Recall@5 = 0.4511
- 1000 页：Evidence Recall@5 = 0.3666
- 3000 页抽样：Evidence Recall@5 = 0.3060

这条曲线说明：parser/sidecar/chunk/object traceability 能保持闭合，但离线 lexical retrieval 随单文档规模扩大会明显退化。后续真实 LightRAG API 评估应证明其 graph/vector/mix 检索是否能显著高于该 baseline。

scale report 现在也会输出 `generation_peak_memory_mb`。当前重新生成后的 `rich-smoke-v1` peak memory 为 5.431 MB；旧的 20/200/1000/3000 页数据集是在资源监控字段加入前生成，因此该列为空，后续重跑对应 tier 会自动补齐。

### 5.13 Document Memory Readiness Report

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.readiness_report memory_data_service/generated/rich-smoke-v1 memory_data_service/generated/rich-smoke-20p-v1 memory_data_service/generated/rich-medium-200p-v1 memory_data_service/generated/rich-large-1000p-v1 memory_data_service/generated/rich-stress-3000p-v1 --output memory_eval_tests/runs/readiness_report.md --format markdown
```

输出：

- `memory_eval_tests/runs/readiness_report.md`
- `memory_eval_tests/runs/readiness_report.json`

当前结论：

- document_memory_baseline: partial
- Document object preservation: supported
- Chunk traceability: supported
- Layout memory: risk
- Cross-reference preservation: supported
- Retrieval scaling: risk
- End-to-end answerability: first DOCX smoke online pass, long-document and PDF unproven
- PDF and external parsers: unproven

解释：

离线框架已经证明 native DOCX parser 能保留 table/drawing/equation rich-object 与 traceability，同时严格 layout 审计已经发现 VML floating textbox 文本丢失。当前第一轮真实 API ingest/query/answer 已跑通，并在 DOCX smoke 上获得较好结果；但仍不能证明 LightRAG 已满足超长富文本文档 Document Memory，因为 PDF parser、VLM 视觉理解、storage chunk traceability、page/bbox layout accuracy、长文档在线准确率和多 query mode 对照仍未验证。

### 5.14 Parser / Mode Comparison Report

命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.comparison_report memory_eval_tests/runs/offline/rich-smoke-v1/retrieval_sidecar.json memory_eval_tests/runs/offline/rich-smoke-20p-v1/retrieval_sidecar.json memory_eval_tests/runs/offline/rich-medium-200p-v1/retrieval_sidecar.json memory_eval_tests/runs/offline/rich-large-1000p-v1/retrieval_sidecar.json memory_eval_tests/runs/offline/rich-stress-3000p-v1/retrieval_sidecar.json --output memory_eval_tests/runs/comparison_report.md --format markdown
```

输出：

- `memory_eval_tests/runs/comparison_report.md`
- `memory_eval_tests/runs/comparison_report.json`

当前已比较：

- sidecar backend / sidecar mode / top_k=5
- 12、20、200、1000、3000 页 tier
- 指标包括 cases、Evidence Recall@5、MRR、Context Precision、Object Hit Rate

后续真实 API 和 Docling/MinerU 产生 JSON 后，可直接把对应 retrieval/answer/parser 报告加入同一个 comparison report。

### 5.15 真实 LightRAG API 在线端到端评估

当前已完成第一轮 DOCX smoke 在线链路：

```text
rich-smoke-v1.docx
-> LightRAG API upload
-> native-teP parser
-> paragraph_semantic chunks
-> local Ollama bge-m3 embedding
-> remote OpenAI-compatible LLM
-> KG/entity/relation/chunk storage
-> /query/data retrieval
-> /query answer generation
```

在线配置：

- dataset: `memory_data_service/generated/rich-smoke-v1`
- format: DOCX
- pages: 12
- facts: 27
- questions: 36
- parser: `docx:native-teP`
- chunk method: `paragraph_semantic`
- embedding: local Ollama `bge-m3:latest`
- embedding dim: 1024
- LLM: project-level `.env` 中配置的 OpenAI-compatible chat model
- query mode: `mix`
- LightRAG working dir: `memory_eval_tests/runs/online/rich-smoke-v1-api/rag_storage`

API ingest 结果：

- LightRAG document status: processed
- content_length: 26945
- chunks_count: 14
- multimodal chunks: 7
- parse_engine: native
- process_options: teP
- chunk_opts: `size=2000, overlap=100, drop_rf=True`
- analyzing time: about 18s
- process time: about 122s

在线 `/query/data` retrieval 结果：

- report: `memory_eval_tests/runs/online/rich-smoke-v1-api/retrieval_mix_top5.json`
- cases: 34
- Evidence Recall@5: 1.0
- MRR: 1.0
- full_recall_cases: 34/34

说明：

当前在线 retrieval 指标是 fact-level evidence hit：只要返回上下文中能命中 oracle fact id、答案值或对应 evidence text，即认为找到了证据。这能说明 LightRAG 已经能把问题路由到正确事实，但不等价于“逐字返回 oracle 原句”。后续需要同时保留 strict exact-evidence recall 和 fact-level evidence recall 两套指标。

在线 `/query` answer 结果：

- report: `memory_eval_tests/runs/online/rich-smoke-v1-api/answer_mix.json`
- cases: 36
- answer_accuracy: 0.9444
- numeric_unit_accuracy: 1.0
- formula_accuracy: 1.0
- table_cell_accuracy: 1.0
- abstention_accuracy: 1.0
- citation_accuracy: 1.0
- groundedness: 0.9444
- hallucination_rate: 0.0556

失败问题：

- `Q-FACT-00006`: 期望 `verified-state-0004`，模型回答信息不足。该问题依赖图片/figure visual state。当前 `VLM_PROCESS_ENABLE=false`，因此这类视觉语义问题失败是合理暴露项。
- `Q-MULTIHOP-0010`: 期望 `99.75 ms; E_{10}=P_{10}T_{10}/\eta_{10}`，模型给出了公式，但漏掉表格中的 `99.75 ms` latency fact。这是真实多跳合并失败。

结论：

在 12 页 synthetic rich DOCX smoke 上，LightRAG 的在线基础链路已经可用，检索阶段表现很好；问答阶段对数值、公式、表格、拒答和引用表现较好，但对视觉内容和多跳证据合并仍有明显风险。该结果不能外推到 200/1000/3000 页，也不能代表 PDF/Docling/MinerU/VLM 场景。

## 6. LibreOffice / PDF 问题

当前 PDF 生成使用：

```bash
/opt/homebrew/bin/soffice --headless --convert-to pdf
```

本机运行时出现 exit status 134，并触发 macOS Apple 崩溃提示。

当前处理方式：

- 不让数据生成整体失败。
- 在 `manifest.json` 中将 PDF 文件标记为 skipped。
- 保存失败原因。

短期建议：

- 后续默认只生成 DOCX，避免反复触发 Apple 崩溃弹窗。
- PDF 测试单独作为第二阶段处理。

推荐命令：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --tier smoke --formats docx
```

PDF 后续可选替代方案：

- 修复本机 LibreOffice 安装。
- 使用 Python/reportlab 直接生成 PDF。
- 使用 pandoc + tectonic/xelatex。
- 使用 docx2pdf，但 macOS 仍可能依赖 Word 或系统 GUI。
- 准备真实 PDF 数据集，不从 DOCX 转换。

## 6.5 LightRAG API / LLM Binding 当前状态

真实 LightRAG API 端到端评估已完成第一轮，当前不再阻塞：

- 项目级 `.env` 已配置远端 OpenAI-compatible LLM。
- 本地 Ollama 已安装并启动。
- 本地 embedding 模型 `bge-m3:latest` 已安装，维度为 1024。
- LightRAG API server 已成功使用上述配置完成 DOCX ingest、retrieval 和 answer evaluation。

当前仍需注意：

- API key 只写在项目 `.env` 中，不应提交到 git。
- 当前只验证了 DOCX smoke，尚未验证 PDF、Docling、MinerU、VLM 和长文档在线性能。
- 当前 embedding 是本地 Ollama，LLM 是远端 API；后续可增加本地轻量 LLM 对照，但回答质量预计会低于远端模型。

当前已新增可复现 preflight：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.api_preflight --output memory_eval_tests/runs/api_preflight.json
```

最新 preflight 结果：

- ready_for_online_eval: true
- llm_ready: true
- embedding_ready: true
- api_ready: true
- blockers: []

## 7. 数据生成服务增强计划

### 7.1 文档结构增强

目标：从“简单合成文档”升级为“接近真实技术文档/白皮书/论文”的 synthetic benchmark。

待实现：

- [x] 多层级章节树：Part / Chapter / Section / Subsection / Paragraph。
- [x] 自动目录 TOC field。
- [x] 手工目录页。
- [x] 编号列表。
- [x] 项目符号列表、嵌套列表。
- [x] 页眉页脚。
- [x] 脚注、尾注。
- [x] 附录。
- [x] 术语表。
- [x] 参考文献 section。
- [x] Citation-like references。
- [x] Cross-reference fields。
- [x] 章节摘要和局部结论。

### 7.2 表格增强

待实现：

- [x] 合并单元格。
- [x] 多级表头。
- [x] 跨页长表格。
- [x] 表格脚注。
- [x] 表格 caption。
- [x] 表格内 gold fact。
- [x] 表格内 distractor fact。
- [x] 表格与正文交叉引用。
- [x] 单位、数值范围、日期、版本号、阈值等类型。

### 7.3 图片与 caption 增强

待实现：

- [x] 生成信息密集型图像，而不是纯占位图。
- [x] 图中包含可 OCR 的标签、数字、箭头和状态。
- [x] 图注中包含 gold fact。
- [x] 正文引用 figure id。
- [x] Figure 与 section/block 建立 oracle 关系。
- [x] 图像文件资产记录到 manifest。
- [x] 测试 LightRAG drawing sidecar 与 block 的关系。

### 7.4 公式增强

待实现：

- [x] 生成 Word 原生 OMML equation object。
- [x] 生成 LaTeX-like formula text 作为对照。
- [x] 公式编号。
- [x] 公式 caption 或 equation label。
- [x] 正文引用公式编号。
- [x] 公式变量定义文本。
- [x] 公式问答 oracle。

### 7.5 复杂推理数据增强

待实现：

- [x] 单跳事实问题。
- [x] 多跳跨章节问题。
- [x] 表格 + 正文组合问题。
- [x] 图片 caption + 正文组合问题。
- [x] 公式 + 变量定义组合问题。
- [x] 时间/version 条件问题。
- [x] 否定问题。
- [x] 冲突事实消解问题。
- [x] 干扰事实强对抗问题。
- [x] 不可回答问题，用于测试 abstention。

### 7.6 长文档规模增强

待实现：

- [x] smoke: 20 页，开发调试。
- [x] medium: 200 页，常规评估。
- [x] large: 1000 页，超长文档评估。
- [x] stress: 3000 页，压力测试，当前为 evidence/retrieval 抽样验证。
- [x] 每个 tier 生成稳定 dataset id。
- [x] 每个 tier 记录生成时间、文件大小、fact 数、question 数。
- [x] 大文档生成时避免一次性内存爆炸：已加入默认 3000 页生成护栏、`--allow-oversized-generation` 显式 override、`generation_peak_memory_mb` 与 `generation_resource_estimate`。

## 8. 测试框架完整计划

### 8.1 数据完整性测试

状态：已完成第一版。

已实现：

- [x] manifest schema 校验。
- [x] oracle schema 校验。
- [x] facts/questions 与 oracle 一致性校验。
- [x] question evidence_fact_ids 存在性校验。
- [x] 本地文件存在性和大小校验。
- [x] 每个 modality 的最小覆盖率校验。
- [x] 每个 question_type 的最小覆盖率校验。
- [x] 每个 rich relation_type 的最小覆盖率校验。
- [x] 每个 tier 的 fact/question 数量阈值校验。
- [x] rich DOCX package 结构校验：footnotes/endnotes part、footnote/endnote reference、REF cross-reference field。

待增强：

- [x] 更细的 tier-specific 阈值，例如 medium/large/stress 的对象密度、跨章节问题比例、干扰事实比例。

### 8.2 Parser / Sidecar 测试

状态：已完成第一版。

已实现：

- [x] 调用 `python -m lightrag.parser.cli`。
- [x] 支持 native parser。
- [x] 统计 blocks。
- [x] 统计 positioned blocks。
- [x] 统计 tables/drawings/equations。
- [x] 统计 object linked_to_block。
- [x] 对 parser 失败输出结构化错误报告。
- [x] 对 page/position/layout 做可用性和覆盖率评估。
- [x] 对 cross-reference preservation rate 做离线审计。

待增强：

- [x] 对 sidecar refs 做逐项验证。
- [x] 对 table/drawing/equation 的 blockid 做 oracle 对齐。
- [ ] 对 page/bbox layout accuracy 做准确性评估，当前 native DOCX sidecar 无页码/bbox，暂不可评估。
- [ ] 接入 docling parser。
- [ ] 接入 mineru parser。

### 8.3 Chunk Traceability 测试

状态：已完成离线第一版。

已实现：

- [x] 调用 LightRAG paragraph-semantic chunker。
- [x] 验证 chunk 是否保留 sidecar refs。
- [x] 验证 chunk -> blockid。
- [x] 计算 chunk fact evidence coverage。
- [x] 验证 chunk -> table/drawing/equation object 链路，当前在 `object_traceability.py` 中完成。
- [x] 检查 chunk 是否丢失 caption 或对象引用。

待实现：

- [ ] 验证 API ingest 后的 storage chunk 是否保留 sidecar refs。

### 8.4 Object Traceability 测试

状态：已完成第一版。

已实现：

- [x] table object -> sidecar blockid。
- [x] drawing object -> sidecar blockid。
- [x] equation object -> sidecar blockid。
- [x] oracle object count -> sidecar object count。
- [x] object-level preservation rate。
- [x] fact evidence hit rate。
- [x] table object -> blockid -> chunk。
- [x] drawing object -> blockid -> chunk。
- [x] equation object -> blockid -> chunk。
- [x] caption -> object -> block。
- [x] 正文 reference -> object。
- [x] object-level retrieval recall。

待实现：

- [ ] API ingest 后的 object -> storage chunk 追踪。

### 8.5 Index Construction 测试

状态：已完成 API 入口与 preflight 第一版。

已实现：

- [x] `api_preflight.py` 检查 API/LLM/embedding/Ollama 前置条件。
- [x] `index_runner.py` 预留 LightRAG API upload 入口。
- [x] 上传后按 `track_id` 轮询 `/documents/track_status/{track_id}`。

待实现：

- [ ] 实际启动 LightRAG API server。
- [ ] 导入 DOCX 数据集。
- [ ] 记录 ingest time。
- [ ] 记录 chunk count。
- [ ] 记录 entity count。
- [ ] 记录 relation count。
- [ ] 记录 vector storage size。
- [ ] 记录 graph storage size。
- [ ] 比较 baseline chunker 与 parser-aware ingest。

### 8.6 Retrieval 测试

状态：已完成离线第一版。

已实现：

- [x] `retrieval_eval.py` 初步入口。
- [x] sidecar backend。
- [x] 计算 Evidence Recall@K。
- [x] 计算 MRR。
- [x] 计算 Context Precision。
- [x] 计算 Object Hit Rate。
- [x] 对接真实 LightRAG `/query/data` API 请求格式。

待实现：

- [ ] 在真实 LightRAG API server 上运行并保存结果。
- [ ] 获取 include_chunk_content / context。
- [ ] 比较 naive/hybrid/local/global/mix。
- [ ] 比较 baseline chunker 与 parser-aware memory。

### 8.7 Answer 测试

状态：已完成评分器第一版。

已实现：

- [x] `answer_eval.py` 初步入口。
- [x] exact match。
- [x] numeric/unit accuracy。
- [x] formula accuracy。
- [x] table cell accuracy。
- [x] abstention accuracy。
- [x] citation correctness。
- [x] answer groundedness。
- [x] hallucination rate。

待实现：

- [ ] 调用 LightRAG `/query`。
- [ ] 强制 `include_references=true`。
- [ ] 强制 `include_chunk_content=true`。
- [ ] 在真实 LightRAG API server 上运行并保存结果。

### 8.8 Performance 测试

状态：已完成离线第一版。

已实现：

- [x] dataset size。
- [x] parse time。
- [x] sidecar size。
- [x] chunk count。
- [x] block/table/drawing/equation count。
- [x] chunk sidecar coverage。
- [x] data generation time。
- [x] data generation peak memory。
- [x] 不同 tier 的性能曲线。

待实现：

- [ ] ingest time。
- [ ] entity count。
- [ ] relation count。
- [ ] query latency p50/p95。
- [ ] storage size。
- [ ] memory usage。

### 8.9 Report 测试

状态：已完成第一版。

已实现：

- [x] Markdown 输出。
- [x] JSON 输出。
- [x] CSV 输出。
- [x] 生成单个 dataset 的完整离线评估报告。
- [x] 生成多 tier scale report。
- [x] 生成可读结论：LightRAG 是否满足 Document Memory 需求。
- [x] 生成多 parser/mode 对比报告。

待增强：

- [ ] 接入真实 API / Docling / MinerU 后，生成包含多 parser、多 query mode、多 answer 指标的完整对比报告。

## 9. 推荐下一阶段任务顺序

### Phase 1: 数据生成器升级

状态：已完成 rich 多规模第一版，继续增强真实 Word/PDF 结构、复杂版面和端到端评估。

当前 rich 数据已经能覆盖对象图、表格、图片、OMML 公式、脚注/尾注、第一版 Word REF/CITATION/BIBLIOGRAPHY 字段、updateFields 设置、双栏 section、VML floating textbox、跨页长表格、多跳、版本条件、冲突消解、否定约束和拒答问题，并已跑通过 smoke/medium/large/stress 历史离线规模验证。严格复杂版面审计加入后，当前 smoke 明确暴露 native DOCX parser 对 floating textbox 的文本丢失问题；真实 API 已完成第一轮 DOCX smoke 端到端评估。后续仍需更多真实 Word/PDF 结构变体、外部 parser 对比和长文档在线评估。

任务：

1. [x] 新增 `memory_data_service/generators/rich_docx_generator.py`。
2. [x] 支持第一批复杂结构：目录页、caption、交叉引用段落、附录、术语表。
3. [x] 将公式升级为 Word OMML equation object。
4. [x] 生成信息密集型图片，而不是占位图。
5. [x] 扩展 oracle schema，显式记录 document object graph：
   - section
   - paragraph
   - table
   - figure
   - equation
   - caption
   - reference
   - fact
   - question
6. [x] 生成 object relation：
   - contains
   - refers_to
   - supports
   - distracts
   - caption_of
   - defines
   - mentions
   - contradicts
7. [x] 补充自动更新 TOC field、参考文献 field、双栏/浮动对象等结构。
8. [x] 补充 mentions、contradicts 等更强语义关系，并让冲突事实在 relation graph 中显式表达。

### Phase 2: Parser / sidecar 深度审计

状态：已完成 object/chunk traceability、layout coverage、caption linking 和 cross-reference preservation 第一版；page/bbox layout accuracy 仍需更丰富 parser 坐标后才能评估。

任务：

1. [x] 将 LightRAG sidecar 与 oracle object graph 对齐。
2. [x] 计算 object preservation rate。
3. [x] 计算 layout preservation rate。
4. [x] 计算 caption-object linking accuracy。
5. [x] 计算 cross-reference preservation rate。
6. [x] 验证 chunk -> sidecar.refs -> blockid。
7. [x] 验证复杂版面文本保留，并已暴露 native DOCX parser 未抽取 VML textbox 文本。

### Phase 3: Index / Retrieval / Answer 端到端评估

状态：已完成第一轮 DOCX smoke 在线端到端评估。下一步需要做 query mode 对照、长文档在线评估和 storage traceability 深挖。

任务：

1. [x] 启动 LightRAG API。
2. [x] 导入 DOCX。
3. [x] 执行 `mix` query mode。
4. [x] 计算 retrieval metrics。
5. [x] 计算 answer metrics。
6. [x] 输出完整报告。
7. [ ] 执行 `naive/hybrid/local/global/mix` query mode 对照。
8. [ ] 在线验证 API ingest 后 storage 中的 chunk/object/source traceability。
9. [ ] 跑 200/1000/3000 页在线性能与准确性抽样。

### Phase 4: PDF 与外部 parser

状态：未完成。当前 LibreOffice 在本机转换 DOCX 到 PDF 会崩溃，PDF 路径暂时跳过。

任务：

1. 解决 PDF 生成问题。
2. 接入 docling。
3. 接入 mineru。
4. 比较 DOCX native / PDF docling / PDF mineru。

## 10. 当前可用命令

生成 DOCX-only 数据集：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx
```

生成固定 id 的 rich 调试数据集：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx --pages 12 --dataset-id rich-smoke-v1
```

列出数据集：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli list
```

启动数据服务：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_data_service.cli serve --host 127.0.0.1 --port 9731
```

校验数据完整性：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.integrity memory_data_service/generated/<dataset_id> --json
```

审计 LightRAG native parser sidecar：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.sidecar_audit memory_data_service/generated/<dataset_id>/<dataset_id>.docx --engine native --output-dir memory_eval_tests/runs/sidecar --json
```

对比 oracle object 与 LightRAG sidecar object：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.object_traceability --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --json
```

审计 chunk -> sidecar.refs -> blockid：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.chunk_traceability --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --json
```

审计 layout / position preservation：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.layout_audit --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --json
```

审计 DOCX REF field 与 cross-reference preservation：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.cross_reference_audit --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --json
```

运行完整离线评估套件：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/<dataset_id> --engine native --top-k 5
```

运行 large/stress 抽样离线评估套件：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset memory_data_service/generated/<dataset_id> --engine native --top-k 5 --max-cases 500 --max-facts 1000
```

运行离线 sidecar retrieval baseline：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.retrieval_eval --backend sidecar --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --mode sidecar --top-k 5
```

运行离线 performance audit：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.performance_audit --dataset memory_data_service/generated/<dataset_id> --parsed-dir memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed --json
```

生成 Document Memory readiness 可读结论：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.readiness_report memory_data_service/generated/rich-smoke-v1 memory_data_service/generated/rich-smoke-20p-v1 memory_data_service/generated/rich-medium-200p-v1 memory_data_service/generated/rich-large-1000p-v1 memory_data_service/generated/rich-stress-3000p-v1 --output memory_eval_tests/runs/readiness_report.md --format markdown
```

上传数据集到真实 LightRAG API 并等待处理完成：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.index_runner --dataset memory_data_service/generated/<dataset_id> --rag-api-url http://127.0.0.1:9621 --formats docx --wait
```

检查真实 LightRAG API 评估前置条件：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.api_preflight --output memory_eval_tests/runs/api_preflight.json
```

运行真实 LightRAG `/query/data` 检索评估：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.retrieval_eval --backend api --dataset memory_data_service/generated/<dataset_id> --rag-api-url http://127.0.0.1:9621 --mode mix --top-k 10
```

运行真实 LightRAG `/query` 问答评估：

```bash
/Users/sakura/miniconda3/bin/conda run -n lightrag-memory-eval python -m memory_eval_tests.answer_eval --dataset memory_data_service/generated/<dataset_id> --rag-api-url http://127.0.0.1:9621 --mode mix
```

## 11. 当前判断

当前框架的价值：

- 已经建立了数据服务和评估框架分离的工程结构。
- 已经能生成可控 oracle。
- 已经形成第一版 Document Object Graph oracle。
- 已经能跑 LightRAG native parser sidecar 审计。
- 已经能看到 LightRAG 对 table/drawing/equation/block 的基础保留情况。
- 已经能离线验证 object traceability 和 chunk traceability。
- 已经能离线验证 layout position 覆盖、对象到 positioned block 桥接、DOCX REF field 与 cross-reference 文本保留。
- 已经有 sidecar retrieval baseline 和离线 performance audit。
- 已经有单 dataset 一键离线 runner，可生成完整 JSON/Markdown 报告。
- 已经完成第一轮真实 LightRAG API DOCX smoke ingest、retrieval 和 answer 评估。
- 已经验证本地 Ollama embedding + 远端 LLM 的在线链路可用。

当前框架的不足：

- rich 数据已经明显增强，并已覆盖第一版 updateFields、Citation/Bibliography field、双栏版面、浮动文本框等复杂结构；但这些仍是 synthetic 实现，需要更多真实文档变体，例如 Zotero/Word citation、多种文本框实现、图文混排、跨页浮动对象、真实论文/白皮书/标准规范。
- native DOCX sidecar 的 position 目前是 `paraid` 占位，无法评估 page/bbox layout accuracy。
- native DOCX parser 当前没有保留 VML floating textbox 文本，严格 layout audit 已将其标为失败。
- rich 数据已验证 12/20/200/1000 页全量离线评估和 3000 页结构全量 + retrieval/evidence 抽样评估，但还没有用真实 PDF 或真实论文/白皮书数据校准。
- Retrieval 已有离线 sidecar baseline 和真实 API `mix` 第一轮结果，但还没有真实 LightRAG API 的 naive/hybrid/local/global/mix 对比结果。
- Answer 已有真实 API `mix` 第一轮结果，但还没有长文档、PDF、VLM 和多模型对照结果。
- Chunk/Object traceability 已有离线 parser/chunker 层验证，但还没有验证 API ingest 后落盘 storage 中的 chunk sidecar refs。
- PDF 路径受 LibreOffice 崩溃影响，暂时不稳定。

下一步最应该做：

优先跑真实 LightRAG API 的 `naive/hybrid/local/global/mix` 对比和 medium/large 在线抽样，同时继续扩展 rich 数据生成器的真实 Word/PDF 结构覆盖。这样才能判断 LightRAG 在超长富文本文档上是停留在 Vector Retrieval，还是能作为可溯源 Document Memory 基线。
