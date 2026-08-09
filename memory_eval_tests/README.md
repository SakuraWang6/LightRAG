# LightRAG Memory Evaluation Tests

This directory consumes datasets produced by `memory_data_service/`. It does not
generate documents. Implementation is grouped by responsibility; generated
reports remain in `runs/` and are deliberately outside the Python packages.

```text
memory_eval_tests/
├── common/       # shared DatasetClient
├── offline/      # parser, integrity, provenance, layout and performance audits
├── online/       # API preflight, ingestion, retrieval and answer evaluation
├── experiments/  # KG, evaluator, selector and structure ablations
├── reporting/    # single-run, comparison, scale and readiness reports
└── runs/         # generated artifacts; never move or edit by framework cleanup
```

Every entry point lives in a responsibility-based package; there are no
top-level compatibility aliases. Use the grouped module paths below for all
new automation.

| Task | Recommended module |
| --- | --- |
| One-shot offline audit | `memory_eval_tests.offline.offline_runner` |
| API/import/retrieval/answer checks | `memory_eval_tests.online.{api_preflight,index_runner,retrieval_eval,answer_eval}` |
| Controlled ablations | `memory_eval_tests.experiments.*` |
| Result summaries | `memory_eval_tests.reporting.{report,comparison_report,scale_report,readiness_report}` |

## Environment

```bash
conda env create -f memory_eval_env.yml
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx
```

## Offline Suite

Run the complete offline suite for one generated dataset:

```bash
DATASET=memory_data_service/generated/<dataset_id>
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.offline_runner --dataset "$DATASET" --engine native --top-k 5
```

This writes JSON reports plus a Markdown summary to:

```text
memory_eval_tests/runs/offline/<dataset_id>/
```

For very large datasets, use deterministic sampling for evidence and retrieval
checks:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.offline_runner --dataset "$DATASET" --engine native --top-k 5 --max-cases 500 --max-facts 1000
```

Render a scale summary across several generated datasets:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.reporting.scale_report \
  memory_data_service/generated/rich-smoke-v1 \
  memory_data_service/generated/rich-medium-200p-v1 \
  memory_data_service/generated/rich-large-1000p-v1 \
  --output memory_eval_tests/runs/scale_report.md
```

Render a readable Document Memory readiness conclusion:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.reporting.readiness_report \
  memory_data_service/generated/rich-smoke-v1 \
  memory_data_service/generated/rich-medium-200p-v1 \
  memory_data_service/generated/rich-large-1000p-v1 \
  --output memory_eval_tests/runs/readiness_report.md
```

Render a comparison table from parser/retrieval/answer JSON reports:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.reporting.comparison_report \
  memory_eval_tests/runs/offline/rich-smoke-v1/retrieval_sidecar.json \
  memory_eval_tests/runs/offline/rich-medium-200p-v1/retrieval_sidecar.json \
  --output memory_eval_tests/runs/comparison_report.md
```

## Sidecar Audit

Validate the generated oracle and file manifest first:

```bash
DATASET=memory_data_service/generated/<dataset_id>
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.integrity "$DATASET" --json
```

Then run the LightRAG parser sidecar audit:

```bash
DATASET=memory_data_service/generated/<dataset_id>
DOCX=$(ls "$DATASET"/*.docx | head -1)
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.sidecar_audit "$DOCX" --engine native --json
```

Compare oracle objects with parser sidecars:

```bash
PARSED_DIR=memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.object_traceability --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
```

Audit chunk provenance and run the offline retrieval/performance baselines:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.chunk_traceability --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.layout_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.cross_reference_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.online.retrieval_eval --backend sidecar --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --mode sidecar --top-k 5
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline.performance_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
```

`layout_audit` verifies position coverage and object-to-positioned-block
traceability. Native DOCX sidecars currently expose `paraid` placeholders rather
than page/bbox coordinates, so page-level layout accuracy is reported as not
evaluable for that parser. The strict rich smoke audit also checks complex
layout text preservation; with the current native DOCX parser, VML floating
textbox text is expected to fail this check and is reported as a parser
limitation.

## Optional Online Evaluation

Check whether the local LightRAG API and model backends are ready:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.online.api_preflight \
  --output memory_eval_tests/runs/api_preflight.json
```

Start your LightRAG API server separately, upload a generated dataset, then run:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.online.index_runner --dataset "$DATASET" --formats docx --wait
conda run -n lightrag-memory-eval python -m memory_eval_tests.online.retrieval_eval --dataset "$DATASET" --mode mix
conda run -n lightrag-memory-eval python -m memory_eval_tests.online.answer_eval --dataset "$DATASET" --mode mix
```

PDF parser paths (`docling` / `mineru`) require their normal LightRAG parser
service environment variables.

## 指标定义

指标名与确定性实现严格对应；历史运行（`metric_semantics: legacy`）的旧字段
`hallucination_rate` / `citation_accuracy` 在控制台读取时自动映射为
`ungrounded_rate` / `evidence_available`，数字不变。

| 指标 | 精确语义 |
| --- | --- |
| `answer_accuracy` | 答案与 oracle 期望值精确匹配（含数值/单位、公式、表格单元规则）的题数占比。 |
| `groundedness` | 答案正确 **且** oracle 证据出现在 API references 中的题数占比；abstain 题正确拒答即视为 grounded（无需证据）。 |
| `ungrounded_rate` | 未满足 grounded 的题数占比（答案错误或证据未进入上下文）；abstain 题按 `abstention_correct` 单独判定。历史名为 `hallucination_rate`，它测的是“答错/未支撑率”，不是真实幻觉内容判定。 |
| `evidence_available` | oracle 证据是否全部出现在 API references 中（与回答是否引用无关）；abstain 题无 oracle 证据，该字段为 null 且不计入分母。历史 `citation_accuracy` 与该指标数值重复，已合并。 |
| `citation_presence` | 回答中出现显式稳定 ID（`FACT-*` / `OBJ-*`）的题数占比。 |
| `citation_correctness` | 仅在有稳定 ID 引用的题上定义：回答中出现的 ID 是否覆盖全部 oracle 证据 ID。 |
| `abstention_accuracy` | abstain 题正确拒答的占比；abstain 不参与引用类指标。 |
| `average_recall` | 检索类：命中的 oracle 证据数 / 期望证据数，在 top-K 排名内计算。 |
| `mrr` | 检索类：首个命中证据位置的倒数均值（1/rank）。API 与 sidecar 后端同口径。 |
| `context_precision` | 检索类：含至少一条证据的上下文数 / 返回上下文数。API 与 sidecar 都按单个返回上下文（API chunk / sidecar block）计算，粒度一致。 |
| `object_hit_rate` | 对象级命中率，仅 sidecar 后端可计算；API references 不暴露对象类型，输出 null。跨后端对比仅限 recall / MRR / context_precision。 |

检索与回答评估使用同一套确定性等距抽样（`common/sampling.py::sample_evenly`）；
两边都先对全量题列表抽样，再在采样结果上过滤：检索侧排除 abstain（其
`evidence_fact_ids` 为空，召回无意义），回答侧保留 abstain 以计算拒答指标。
因此无论是否设置 `max_cases`，两边的非 abstain 子集都完全一致。
