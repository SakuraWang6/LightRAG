# Recall Lab

Recall-only experiment harness for LightRAG retrieval. It starts the same
isolated execution unit as the product evaluation framework, uploads and
indexes a dataset, then stops before answer generation. Every run writes:

- `recall_report.json` — full per-question metrics, candidates, and hits
- `ranking.json` — the same data under a stable, UI-friendly schema
- `report.md` — human-readable summary grouped by question type

Because recall does not need a full KG, the fast iteration command uses
`naive` mode and `--skip-kg`. This keeps the experiment focused on chunk
representation and vector ranking.

## Run one recall experiment

```bash
conda run -n lightrag-memory-eval python -m memory_recall_lab.run \
  --dataset memory_data_service/generated/verify-en-20p \
  --output-dir memory_recall_lab/runs/<run-name> \
  --label "<human label>" \
  --mode naive \
  --top-k 20 \
  --chunk-top-k 20 \
  --skip-kg
```

## Open the comparison UI

```bash
conda run -n lightrag-memory-eval python -m memory_recall_lab.server \
  --host 127.0.0.1 \
  --port 8710 \
  --runs-root memory_recall_lab/runs
```

Open <http://127.0.0.1:8710>. The UI compares selected runs across overall and
per-question-type Recall@1/3/5, MRR, gold-rank distribution, and exposes every
question's ranked candidate list.

## Table representation ablation (verify-en-20p, naive, top 20)

Each arm was implemented on its own branch and run with the identical dataset,
embedding (`bge-m3`), Top-K, and query settings:

| Arm | Branch | Table representation | Table-cell R@1 | Table-cell R@3 | Table-cell R@5 | Overall R@1 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| A0 | `recall-a0-old-fixed-token` | legacy fixed-token windows | 57.1% | 85.7% | 85.7% | 55.4% |
| A1 | `recall-a1-atomic-raw` | atomic raw JSON table | 0.0% | 0.0% | 0.0% | 29.2% |
| A2 | `exp/recall-lab` (current) | atomic table + preceding context | 0.0% | 28.6% | 57.1% | 32.3% |
| A3 | `recall-a3-structured-envelope` | structured envelope around atomic table | 0.0% | 14.3% | 14.3% | 28.5% |

Interpretation: A0 has the best ranking because legacy windows put table
headers, prose, and answer rows together; atomic JSON representations preserve
evidence integrity but are poor dense-retrieval targets. A2 restores more
table-cell recall than A1/A3, but still does not recover the legacy fixed-token
ranking. This is the expected starting point for the next stage: separate
evidence objects from retrieval views (table view / row view).
