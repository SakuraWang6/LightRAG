# LightRAG Memory Data Service

Independent synthetic data service for rich single-document memory evaluation.
It generates DOCX/PDF files plus `manifest.json`, `facts.json`,
`questions.json`, `objects.json`, `relations.json`, and `oracle.json`.

## Structure and boundaries

```text
memory_data_service/
├── cli.py             # local generate/list/serve commands
├── app.py             # FastAPI transport only
├── schemas.py         # request, manifest and oracle contracts
├── storage.py         # generated-dataset persistence helpers
├── resource_guard.py  # generation size/resource protection
├── generators/        # basic and rich DOCX generation implementations
└── generated/         # generated datasets; runtime artifacts, never reorganized
```

For new code, keep document construction in `generators/`, dataset contracts in
`schemas.py`, and disk access in `storage.py`. `cli.py` and `app.py` are thin
entry points; neither should contain generation logic. The `generated/` tree is
an artifact area rather than framework source and is intentionally left in
place by code cleanup.

## Generate Locally

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate \
  --profile rich --tier smoke --formats docx \
  --dataset-id rich-smoke-v1 --output-root "$PWD/memory_data_service/generated"
conda run -n lightrag-memory-eval python -m memory_data_service.cli list
```

### Choose the corpus language

Datasets default to English. Pass `--language zh` to create a Simplified-Chinese
corpus: source documents, oracle questions, and answers are Chinese, while the
manifest records `language: "zh"` for reproducibility. The rich profile retains
its object graph, tables, figures, equations, and cross-document question.

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate \
  --profile rich --tier smoke --language zh --formats docx \
  --dataset-id zh-rich-smoke-v1 --output-root "$PWD/memory_data_service/generated"
```

The evaluation workbench exposes the same choice as **数据语言** when creating a
dataset. Existing datasets without this field are treated as English.

### Name a dataset

`dataset_id` is an internal filesystem/API identifier. Use `--display-name` for
the name shown in the workbench and copied into the document title; generated
IDs remain stable even when the display name contains spaces or non-ASCII text.
Older manifests without a display name remain valid and are shown with a
configuration-derived fallback label.

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate \
  --display-name "Q3 Chinese support release" --language zh --profile rich
```

Generation has a default 3000-page safety guard because DOCX writing uses
`python-docx` in-process. Larger experiments must opt in explicitly:

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --pages 3001 --allow-oversized-generation
```

An existing `dataset_id` is **not overwritten by default**; pass `--force` to
replace it. The dataset root defaults to `memory_data_service/generated` and can
be overridden with `MEMORY_EVAL_DATASETS_ROOT` (required for wheel installs,
where the in-package directory is usually read-only).

`manifest.json` records `generation_peak_memory_mb` and a
`generation_resource_estimate` so large-run resource behavior is auditable.

The default `rich` profile creates an operational decision dossier: a coherent
programme narrative with accountable owners, staged delivery gates, source and
security approvals, rollback constraints, risk controls, and cross-page
dependency questions. It also creates a DOCX with chapter/section hierarchy,
automatic TOC field plus manual TOC entries, header/footer, numbered controls,
bullet and nested bullet lists, section summaries, local conclusions,
merged-header tables, text-bearing figures, OMML equations, captions,
footnotes/endnotes, REF/CITATION/BIBLIOGRAPHY fields, two-column sections,
VML floating textboxes, references, glossary entries, appendix distractors, and
an oracle object graph.
The oracle includes direct facts, table/figure/equation facts, multi-hop
questions, version-conditioned questions, conflict-resolution questions,
negative-constraint questions, and abstention questions. Use `--profile basic`
for the older minimal smoke corpus.

Generated datasets are written to:

```text
memory_data_service/generated/<dataset_id>/
```

## Serve

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli serve --host 127.0.0.1 --port 9731
```

Endpoints:

- `POST /datasets?force=` — generate a dataset (force overwrites an existing id)
- `GET /datasets?limit=&offset=` — paginated list
- `GET /datasets/{dataset_id}` — manifest
- `GET /datasets/{dataset_id}/oracle` — unified oracle
- `GET /datasets/{dataset_id}/files/{name}` — download DOCX/PDF/JSON/images
- `DELETE /datasets/{dataset_id}` — delete a dataset (path validation; the workbench proxy returns 409 while it is being generated)

Set `MEMORY_DATA_SERVICE_API_KEY` to require an `X-API-Key` header on every
endpoint.

The evaluation workbench (`lightrag-server` → `/eval/datasets`) proxies these
capabilities (paginated list, form-driven creation through its job channel, and
delete with a 409 guard while a dataset is being generated), so you usually do
not need to run this service separately.

PDF output uses `/opt/homebrew/bin/soffice` when available. If conversion fails,
the PDF file is marked as `skipped` in `manifest.json`.
