# LightRAG Memory Evaluation Tests

This directory consumes datasets produced by `memory_data_service/`. It does not
generate documents. Implementation is grouped by responsibility; generated
reports remain in `runs/` and are deliberately outside the Python packages.

```text
memory_eval_tests/
├── common/       # DatasetClient, deterministic sampling, evidence normalization, auth HTTP helpers
├── offline/      # parser, integrity, provenance, layout and performance audits
├── online/       # API preflight, ingestion, retrieval and answer evaluation
├── experiments/  # 14 registered experiments + unified harness (run.py) and supervise watchdog
├── reporting/    # single-run, comparison, scale, readiness and baseline reports
├── tools/        # legacy run/report migration
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

Useful environment variables:

| Variable | Purpose |
| --- | --- |
| `MEMORY_EVAL_DATASETS_ROOT` | Dataset generation/read root; **required for wheel installs** (site-packages is usually read-only) |
| `MEMORY_EVAL_RUNS_ROOT` | Runs root shared by envelope invalidation and the console scan |
| `LIGHTRAG_API_KEY` / `LIGHTRAG_ACCESS_TOKEN` | `X-API-Key` / Bearer auth for online evaluation; persisted envelopes redact these to `configured` |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Server-side model endpoint/name for the console "AI analysis" |

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

When the LightRAG API enforces auth, pass `--api-key` / `--access-token` to
`api_preflight`, `index_runner`, `retrieval_eval` and `answer_eval` (both default
to the `LIGHTRAG_API_KEY` / `LIGHTRAG_ACCESS_TOKEN` environment variables).

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

## 看护运行（supervise）

长实验可用 `experiments/supervise.py` 看护：子进程崩溃（exit code ≠ 0）自动重启，
超过 `--max-restarts` 后放弃；重启时自动继承已有 `run.json` 的 `started_at` 与
`restarts`，跨 supervisor 重启（含 launchctl 拉起）不丢连续性。

```bash
conda run -n lightrag-memory-eval python -m memory_eval_tests.experiments.supervise \
  --experiment context_size \
  --dataset memory_data_service/generated/rich-smoke-v1 \
  --output-dir memory_eval_tests/runs/context-size-v2 \
  --supervision heartbeat --stale-minutes 60
```

### 心跳与挂死检测的职责边界

- **崩溃重启是默认能力**：子进程退出非零即重启；`supports_resume` 的实验
  （`context_size` / `kg_ablation`）会自动读 `partial.json` 续跑，其余从头重试。
- **挂死检测默认关闭**（`supervision="none"`）。LLM 单次调用挂死已由
  `chat_ollama` 的硬超时兜底，supervisor 的 stale-kill 价值有限，因此作为显式
  高级选项：`--supervision heartbeat` 启用后，监测子进程 `run.py` 每 30s touch 的
  `.heartbeat` 文件（证明“解释器活着”，能兜 GIL 阻塞类挂死），`run.log` 增长仅作
  辅助信号；`--stale-minutes` 默认 60。
- **阈值估算**：`--stale-minutes` 应大于最坏单阶段耗时。参考
  `--num-ctx` × 每 token 耗时（慢机器 16K 上下文单次生成可达数十分钟）；chat 层
  超时（`--timeout 1800` 等）已单独兜底，不要在 heartbeat 阈值上过度激进。

### 行为说明

- 子进程以独立进程组运行（`start_new_session=True`），收到 SIGINT/SIGTERM 时对
  整棵进程树（含 online_baseline 的 retrieval/answer 子进程）先 SIGTERM、30s 后
  SIGKILL，避免孙进程变孤儿继续写 output 目录。
- supervisor 事件与子进程输出统一写入 `run.log`；每次重启会把
  `progress.json.message` 置为“第 N 次重启（续跑/重试）”作为瞬时提示，
  envelope 的 `restarts` / `last_restart_resume` 字段与 run.log 事件是权威记录
  （console 徽标显示重启次数与最后一次是续跑还是从头重试）。
- `output_dir/.supervise.lock` 保证同一 output-dir 只允许一个看护进程；重复启动
  直接报错退出。
- 默认会剥离 `http(s)_proxy` 等代理环境变量（避免本地 Ollama 被代理干扰）；
  包装走外部 API 的实验（如 `frozen_prompt_llm_eval`）且网络需要代理时，加
  `--keep-proxy` 保留代理变量（本地地址仍走 `NO_PROXY`）。
- `run.py` 单独直跑（不经 supervise）收到 SIGTERM 时只做优雅收尾（写
  progress、中断 runner 走 finally），**不会**清理它 spawn 的子进程；需要整棵
  进程树清理时请通过 supervise 接收信号（`start_new_session` + `killpg`）。
- launchctl 示例：`KeepAlive` 配合 `SuccessfulExit: false`，进程退出即由系统拉起，
  supervisor 启动时会继承旧 `run.json` 的 `started_at`/`restarts`。

## WebUI 评测工作台

评测控制台包含三个子视图：

- **运行**：runs 列表、详情（指标/逐题/报告/日志）、对比与导出、取消活跃运行、
  一键复现。
- **新建运行**：选数据集 → 选实验（显示看护/续跑能力与环境变量就绪状态）→
  填参数（通用字段 + 实验 `extra_schema` 高级参数）→ 启动（可选看护）。
- **数据集**：列表（tier/页数/模态/文件数/生成时间）、表单化生成（含资源预估与
  pages 上限提示）、删除（生成中的数据集拒绝删除，返回 409）。

后端接口：`GET /eval/experiments`、`POST/GET /eval/jobs`、`POST
/eval/jobs/{id}/cancel`、`GET/DELETE /eval/datasets`（生成走
`POST /eval/jobs` 的 `kind=dataset`）、`GET/POST/DELETE /eval/templates`。
作业状态以 `runs/.jobs/<job_id>/job.json`
（pid + 进程启动时间）为准，API 重启后可恢复取消；job.json 不存凭据。
数据集生成走同一 job 通道（`kind=dataset`），默认 pages 上限 1000，
`allow_oversized_generation` 可放开；`/eval` 依赖随 wheel 打包的
`memory_eval_tests` / `memory_data_service` 包，包缺失时返回 503。

并发与队列：作业按 FIFO 排队，`MEMORY_EVAL_MAX_ACTIVE_JOBS`（默认 1）控制
同时运行数，`MEMORY_EVAL_WAIT_FOR_RUN` 可让队列等待指定 run 完成后自动启动。
后端队列已实现；前端“作业/队列视图”尚未提供（排队中的 job 目前不在 UI 可见）。

### 数据集根目录

数据集的生成与读取统一走 `MEMORY_EVAL_DATASETS_ROOT` 环境变量（未设置时回落包内
`memory_data_service/generated`）。**打包安装（wheel）后该目录位于 site-packages，
通常只读，必须显式设置 `MEMORY_EVAL_DATASETS_ROOT`**；本地开发可不设置。
