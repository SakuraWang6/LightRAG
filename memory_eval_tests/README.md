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
├── runs/         # generated artifacts; never move or edit by framework cleanup
└── <legacy .py>  # stable compatibility entry points
```

Use the grouped module paths for new automation. Existing commands such as
`python -m memory_eval_tests.offline_runner` remain supported as compatibility
aliases, so saved scripts and prior experiment notes continue to work.

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
