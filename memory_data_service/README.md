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
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --tier smoke --formats docx
conda run -n lightrag-memory-eval python -m memory_data_service.cli list
```

Generation has a default 3000-page safety guard because DOCX writing uses
`python-docx` in-process. Larger experiments must opt in explicitly:

```bash
conda run -n lightrag-memory-eval python -m memory_data_service.cli generate --profile rich --pages 3001 --allow-oversized-generation
```

`manifest.json` records `generation_peak_memory_mb` and a
`generation_resource_estimate` so large-run resource behavior is auditable.

The default `rich` profile creates a DOCX with chapter/section hierarchy,
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

- `POST /datasets`
- `GET /datasets`
- `GET /datasets/{dataset_id}`
- `GET /datasets/{dataset_id}/oracle`
- `GET /datasets/{dataset_id}/files/{name}`

PDF output uses `/opt/homebrew/bin/soffice` when available. If conversion fails,
the PDF file is marked as `skipped` in `manifest.json`.
