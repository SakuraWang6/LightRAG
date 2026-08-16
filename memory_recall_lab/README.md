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

## Retrieval strategy and multi-view experiments

On the A2 chunker (`exp/recall-lab`) with `naive` mode and top 20:

| Arm | Branch | Change | Table-cell R@1 | Table-cell R@3 | Table-cell R@5 | MRR |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| B0 | `recall-b0-dense-only` | disable explicit-id recall | 0.0% | 28.6% | 57.1% | 0.222 |
| A2/Baseline | `exp/recall-lab` | FACT/EQ/REF exact-id + dense | 0.0% | 28.6% | 57.1% | 0.222 |
| B1 | `recall-b1-exact-id-table` | add TBL/FIG exact-id | 0.0% | 28.6% | 100.0% | 0.283 |
| C3 | `recall-c3-table-row-view` | table view + row view | 14.3% | 14.3% | 14.3% | 0.265 |
| C3 + B1 | `recall-c3-table-row-view-exact-id` | row views + TBL exact-id | 14.3% | 85.7% | 100.0% | 0.481 |

The combination of row views and table-id exact recall recovers table-cell
Recall@3 to the legacy fixed-token level while keeping the underlying table
object intact. Recall@1 remains the open problem, which points to a ranking
step rather than more chunk-context tuning.

## Ranking stage (R0 / R1)

Baseline is fixed at `C3 + exact-id` (`recall-c3-table-row-view-exact-id`).
The R1 branch keeps candidate generation identical and adds a structured ranker
in `lightrag/operate.py`:

```text
explicit FACT-ID match
  > TBL-ID match + row-view
  > TBL-ID match + table-view/raw
  > other candidates
```

Within the same TBL row-view tier, a cheap lexical-overlap score is used as a
proxy for semantic row ranking.

| Arm | Branch | Table-cell R@1 | R@3 | R@5 | MRR |
| --- | --- | ---: | ---: | ---: | ---: |
| R0 | `recall-c3-table-row-view-exact-id` | 14.3% | 85.7% | 100.0% | 0.481 |
| R1 | `recall-r1-structured-ranker` | **100.0%** | **100.0%** | **100.0%** | **1.000** |

The R1 result exceeds the legacy fixed-token table-cell R@1 (57.1%) without
changing the atomic table object or C3 retrieval views.

Run the ranking audit on an existing run:

```bash
conda run -n lightrag-memory-eval python -m memory_recall_lab.audit_ranking \
  --run-dir memory_recall_lab/runs/r1-structured-ranker-top20
```

This writes `ranking_audit.md` and `ranking_audit.json` into the run directory.
