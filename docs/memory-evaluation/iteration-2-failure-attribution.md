# I2（P0）：分阶段失败归因

## 目标

把“回答错误”转化为有证据支持的 LightRAG 阶段结论。平台必须区分解析、切块、
索引、召回、证据选择、上下文截断与生成失败；无法区分时必须诚实标记待复核。

## 用户故事

- 作为实验用户，当 Recall@K 为 100% 但回答错误时，我想知道证据是否真的进入了
  回答模型上下文。
- 作为 LightRAG 开发者，我想按失败环节聚合 case，以确定应优化解析、检索还是回答。
- 作为模型选型者，我想确认“换回答模型”是否能解决当前失败。

## 功能需求

### FR-2.1 逐题可追溯工件

每道测试题的结果必须关联以下工件 ID 或安全截取：

1. oracle 事实、答案、证据对象和来源位置；
2. 已解析对象与对应 chunk；
3. 索引/图谱中可查的对象标识；
4. 原始 Top-K 候选及排名和分数；
5. 重排或证据选择后进入最终上下文的内容、顺序、token 数和截断信息；
6. 最终回答、引用、拒答判定；
7. 使用 oracle 金标准证据的上限回答（适用时）。

不得仅用回答 API 的摘要 references 推断最终上下文；若服务端无法暴露该信息，必须
扩展受控评测 trace 接口或标为“上下文不可观测”。

### FR-2.2 标准归因分类

系统至少产生以下 `primary_cause`：

- `parse_missing`：oracle 内容未在解析产物中保留；
- `chunk_missing`：已解析但没有可承载完整证据的 chunk；
- `index_missing`：有效 chunk 未进入应查询的索引；
- `retrieval_miss`：有效证据不在指定 Top-K 内；
- `selection_or_truncation_miss`：Top-K 有证据但最终上下文无足够证据；
- `generation_or_prompt_failure`：最终上下文充分，答案仍错误；
- `abstention_failure`：可答题错误拒答或不可答题未正确拒答；
- `oracle_or_scorer_uncertain`：真值或评分无法可靠裁定；
- `unclassified`：所需 trace 缺失或规则不适用。

每个分类必须附 `evidence`、规则版本与置信度；不允许将剩余错误一律归因于模型。

### FR-2.3 金标准证据上限

- 将既有 `oracle_upper_bound` 标准化为诊断阶段：给同一个回答模型、同一提示词、同一
  解码参数提供足够的 oracle 证据。
- 上限实验必须与端到端 run 关联，而不是作为无关联的独立报告。
- 若上限回答也失败，归因候选限定为回答模型、提示词、问题/真值或评分；报告不得责怪
  召回。

### FR-2.4 汇总与导出

- run 报告按题型、模态、检索模式和失败分类展示 case 数与占比。
- 支持从失败分类跳转到逐题 trace，并导出脱敏 JSON/CSV。
- 明确展示未覆盖率：缺 trace 或不适用的题不得静默从分母中消失。

## 数据与接口变更

- 定义版本化 `case_trace.json` 和 `diagnosis.json` schema。
- 在评测请求增加仅供受控实验使用的 trace 标志；生产普通问答接口默认不返回完整上下文。
- 运行结果增加 `diagnosis_coverage`、`cause_distribution` 与 `trace_availability`。

## 非目标

- 本迭代不要求自动修复 LightRAG。
- 本迭代不以 LLM 自由文本分析替代确定性归因规则；LLM 可提供建议，但不能覆盖规则结论。

## 验收标准

- 构造四类人为故障（解析删除、Top-K 截断、上下文截断、回答模型替换）后，系统分别
  给出对应主因。
- 100% Recall 但最终上下文不含证据的 case 被归为 `selection_or_truncation_miss`。
- 每个 `generation_or_prompt_failure` 都能查看实际最终上下文与 oracle 上限回答。
- 诊断汇总中显示有效归因、待复核与不可观测 case 的完整分母。
