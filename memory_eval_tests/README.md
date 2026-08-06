# LightRAG Memory Evaluation Tests

This directory consumes datasets produced by `memory_data_service/`. It does not
generate documents.

## Environment

```bash
conda env create -f memory_eval_env.yml
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx
```

## Offline Suite

Run the complete offline suite for one generated dataset:

```bash
DATASET=memory_data_service/generated/<dataset_id>
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset "$DATASET" --engine native --top-k 5
```

This writes JSON reports plus a Markdown summary to:

```text
memory_eval_tests/runs/offline/<dataset_id>/
```

For very large datasets, use deterministic sampling for evidence and retrieval
checks:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.offline_runner --dataset "$DATASET" --engine native --top-k 5 --max-cases 500 --max-facts 1000
```

Render a scale summary across several generated datasets:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.scale_report \
  memory_data_service/generated/rich-smoke-v1 \
  memory_data_service/generated/rich-medium-200p-v1 \
  memory_data_service/generated/rich-large-1000p-v1 \
  --output memory_eval_tests/runs/scale_report.md
```

Render a readable Document Memory readiness conclusion:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.readiness_report \
  memory_data_service/generated/rich-smoke-v1 \
  memory_data_service/generated/rich-medium-200p-v1 \
  memory_data_service/generated/rich-large-1000p-v1 \
  --output memory_eval_tests/runs/readiness_report.md
```

Render a comparison table from parser/retrieval/answer JSON reports:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.comparison_report \
  memory_eval_tests/runs/offline/rich-smoke-v1/retrieval_sidecar.json \
  memory_eval_tests/runs/offline/rich-medium-200p-v1/retrieval_sidecar.json \
  --output memory_eval_tests/runs/comparison_report.md
```

## Sidecar Audit

Validate the generated oracle and file manifest first:

```bash
DATASET=memory_data_service/generated/<dataset_id>
conda run -n lightrag-memory-eval python -m memory_eval_tests.integrity "$DATASET" --json
```

Then run the LightRAG parser sidecar audit:

```bash
DATASET=memory_data_service/generated/<dataset_id>
DOCX=$(ls "$DATASET"/*.docx | head -1)
conda run -n lightrag-memory-eval python -m memory_eval_tests.sidecar_audit "$DOCX" --engine native --json
```

Compare oracle objects with parser sidecars:

```bash
PARSED_DIR=memory_eval_tests/runs/sidecar/<dataset_id>.docx.parsed
conda run -n lightrag-memory-eval python -m memory_eval_tests.object_traceability --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
```

Audit chunk provenance and run the offline retrieval/performance baselines:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.chunk_traceability --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.layout_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.cross_reference_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
conda run -n lightrag-memory-eval python -m memory_eval_tests.retrieval_eval --backend sidecar --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --mode sidecar --top-k 5
conda run -n lightrag-memory-eval python -m memory_eval_tests.performance_audit --dataset "$DATASET" --parsed-dir "$PARSED_DIR" --json
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
conda run -n lightrag-memory-eval python -m memory_eval_tests.api_preflight \
  --output memory_eval_tests/runs/api_preflight.json
```

Start your LightRAG API server separately, upload a generated dataset, then run:

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.index_runner --dataset "$DATASET" --formats docx --wait
conda run -n lightrag-memory-eval python -m memory_eval_tests.retrieval_eval --dataset "$DATASET" --mode mix
conda run -n lightrag-memory-eval python -m memory_eval_tests.answer_eval --dataset "$DATASET" --mode mix
```

PDF parser paths (`docling` / `mineru`) require their normal LightRAG parser
service environment variables.
