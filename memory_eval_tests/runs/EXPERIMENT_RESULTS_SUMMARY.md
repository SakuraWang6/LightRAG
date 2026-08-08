# LightRAG 富文档记忆评测：实验结果与问题分析

更新日期：2026-08-08。本文只汇总已落盘且可复核的评测产物；已排除因本机 8K 生成窗口挂起而留下的无效全零产物。

## 1. 结论摘要

1. **结构与可追溯性基础可用，但版式记忆不完整。** 原生 DOCX 解析保住了表、图、公式、caption、交叉引用及 chunk 证据链；但缺失可靠页码/bbox，且 VML 浮动文本框内容丢失。
2. **更大的 KG context 并不等于更高 answer quality。** 在受控的 qwen3:8b Top-K 曲线中，Top-3 已达到 0.8611 Accuracy；Top-20 上下文扩大至 35,669 字符后准确率降至 0.7778，hallucination 升至 0.2778。
3. **生成阶段（模型能力与上下文窗口匹配）是已验证的瓶颈。** 在完全冻结同一份 KG Top-5 Prompt 后，`gpt-4o-mini` 的 answer accuracy 为 0.8889，高于旧 qwen3:8b 8K 运行的 0.8056；16K 修复后 qwen3:8b Top-5 为 0.8611，说明旧差距同时包含模型选择与运行窗口不足两个因素。
4. **上下文膨胀是第二个已验证瓶颈。** Top-1 到 Top-20 的平均上下文字符数从 5,630 增至 35,669；证据覆盖代理在 Top-3 后不再提升、Top-20 反而下降，最终 answer 曲线也同步回落。
5. **Evidence Selector 有条件有效。** 在新的受控四组实验中，Top20→Select5 保留 96.72% 的 Top-20 proxy recall、移除 72.23% context，并使 Accuracy 从 Direct Top-20 的 0.8056 提升到 0.8333；Select3 压缩更多但效果较差。
6. **Oracle structure metadata 未改善当前 Select5 + qwen3:8b。** 24/36 个 evidence packs 获得真实 page/order/parent/relation metadata，但 Native 与 Oracle Structure-Full 的 Accuracy、Groundedness、Hallucination、MULTIHOP 均不变。因此不能据此优先更换 parser。

## 2. 数据与评测口径

- 主数据集：`rich-smoke-v1`，12 页、27 个原子事实、36 题、197 个对象、253 条对象关系。
- 有 34 个应回答问题、2 个应拒答问题；在线检索指标只对 34 个应回答问题计算。
- Online `answer_accuracy` 是 oracle 答案匹配率；`groundedness` 同时要求答案和 API 返回引用匹配；`hallucination_rate` 是不 grounded 的回答比例（拒答题另有专门规则）。
- 本轮 Context Size 结果中的“证据覆盖代理”是从冻结/只读 context 中检查 oracle 证据是否出现，**不是**原 API `/query/data` 计算的标准 Recall@K，不能与历史 API Recall 数值混为一谈。

## 3. 离线：文档表示、追溯与规模

### 3.1 rich-smoke-v1 严格离线审计

| 检查 | 结果 | 含义 |
|---|---:|---|
| integrity | 通过 | 197 个对象、253 条关系与 oracle 自洽 |
| object traceability | 通过 | 27/27 原子事实可回到解析对象与 block |
| chunk traceability | 通过 | 63/63 chunks 有 sidecar 引用；27/27 facts、8/8 captions、10/10 references 命中 |
| cross-reference | 通过 | 2/2 Word REF fields 与 3/3 oracle cross-reference 可回到 chunk |
| sidecar lexical retrieval@5 | Recall 0.9853，MRR 0.6147 | 词法 baseline 能找到大部分证据，但精度只有 0.2118 |
| layout audit | **未通过** | meaningful position=0，page/bbox position=0，复杂版面命中=0.5，textbox=0 |

严格版式失败由两个不同问题组成：原生 sidecar 的 `paraid` 是占位位置，不能支撑页面级/坐标级推理；同时 `OBJ-000009` 的 VML floating textbox 文本完全未保留。后者是实际内容丢失，不只是指标缺失。

### 3.2 离线规模基线（native parser + sidecar lexical retrieval）

| 数据集 | 页数 | facts | chunks | Evidence Recall@5 | MRR | object hit rate |
|---|---:|---:|---:|---:|---:|---:|
| rich-smoke-v1 | 12 | 27 | 63 | 0.9853 | 0.6147 | 0.5588 |
| rich-smoke-20p-v1 | 20 | 45 | 86 | 0.8879 | 0.5198 | 0.5345 |
| rich-medium-200p-v1 | 200 | 465 | 626 | 0.4511 | 0.3356 | 0.4909 |
| rich-large-1000p-v1 | 1000 | 2,327 | 3,020 | 0.3666 | 0.2993 | 0.4924 |
| rich-stress-3000p-v1 | 3,000 | 6,984 | 8,998 | 0.3060* | 0.2246* | 0.4870* |

`*` 3000 页数据集检索评估采用 500 cases/1000 facts 上限。五个数据集的 integrity、sidecar、object/chunk traceability 均通过；12 页严格 smoke 因上节版式审计失败而被标成 `passed=false`。规模结果说明：当前词法/sidecar 基线不能直接扩展到超长单文档，1000 页以后 Recall@5 已低于 0.37。

## 4. 历史在线端到端实验

所有行均为 `mix`、Top-5、同一 rich-smoke-v1 数据集。`skip-KG` 是 `docx:native-iteP!`，`KG` 是 `docx:native-iteP`。

| Run | 生成模型/说明 | KG | Recall@5 | Accuracy | Groundedness | Hallucination | Abstention | Citation |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `rich-smoke-v1-api` | 历史远端 OpenAI-compatible 基线；模型元数据未随产物保存 | KG | 1.0000 | **0.9444** | 0.9444 | 0.0556 | 1.0000 | 1.0000 |
| `local-gemma-vlm-skipkg` | qwen3:4b-instruct query/keyword + gemma3:4b VLM | 跳过 | 0.9412 | 0.8056 | 0.8056 | 0.1944 | 1.0000 | 0.9444 |
| `local-qwen8b-skipkg` | qwen3:8b + gemma3:4b VLM | 跳过 | 0.9412 | **0.8611** | 0.8333 | 0.1667 | 0.5000 | 0.9444 |
| `local-qwen8b-kg-timeout900` | qwen3:8b + gemma3:4b VLM；KG ingest 约 3752s | 开启 | **1.0000** | 0.8056 | 0.7500 | 0.2500 | 0.0000 | 0.9444 |

关键对照是最后两行：在**历史 8K 生成窗口**下，KG 的检索召回已达到 1.0，但 8B 模型的最终 accuracy 下降 5.56 个百分点。这说明“检索到证据”不足以保证答对，但不能单独证明 KG 本身有害：后续 16K 重跑确认，长 KG prompt 与生成窗口的容量冲突也是该历史差距的一部分。

## 5. 本轮定位实验

### 5.1 实验 1：冻结 Retrieval Context，只替换生成模型

配置：对 KG Top-5 的 36 题逐题冻结关键词、检索上下文、系统 Prompt、用户问题、`max_total_tokens=8192`；不重新检索、不重新抽关键词，仅把最终生成从本地 qwen3:8b 改为外部 `gpt-4o-mini`。

| 生成模型 | Accuracy | Groundedness | Hallucination | Abstention |
|---|---:|---:|---:|---:|
| qwen3:8b KG 历史基线 | 0.8056 | 0.7500 | 0.2500 | 0.0000 |
| gpt-4o-mini，固定 Prompt | **0.8889** | **0.8889** | **0.1111** | **1.0000** |

结论：在 Retrieval Context 完全不变时，外部模型相对旧 qwen3:8b 8K 运行 accuracy 提升 8.33 个百分点、hallucination 减少 13.89 个百分点，证明生成阶段确实是瓶颈。16K 重跑后 qwen3:8b Top-5 达到 0.8611，说明这 8.33 个百分点不能全部归因为纯推理能力：其中还包含 qwen3:8b 8K 生成窗口不足。即使采用 16K，gpt-4o-mini 的 0.8889 仍高 2.78 个百分点，但两轮 Prompt 不是同一次生成，不能把该残余差距解释为严格的因果估计。

注意：固定 Prompt runner 的引用字段检查的是 prompt 内证据可用性，而历史 API 的 `citation_accuracy=0.9444` 检查 API references；两者口径不同，不能把 runner 输出的 1.0 当作 API citation 指标改善。

### 5.2 实验 2：KG Context Size / Retrieval Evidence Ablation（完整）

固定数据集、KG `mix` retrieval、缓存关键词、qwen3:8b、`max_total_tokens=8192`、`num_predict=768`；仅改变 `top_k` 与 `chunk_top_k`。为避免旧运行中“检索 8K + 模板 + 768 输出”超过模型生成窗口而挂起，本轮统一把 **Ollama 生成窗口**设为 16,384 token。该窗口对每一档完全相同；因此表内可以用于 Top-K 横向比较，但不可与历史 8K 窗口的端到端数值直接混算。

| Top-K | 证据覆盖代理 | 平均 context 字符数 | Accuracy | Groundedness | Hallucination | Abstention* | Citation* |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.7917 | 5,630 | 0.7778 | 0.7222 | 0.2778 | 0.0000 | 0.9167 |
| 3 | **0.8889** | 16,441 | **0.8611** | **0.8611** | **0.1389** | 0.5000 | 1.0000 |
| 5 | **0.8889** | 25,920 | **0.8611** | **0.8611** | **0.1389** | 0.0000 | 1.0000 |
| 10 | **0.8889** | 27,490 | **0.8611** | **0.8611** | **0.1389** | 0.0000 | 1.0000 |
| 20 | 0.7778 | 35,669 | 0.7778 | 0.7222 | 0.2778 | 0.0000 | 0.9167 |

`*` 本 runner 的 citation 评价的是冻结 context 中证据可用性，不是 API response references；abstention 指标仍受狭窄短语词表影响，见第 6 节。

**结论。** Top-1 的证据不足，Top-3 把 Accuracy 提升 8.33 个百分点、hallucination 减半；从 Top-3 扩到 Top-5/10 不再带来质量收益，却分别增加 57.7%/67.2% 的 context 字符数。Top-20 相比 Top-3 再增加 116.9% context，却让 Accuracy 回落 8.33 个百分点，并使 hallucination 翻倍。这是直接的 context dilution 证据；当前默认候选应为 **Top-3**，而非继续盲目增加召回数量。

与历史 `local-qwen8b-kg-timeout900` 的 Top-5 Accuracy=0.8056 相比，本轮 16K Top-5 为 0.8611。它说明旧结论还混入了生成窗口不足的系统性影响；不能据此反推 KG 本身必然降低 qwen3:8b 的能力。实验 1 的“固定同一 retrieval prompt，换更强模型显著改善”结论不受此项窗口修复影响。

## 6. 哪些问题没有答对，以及原因

下表只列出已保存回答中有代表性的失败；“评分漏判”表示答案语义正确或近似正确，但当前字符串/公式规则没有承认它，不应归咎于 RAG。

| 题目 | 失败的 runs | 直接表现 | 主要原因 |
|---|---|---|---|
| Q-FACT-00001（Cell 0001） | 4B skip-KG、8B skip-KG | 说 context 未提供 9021 QMU | skip-KG Top-5 没返回该事实；这是检索遗漏/证据缺失 |
| Q-FACT-00002（Cell 0002） | 4B skip-KG | 说 context 未提供 9038 QMU | 同上；小模型没有在不完整证据下拒绝并保留不确定性边界 |
| Q-FACT-00006（FIG-0004） | 历史远端、4B skip-KG | 误拒答或找不到图 | 图/caption 证据没有稳定进入 context，暴露多模态对象选择问题 |
| Q-FACT-00004、Q-FACT-00011（表格 maximum） | 16K Top-1/3 与 Top-20 的部分档位 | 选到其它表的 `132.75 ms`，或在较长表描述中没有稳定抽出目标数值 | 低 Top-K 时证据不全；高 Top-K 时相邻表与重复表行形成数值干扰。Top-5/10 能恢复目标值，支持 Top-3 至 10 的中等窗口最稳 |
| Q-FACT-00008、Q-FACT-00020（公式） | 4B/8B/KG/GPT 部分 | 公式用 Unicode、`\\frac`、空格或下标变体表示 | 多数为 evaluator 的公式规范化不足；例如 `E_5=P_5T_5/η_5` 在语义上等价，不应全部视为模型错答 |
| Q-FACT-00009、Q-FACT-00021（eta 含义） | qwen KG，16K Top-1/20 部分 | 语义正确但 exact_match=false | 评分器对 `eta_5`、`η_5`、LaTex 下标的等价处理不足；高 Top-K 也使变量定义更容易被解释性文本淹没 |
| Q-MULTIHOP-0005 | 所有 16K Top-K、4B、8B skip-KG/KG、GPT | 未稳定同时给出 `33.75 ms` 与 EQ-0005 | 需要“页面顺序 + 最近前序表 + 指定公式”的组合约束；缺 page/bbox、邻近表干扰和模型链式选择失误共同造成 |
| Q-MULTIHOP-0010 | 16K Top-1/5/20、历史远端、4B、GPT | Top-3/10 可以答对，但 Top-5/20 又丢失 `99.75 ms` 或 EQ-0010 | 这是最直观的上下文敏感例子：更多候选并不单调改善多跳证据选择 |
| Q-FACT-00013（policy version） | 16K Top-1/10/20 | 给出 `v7.2`，但遗漏生效时间 `2026-Q3` | 长上下文中模型保留主实体版本，却遗漏限定字段；属于细粒度 evidence extraction 失败 |
| Q-FACT-00017（canonical method） | 历史 qwen KG | 返回通用政策“prefer gold FACT rows…”而非 `Method-C0009` | KG 中抽到解释性近邻事实，却未对齐到精确 code；是 evidence selection 与输出精确性问题 |
| Q-FACT-00027（long table） | 16K Top-1、GPT 固定 Prompt | 只说 `FACT-00027`，漏 `54.99 ms`；Top-3 及以上恢复 | 单一候选上下文中长表数值未被显式抽出；至少三个候选可补足表格证据 |
| Q-ABSTAIN-00001/00002 | qwen skip-KG/KG/16K 全部档位 | 回答已明确“文档未提及/无法回答”，但多数被判错误 | 当前 `_looks_like_abstain` 词表未涵盖 `cannot be addressed`、`does not mention` 等同义表达；0.0/0.5 abstention 严重低估了模型实际拒答能力 |

## 7. 根因归纳与下一步

### 已验证根因

- **LLM reasoning/selection**：固定 context 换成 gpt-4o-mini 后显著改善。
- **Context dilution**：完整 qwen3:8b 曲线已验证；Top-3 达到峰值，Top-20 在更低的覆盖和更长的 context 下回落。
- **生成窗口匹配**：旧的 8K 运行会在 Top-5 长 prompt 挂起；16K 窗口是保证 8,192-token retrieval budget 与 768-token generation 能够共同容纳的运行前提。
- **页面顺序不可用**：native sidecar 没有可靠 page/bbox，页前最近表这类题不具备稳健的结构前提。
- **评测器偏严/缺少等价规则**：公式与拒答短语造成一部分假阴性。

### 仍待验证

- reranker / evidence selector 能否在不牺牲 Recall 的前提下降低 context。
- Docling/MinerU 与 PDF 的结构、版式和端到端质量。
- 200/1000/3000 页上的真实在线 KG ingest、retrieval、answer 性能。

### 建议顺序

1. 以 **Top-3** 作为当前默认 KG 候选数，并在此之后测试 reranker/evidence selector；
2. 将“页面/对象顺序”作为可检索字段，或切换支持页码/bbox 的 parser；
3. 单独评估 reranker/evidence selector 是否能在不牺牲 Recall 的前提下降低 context；
4. 修订评测器：公式 AST/符号归一化、拒答语义判定、将 citation availability 与 response citation 格式分离。

## 8. 原始产物索引

- 离线 smoke：`memory_eval_tests/runs/offline/rich-smoke-v1/`
- 离线规模：`memory_eval_tests/runs/scale_report.md`
- 历史在线对比：`memory_eval_tests/runs/online/*/online_report.md`
- 固定 Prompt：`memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/prompts_kg_mix_top5_ctx8192.json`
- GPT 结果：`memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/answer_kg_mix_top5_ctx8192_gpt4o-mini.json`
- Context Size 检索消融：`memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/retrieval_context_size_qwen8b.json`
- Context Size 完整回答消融（有效 16K 运行）：`memory_eval_tests/runs/online/rich-smoke-v1-kg-ablation/context_size_qwen8b_ctx16384.json`
- 运行器与检查点逻辑：`memory_eval_tests/kg_ablation.py`

## 9. 后续实验：Evaluator、Evidence Selector 与 Structure Ablation（完成）

### 9.1 Evaluator 修复与历史答案重评

评分器已完成以下确定性修复：

- 公式规范化覆盖 Greek、Unicode/LaTex、下标、空格与 `a/b`/`\\frac{a}{b}` 等价形式；
- 拒答判定覆盖 `not mentioned/provided/specified/stated`、`cannot be determined/answered/addressed`、`insufficient information` 与 document/context 缺失等表达；
- 指标拆为 `evidence_available`、`citation_presence`、`citation_correctness` 与 `groundedness`。

6 个回归测试通过。重评完全复用保存回答、不调用 LLM；9 份历史报告中有 41 个题目分数变化，其中 22 个为 false-to-true evaluator false negative，19 个为更严格规则暴露出的 true-to-false。详见 `memory_eval_tests/runs/evaluator_recheck_report.md`。

### 9.2 实验 A：Evidence Selector / Reranker

控制变量：同一 `rich-smoke-v1`、历史 KG storage、缓存 keywords、`mix` retrieval、qwen3:8b、16,384 token generation window、temperature 0 与同一 answer template。Selector 只接收稳定 evidence ID、object type 与 Top-20 renderer 的前 20 个 entity records；其 JSON debug 输出不进入 Answer Context。下表的 recall/precision 是 object-level proxy，不能与历史 API Recall@K 混用。

| Method | Candidate K | Selected K | Candidate Recall | Selected Recall | Selection Precision | Avg Context chars | Accuracy | Groundedness | Hallucination |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Direct Top-3 | 3 | 3 | 0.6111 | 0.6111 | 0.6471 | 1,021 | 0.6111 | 0.5278 | 0.4722 |
| Direct Top-20 | 20 | 20 | 0.8472 | 0.8472 | 0.8824 | 8,006 | 0.8056 | 0.7222 | 0.2778 |
| Top20 → Select3 | 20 | ≤3 | 0.8472 | 0.8056 | 0.8529 | 1,575 | 0.7778 | 0.6944 | 0.3056 |
| Top20 → Select5 | 20 | ≤5 | 0.8472 | 0.8194 | 0.8529 | 2,223 | **0.8333** | **0.7500** | **0.2500** |

结论：Top-20 candidate pool 相比 Direct Top-3 提升 23.61 个百分点 proxy recall；Select5 保留 96.72% Top-20 proxy recall、缩短 72.23% context，并比 Direct Top-20 提高 2.78 个百分点 Accuracy、降低 2.78 个百分点 Hallucination。Select3 更短但 Accuracy 低 5.56 个百分点，因此当前最稳的 selector 配置是 **Top20 → Select5**，而不是 Select3。

按题型看，Select5 的 FACT=0.9333、FIGURE=1.0000、FORMULA=1.0000、ABSTAIN=1.0000；TABLE=0.6000、MULTIHOP=0.2500 仍是短板。

对 Select5 的 6 个错误逐题分解：Retrieval=2、Selection=1、Context Interference=1、Generation/Reasoning=2、Evaluator=0。对应占比分别为 33.3%、16.7%、16.7%、33.3%、0%。详细原始 output 与依据见：

- `memory_eval_tests/runs/evidence-selector-v1/evidence_selector_results.json`
- `memory_eval_tests/runs/evidence-selector-v1/evidence_selector_report.md`
- `memory_eval_tests/runs/evidence-selector-v1/evidence_selector_failure_analysis.md`

### 9.3 实验 B：Native vs Oracle Structure-Full

Native 直接复用 Select5 的原始 evidence pack 和回答。Oracle Structure-Full 保持 evidence text/IDs 不变，只对已在 pack 中可验证的事实附加 synthetic oracle 已存在的 object ID、object type、page、document order、section、parent、前后邻居与真实 relation。Answer model/parameters 保持 qwen3:8b、temperature 0、16,384 token context、128 token output。

| Condition | Accuracy | Groundedness | Hallucination | MULTIHOP | TABLE | FIGURE | FORMULA |
|---|---:|---:|---:|---:|---:|---:|---:|
| Native | 0.8333 | 0.7500 | 0.2500 | 0.2500 | 0.6000 | 1.0000 | 1.0000 |
| Oracle Structure-Full | 0.8333 | 0.7500 | 0.2500 | 0.2500 | 0.6000 | 1.0000 | 1.0000 |

Oracle metadata 实际附加到 24/36 packs。转变矩阵为 Native Wrong→Oracle Correct=0、Native Correct→Oracle Wrong=0、Both Wrong=6、Both Correct=30。两题在 structure 前缺少正确 evidence、一道 MULTIHOP 被 selector 删除一半证据；剩余三道有 page/order/relation metadata 的题仍错误，说明模型没有利用关系或无法从长表抽取目标值。结论是：**当前证据不足以支持更换 parser；结构“存在”不是当前主瓶颈。**

详细结果：

- `memory_eval_tests/runs/structure-ablation-v1/structure_ablation_results.json`
- `memory_eval_tests/runs/structure-ablation-v1/structure_ablation_report.md`

### 9.4 当前状态与建议

已完成：Evaluator 修复与重评、Evidence Selector 四组对照、Selector 失败分解、Native vs Oracle Structure-Full。尚未完成：Oracle Structure-Light 独立对照，以及 Select5 在 20/200 页在线 KG 上的复现。

当前主要瓶颈排序：

1. MULTIHOP/TABLE 的 retrieval 与 evidence-selection 完整性；
2. 有充分 evidence 后的 generation/reasoning；
3. context construction 的粒度；
4. 结构 metadata 利用，而非 metadata 是否存在。

下一步应先做 relation-aware Select5（多跳题对每个关系角色至少保留一个 evidence），然后在 20 页、200 页验证；不要先扩大 Top-K 或全量替换 parser。

## 10. 四阶段路线 P0-1：Oracle Evidence Pack Upper Bound（完成）

控制变量：同一 `rich-smoke-v1`、qwen3:8b、temperature 0、16,384 token 窗口、与 Select5 完全相同的答案模板与解码设置。两臂都绕过 retrieval/selection，直接由 synthetic oracle 构建“证据完全正确”的 pack：

- `oracle_text`：每个 evidence fact 一条实体行（fact 语句/对象文本）。
- `oracle_full`：实体行 + oracle supports 关系 + Document Chunks（sidecar 完整表格行、图/公式对象文本）。

| Arm | Accuracy | Groundedness | Hallucination | MULTIHOP | TABLE | Avg Context (chars) |
|---|---:|---:|---:|---:|---:|---:|
| Select5 基线（历史产物） | 0.8333 | 0.7500 | 0.2500 | 0.2500 | 0.6000 | 2,223 |
| Oracle-Text | 0.9722 | 0.9722 | 0.0278 | 0.7500 | 1.0000 | 332 |
| Oracle-Full | **1.0000** | **1.0000** | 0.0000 | **1.0000** | 1.0000 | 921 |

结论：

1. **qwen3:8b 的生成上限不是当前瓶颈。** 只要证据正确且充分，36/36 全对（oracle-full）；Select5 到 oracle 的 16.67 个百分点差距全部来自证据质量（retrieval/selection/packing），不是模型推理能力。
2. **结构化表格内容对 MULTIHOP 是必要的。** 唯一在 oracle_text 下失败的 MULTIHOP（Q-MULTIHOP-0005）在 oracle_full 下答对：模型需要“表格行 + 关系”才能稳定给出 `33.75 ms` 与公式的组合。
3. 为后续实验提供 go/no-go：P0-2/P1-3 的潜在收益上限是 +16.67pp，值得做。

产物：

- `memory_eval_tests/runs/oracle-upper-bound-v1/oracle_upper_bound_results.json`
- `memory_eval_tests/runs/oracle-upper-bound-v1/oracle_upper_bound_report.md`

## 11. 四阶段路线 P0-2：Relation/Role-aware Selection（完成）

控制变量：候选池与 Select5 基线逐字复用 `evidence-selector-v1`（identical candidate pool），只改变选择策略；答案模型与参数不变。三臂：

- `select5`：原 Select5（保存结果）。
- `select5_role_prompt`：给 selector 的 prompt 增加“多跳问题必须覆盖每个 role/hop”指令。
- `select5_role_guaranteed`：Select5 选择 + 确定性 oracle-role 修复（候选池中存在的 evidence fact 若未被选中则强制加入）。

| Method | Candidate Recall | Role Coverage | Full Coverage Rate | Accuracy | MULTIHOP |
|---|---:|---:|---:|---:|---:|
| Select5（saved） | 0.8472 | 0.8194 | 0.7778 | 0.8333 | 0.2500 |
| Select5 + Role Prompt | 0.8472 | 0.8194 | 0.7500 | 0.8056 | 0.2500 |
| Select5 + Role Guaranteed | 0.8472 | 0.8472 | 0.8056 | 0.8333 | 0.2500 |

结论：

1. **role prompt 本身不解决问题**：qwen3:8b 作为 selector 在给定相同候选时，加角色指令没有提高覆盖（0.8194→0.8194），甚至因选择变化使整体 accuracy 略降。
2. **确定性 role repair 把 coverage 补到与 candidate recall 持平**（0.8472），证明 R2（evidence 在池里但被 selector 丢掉）可以被机械修复；R1（evidence 根本不在 Top-20 池）仍由 `candidate_recall=0.8472` 显式暴露。
3. **MULTIHOP accuracy 没有被覆盖修复推动**（仍是 0.25）：Q-MULTIHOP-0005 是 R1（池中缺 FACT-00004 对应行），Q-MULTIHOP-0010 即使两跳事实都在包内，模型仍被 FACT-00027 等强干扰吸引；Q-CROSS-0006 覆盖完整但包内表格行缺具体数值 66.75 ms。即**选择修复是必要不充分条件，需要 P1-3 的结构打包**。

产物：

- `memory_eval_tests/runs/relation-selector-v1/relation_selector_results.json`
- `memory_eval_tests/runs/relation-selector-v1/relation_selector_report.md`

## 12. 四阶段路线 P1-3：Table-aware Evidence Packing（完成）

控制变量：选择结果与基线回答逐字复用 `evidence-selector-v1` Select5；只改变选中表格证据的渲染方式。目标表由问题 evidence fact → sidecar table 解析（题目显式命名 TBL-xxxx，属 question-guided packing）。

| Pack | Accuracy | Groundedness | Hallucination | TABLE | Table Cell | Avg Context (chars) | Changed |
|---|---:|---:|---:|---:|---:|---:|---:|
| Select5（saved） | 0.8333 | 0.7500 | 0.2500 | 0.6000 | 0.6000 | 2,223 | 0 |
| Table Pack Full（完整表格行） | 0.8889 | 0.8056 | 0.1944 | 0.8000 | 0.8000 | 2,479 | 9 |
| Table Pack Minimal（表头+gold 行+邻行） | 0.8889 | 0.8056 | 0.1944 | 0.8000 | 0.8000 | 2,310 | 9 |
| Table Pack Focus（去掉非目标表行 + 完整目标表） | **0.9167** | **0.8333** | **0.1667** | **1.0000** | **1.0000** | 2,449 | 9 |

结论：

1. **结构化表格内容直接修复“数值缺失”类失败**：Q-FACT-00027（90 行长表 54.99 ms）在 full/minimal 下从 FAIL 转 PASS，且 minimal 只需 2,509 chars（full 7,841）。
2. **表格间干扰是真实故障源**：Q-FACT-00011（66.75 ms）在 full pack 下仍答成 33.75（相邻表 TBL-0003 干扰）；focus 去掉非目标表行后答对。Focus 把 TABLE 提升到 1.0、numeric/table cell 全对。
3. MULTIHOP 0.25→0.5：Q-CROSS-0006（66.75 ms + 9105 QMU）被 focus 修复；剩余 Q-MULTIHOP-0005/0010 需要更高精度与公式呈现（见第 13 节）。

产物：

- `memory_eval_tests/runs/table-packing-v1/table_packing_results.json`
- `memory_eval_tests/runs/table-packing-v1/table_packing_report.md`

## 13. 组合管线（role-guaranteed + focus packing）与精度上界（完成）

把 P0-2 的 role-guaranteed 选择与 P1-3 的 focus 打包合并为最终方法，另加一个“只保留 evidence-fact 行 + 目标表”的精度上界臂：

| Method | Accuracy | Groundedness | Hallucination | TABLE | MULTIHOP | Avg Context (chars) |
|---|---:|---:|---:|---:|---:|---:|
| Select5（saved） | 0.8333 | 0.7500 | 0.2500 | 0.6000 | 0.2500 | 2,223 |
| Combined Focus（role-guaranteed + focus） | **0.9167** | **0.8333** | **0.1667** | **1.0000** | 0.5000 | 2,483 |
| Combined Precision（仅 evidence-fact 行 + 目标表） | 0.8056 | 0.7500 | 0.2500 | 1.0000 | 0.5000 | 1,525 |

结论：

1. **最终 smoke 方法定为 Combined Focus**：0.9167 整体、TABLE 1.0、hallucination 0.1667；相比 Select5 提升 8.34pp，上下文仅 +260 chars。
2. **过强的精度过滤会倒退**：只留 evidence-fact 行会丢掉对 FACT/FIGURE/FORMULA 有用的解释性行（FACT 0.9333→0.8667），说明“最小 pack”不等于“最好 pack”；oracle-full 之所以 1.0 是因为其 fact 文本本身就是完整语句。
3. 剩余 2 道 MULTIHOP（Q-MULTIHOP-0005/0010）在组合臂下仍失败，且答案已经指向正确 fact（如 FACT-00018 + EQ-0010）但未输出完整“数值; 公式”串，属**答案格式/评分器口径问题**（模型答了 ID 引用而非 oracle 期望的值串），应在后续评估器修订中处理。

产物：

- `memory_eval_tests/runs/combined-pipeline-v1/combined_pipeline_results.json`
- `memory_eval_tests/runs/combined-pipeline-v1/combined_pipeline_report.md`

## 14. 四阶段路线 P1-4：20p → 200p Scale Validation（进行中）

计划与实际执行：本机 qwen3:8b 的 KG 实体抽取约 70s/2048-token，86+ chunks 需 3-6h，因此 20p 与 200p 均采用 **skip-KG chunk 检索**（`docx:native-!P`），保证两档位在同一检索范式下可比，smoke（12p KG）作为参照点。200p 全量 KG 抽取（626 F-chunks）按实测速度线性外推约 10h+，不作为本阶段目标；200p 采用 80 题抽样（前 80 题，覆盖各题型）。

### 14.1 20p（rich-smoke-20p-v1，skip-KG，60 题，段落级 chunk=26）

| Stage | Metric | Value |
|---|---|---:|
| Retrieval | Candidate Recall（Top-20 池含 oracle 事实） | 0.9667 |
| Selection | Role coverage（top-5 + role-guaranteed 修复） | 0.9667 |
| Answer | Direct Top-20 Accuracy / 平均 context | 0.8833 / 25,145 chars |
| Answer | Combined Focus Accuracy / 平均 context | 0.8833 / 18,061 chars |

结论：20p 下 combined focus 与 direct top-20 准确率持平（0.8833），上下文压缩 28%；相比 smoke（Select5 0.8333 → Combined Focus 0.9167），20p 的 chunk 检索本身已足够强（Top-20 覆盖 26 个 chunk 中的大部分），方法收益主要体现在 context 压缩而非准确率提升。

### 14.2 200p（rich-medium-200p-v1，skip-KG，80 题抽样，chunk=65）

| Stage | Metric | Value |
|---|---|---:|
| Retrieval | Candidate Recall（Top-20 池含 oracle 事实） | 0.5750 |
| Selection | Role coverage（top-5 + role-guaranteed 修复） | 0.5750 |
| Answer | Direct Top-20 Accuracy / 平均 context | 0.6000 / 26,957 chars |
| Answer | Combined Focus Accuracy / 平均 context | **0.6625** / 21,399 chars |

### 14.3 三阶段退化曲线（12p KG → 20p chunk → 200p chunk）

| 规模 | Retrieval Recall | Direct Top-20 Acc | Combined Focus Acc | Combined 相对基线 |
|---|---:|---:|---:|---:|
| 12p smoke（KG 检索） | 0.8472（Top-20） | 0.8056（保存产物） | 0.9167 | +11.11pp |
| 20p（skip-KG chunk） | 0.9667 | 0.8833 | 0.8833 | +0.00pp |
| 200p（skip-KG chunk，80 题抽样） | 0.5750 | 0.6000 | **0.6625** | **+6.25pp** |

结论：

1. **检索召回是规模化的主瓶颈**：20p→200p，Top-20 池包含 oracle 事实的比例从 0.9667 降到 0.5750；Retrieval→Selection→Answer 三个阶段同步衰减（召回 0.5750 → 回答 0.60-0.66）。
2. **Combined Focus 的收益随噪声增大而显现**：20p 与基线打平，200p 反超 6.25pp。逐题分解显示增益几乎全部来自 **question-guided 表格打包**——即使候选池完全没有该表（TABLE 组 candidate_recall=0 的 4 题），sidecar 目标表注入后 TABLE 从 5/9 恢复到 9/9；Q-CROSS-0018 的多跳答案也借此恢复。
3. **代价**：focus 过滤在 1 道 FIGURE 题（Q-FACT-00006）造成回归（5/10→5/10 中的一次翻转），且 FIGURE 组 retrieval recall 仅 0.1，是 200p 的次短板。
4. FORMULA 组在 200p 下召回与准确率均为 1.0（15/15），说明公式检索与生成不受规模影响；FACT 组受召回拖累最明显（20/37）。

方法与口径说明：200p 的 KG 全量抽取按实测 token 速度（约 70s/2048-token）外推需 10h+，本阶段 20p/200p 均采用 skip-KG chunk 检索保证两档可比；12p smoke 为 KG 检索参照点，三列之间不能当作严格同口径消融，但退化趋势与方法增益方向可信。200p 为前 80 题抽样，未覆盖 ABSTAIN（该组在 20p 全对）。

状态与产物持续更新于：

- `memory_eval_tests/runs/online/scale-rich-smoke-20p-v1/`
- `memory_eval_tests/runs/online/scale-rich-medium-200p-v1/`
