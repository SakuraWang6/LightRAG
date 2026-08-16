# 重构方案（REFACTOR_PLAN）

> 目标架构设计（Phase 2 产出，只读设计，未实施）。原则：Git 分支表达开发中的变化；YAML 表达实验；Git 历史 + runs 目录表达结果。

## 1. 目标模块结构

```text
LightRAG/
├── lightrag/                          # 通用稳定能力（默认行为尽量不因实验而变）
│   ├── chunker/token_size.py          # atomic table + row-safe split + sidecar（稳定）
│   │                                 # + 可选 retrieval views（由配置开启）
│   ├── operate.py                     # dense / exact-id / rerank（能力保留，rank 策略可插拔）
│   └── (config schema 入口)
│
├── memory_data_service/               # 数据集生成（现状不变）
├── memory_eval_tests/                 # 端到端产品测评（现状不变 + 47dfe6fa）
│
├── memory_recall_lab/                 # recall-only 实验体系（从 exp/recall-lab 提取）
│   ├── configs/                       # 实验配置（YAML）
│   ├── run.py                         # 入口
│   ├── retrieval.py                   # 召回评测
│   ├── ranking/                       # structured rank 等 ranking strategy
│   ├── audit/                         # ranking audit
│   ├── server.py / static/            # 对比 UI
│   ├── runs/                          # 结果（gitignored）
│   └── BRANCH_INVENTORY.md / CAPABILITY_MAP.md / REFACTOR_PLAN.md / BRANCH_CLEANUP_PLAN.md
│
└── tests/recall_lab/                  # harness 测试
```

## 2. 配置 Schema（`memory_recall_lab/configs/*.yaml`）

```yaml
# 实验名与元数据
experiment:
  name: r1_structured_ranker
  historical: false        # true 表示 legacy/historical 臂
  legacy_mode: false

chunking:
  table:
    mode: atomic           # atomic | fixed_token(legacy)
    atomic: true
    preceding_context: true
    row_safe_split: true
    sidecar_backfill: true

representation:
  table:
    raw: false             # A1：纯 JSON
    table_view: true       # C3
    row_view: true         # C3
    structured_envelope: false  # A3

retrieval:
  dense:
    enabled: true
  exact_id:
    enabled: true
    types: [FACT, EQ, REF, TBL, FIG]   # B0 为 []，B1 加 TBL/FIG

ranking:
  strategy: structured     # none | structured | rerank
  lexical_overlap: true

runtime:
  top_k: 20
  chunk_top_k: 20
  skip_kg: true
```

配置文件清单（对应历史实验）：

| 文件 | 语义 | 说明 |
| --- | --- | --- |
| a0_fixed_token.yaml | chunking.table.mode=fixed_token | `historical: true, legacy_mode: true`，若不能安全复现则只记录 commit/tag |
| a1_atomic_raw.yaml | raw=true, preceding_context=false | representation 实验 |
| a2_atomic_context.yaml | 默认（raw=false, preceding_context=true） | 即当前 main 默认 |
| a3_structured_envelope.yaml | structured_envelope=true | representation 实验 |
| b0_dense_only.yaml | exact_id.enabled=false | retrieval 实验 |
| b1_exact_id.yaml | exact_id.types 含 TBL/FIG | retrieval 实验 |
| c3_table_row_view.yaml | table_view/row_view=true | representation 实验 |
| r0_c3_exact_id.yaml | c3 + exact_id 全类型 | baseline |
| r1_structured_ranker.yaml | + ranking.strategy=structured | 当前最优 |

> 约束：A0 的 `if False:` 硬关 table-aware 路径不能直接变成默认代码路径。若 legacy fixed-token 需要复现，优先提供 legacy adapter 或记录历史 commit/tag，而不是污染默认 chunker。

## 3. 能力放置原则（Phase 2 决策）

| 能力 | 归属层 | 理由 |
| --- | --- | --- |
| structured ranker | ranking strategy（hook / strategy 注册），**默认不改变 LightRAG 核心检索输出**；实验路径通过 `ranking.strategy=structured` 启用 | 它依赖 row-view marker（实验表示），不是通用能力；直接硬编码在 `_get_vector_context` 会让默认行为随实验漂移 |
| exact-id recall | candidate retrieval capability（`retrieval.exact_id`），identifier 类型列表可配置；FACT/EQ/REF 默认开启（main 现状），TBL/FIG 由配置开启 | 精确 identifier 是通用检索机制，但 identifier 范围是实验策略 |
| table-view / row-view | representation layer（chunker 生成 retrieval views），**Evidence Object（atomic table + sidecar）永远保留**，views 只是附加检索表示 | 符合「Evidence Object ≠ Retrieval Representation」原则；views 由配置决定 |
| ranking audit | memory_recall_lab/audit/ | 它是实验分析工具，不属于 LightRAG 运行时 |
| timeout 按 figure 缩放 | memory_eval_tests/workflow.py | evaluation 可靠性修复 |
| 队列恒轮询 | lightrag_webui eval 前端（人工确认后） | UX 改进 |

## 4. Run Metadata Schema（增强现有 run.json）

现有 run.json 已含 `git_commit`、dataset manifest/oracle sha256、参数表、runtime snapshot。需要补齐：

```jsonc
{
  "code": {
    "git_commit": "…",
    "git_dirty": false,          // 新增：run 时工作区是否干净
    "branch": "refactor/…"        // 新增：run 时分支
  },
  "config": "configs/r1_structured_ranker.yaml",   // 新增
  "resolved_config": { /* 展开后的 capability 开关 */ },  // 新增
  "dataset": { "version": "verify-en-20p@<manifest_sha>", "generator_version": "…" },
  "metrics": { /* recall_report summary */ },
  "ranking": "ranking.json",
  "audit": "ranking_audit.json",
  "timestamp": "…"
}
```

旧 run 补齐规则：优先从 `run.json` 的 git_commit + README 消融表恢复实验语义；无法确定的能力开关标注 `reconstructed: true`，不冒充原始配置。

## 5. 迁移阶段（Phase 3-6 顺序）

1. 从 `exp/recall-lab` 提取 evaluation infrastructure（memory_recall_lab 主体 + tests/recall_lab）。
2. 从 `recall-c3` 提取 multi-view capability（table_view/row_view 作为配置化 representation）。
3. 从 exact-id 分支（b1/c3-exact-id）提取通用 identifier retrieval（types 配置）。
4. 从 `recall-r1` 提取 structured ranking capability（strategy + audit），与核心检索路径解耦。
5. 统一为 config-driven experiments（9 个 YAML + resolved config 写入 run 元数据）。
6. 重新运行关键 regression（A1/C3/R0/R1 smoke，R1 需复现 table_cell 100/100/100/1.0）。
7. 为历史 milestone 打 annotated tag。
8. 人工确认后删除不再需要的分支。

> 本方案所有代码变更在独立分支 `refactor/recall-experiment-config` 上进行，不直接在 main 上大改。
