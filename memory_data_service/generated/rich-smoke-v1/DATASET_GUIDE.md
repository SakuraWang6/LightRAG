# rich-smoke-v1：测试文档、问题与标准答案

## 1. 数据集用途

`rich-smoke-v1` 是一个 12 页的合成富文本技术文档，用来验证 LightRAG 是否能把长文档当作可追溯的 Document Memory，而不只是在普通文本块上做相似度检索。

文档不是现实业务数据；数值、编号和名称均为可复现的 synthetic oracle。它刻意在权威事实附近放入 provisional、legacy、archived 或 retired distractor，以测试系统能否选中正确证据并拒绝不存在的信息。

## 2. 包含的文档内容

| 内容层 | 覆盖内容 |
|---|---|
| 主体章节 | Retrieval Cell 0001–0012 的校准限制、政策、冲突处理与操作说明 |
| 表格 | 多张 latency threshold 表，含 gold row、archived distractor、合并表头和跨页长表 |
| 图与 caption | FIG-0004、FIG-0008、FIG-0012 的 visual control state；灰色 retired state 是干扰项 |
| 公式 | EQ-0005、EQ-0010 以及 `eta`、peak power、dwell time 等变量说明 |
| 交叉引用 | Word bookmark/REF 指向表格，供跨对象追踪验证 |
| 富版式 | 页眉页脚、目录、多级标题、嵌套列表、双栏、VML 浮动文本框、脚注/尾注、caption、附录 |
| 附录 | `LONG-TBL-APP` 的 rollover latency stress case |

Oracle 文件：`facts.json` 定义 27 个原子事实，`questions.json` 定义 36 个问题，`objects.json`/`relations.json` 描述文档对象图，`oracle.json` 是统一入口。

## 3. 问题设计

36 题由 34 个应回答题和 2 个应拒答题构成：12 个直接数值题、5 个表格单元格题、3 个 figure/caption 题、4 个多跳题、2 个公式题、2 个变量题、2 个公式+变量组合题，以及版本、冲突、负向约束和附录长表题。多跳题特别要求结合顺序、表格或公式；拒答题在文档中没有对应证据。

## 4. 完整题目与标准答案

| ID | 类型 | 问题 | 标准答案 | Oracle 证据 |
|---|---|---|---|---|
| Q-FACT-00001 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0001? | 9021 QMU | FACT-00001 |
| Q-FACT-00002 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0002? | 9038 QMU | FACT-00002 |
| Q-FACT-00003 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0003? | 9053 QMU | FACT-00003 |
| Q-FACT-00004 | table cell | In TBL-0003, what is the Maximum value for the authoritative gold row? | 33.75 ms | FACT-00004 |
| Q-FACT-00005 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0004? | 9071 QMU | FACT-00005 |
| Q-FACT-00006 | figure caption | According to Figure FIG-0004, what is the visual control state? | verified-state-0004 | FACT-00006 |
| Q-FACT-00007 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0005? | 9087 QMU | FACT-00007 |
| Q-FACT-00008 | equation | What is the formula stated in Equation EQ-0005? | `E_{5}=P_{5}T_{5}/\\eta_{5}` | FACT-00008 |
| Q-FACT-00009 | equation variable | In Equation EQ-0005, what does eta_5 mean? | eta_5 is the efficiency coefficient | FACT-00009 |
| Q-EQVAR-0005 | formula + variable | State Equation EQ-0005 and define eta_5. | `E_{5}=P_{5}T_{5}/\\eta_{5}`; eta_5 is the efficiency coefficient | FACT-00008, FACT-00009 |
| Q-MULTIHOP-0005 | multi-hop | Using the latest timing table before page 5 and Equation EQ-0005, which latency fact and equation should be cited together? | 33.75 ms; `E_{5}=P_{5}T_{5}/\\eta_{5}` | FACT-00004, FACT-00008 |
| Q-FACT-00010 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0006? | 9105 QMU | FACT-00010 |
| Q-FACT-00011 | table cell | In TBL-0006, what is the Maximum value for the authoritative gold row? | 66.75 ms | FACT-00011 |
| Q-CROSS-0006 | multi-hop | For retrieval cell 0006, combine the calibration limit with the nearest preceding table maximum. | 9105 QMU; 66.75 ms | FACT-00010, FACT-00011 |
| Q-FACT-00012 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0007? | 9121 QMU | FACT-00012 |
| Q-FACT-00013 | version condition | Which policy version is active for Retrieval Cell 0007, and from when? | v7.2 from 2026-Q3 | FACT-00013 |
| Q-FACT-00014 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0008? | 9137 QMU | FACT-00014 |
| Q-FACT-00015 | figure caption | According to Figure FIG-0008, what is the visual control state? | verified-state-0008 | FACT-00015 |
| Q-FIGTEXT-0008 | figure text + multi-hop | For Retrieval Cell 0008, combine the authoritative calibration limit with the cited figure control state. | 9137 QMU; verified-state-0008 | FACT-00014, FACT-00015 |
| Q-FACT-00016 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0009? | 9161 QMU | FACT-00016 |
| Q-FACT-00017 | conflict resolution | When methods conflict for Retrieval Cell 0009, which method is canonical? | Method-C0009 | FACT-00017 |
| Q-FACT-00018 | table cell | In TBL-0009, what is the Maximum value for the authoritative gold row? | 99.75 ms | FACT-00018 |
| Q-FACT-00019 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0010? | 9173 QMU | FACT-00019 |
| Q-FACT-00020 | equation | What is the formula stated in Equation EQ-0010? | `E_{10}=P_{10}T_{10}/\\eta_{10}` | FACT-00020 |
| Q-FACT-00021 | equation variable | In Equation EQ-0010, what does eta_10 mean? | eta_10 is the efficiency coefficient | FACT-00021 |
| Q-EQVAR-0010 | formula + variable | State Equation EQ-0010 and define eta_10. | `E_{10}=P_{10}T_{10}/\\eta_{10}`; eta_10 is the efficiency coefficient | FACT-00020, FACT-00021 |
| Q-MULTIHOP-0010 | multi-hop | Using the latest timing table before page 10 and Equation EQ-0010, which latency fact and equation should be cited together? | 99.75 ms; `E_{10}=P_{10}T_{10}/\\eta_{10}` | FACT-00018, FACT-00020 |
| Q-FACT-00022 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0011? | 9191 QMU | FACT-00022 |
| Q-FACT-00023 | negative constraint | Which override channel must not be used for Retrieval Cell 0011? | retired override channel ROC-0011 | FACT-00023 |
| Q-FACT-00024 | direct numeric | What is the authoritative calibration limit for Retrieval Cell 0012? | 9204 QMU | FACT-00024 |
| Q-FACT-00025 | table cell | In TBL-0012, what is the Maximum value for the authoritative gold row? | 132.75 ms | FACT-00025 |
| Q-FACT-00026 | figure caption | According to Figure FIG-0012, what is the visual control state? | verified-state-0012 | FACT-00026 |
| Q-CROSS-0012 | multi-hop | For retrieval cell 0012, combine the calibration limit with the nearest preceding table maximum. | 9204 QMU; 132.75 ms | FACT-00024, FACT-00025 |
| Q-FACT-00027 | table cell / appendix | In LONG-TBL-APP, what is the authoritative final rollover latency? | 54.99 ms | FACT-00027 |
| Q-ABSTAIN-00001 | abstain | What is the approval code for the nonexistent zirconium bypass module? | The document does not provide this information. | none |
| Q-ABSTAIN-00002 | abstain | Which appendix authorizes the quantum coolant override? | The document does not provide this information. | none |

## 5. 使用时的注意事项

- 数值题应选择 `authoritative`/gold 事实，而非同一段中出现的 provisional、legacy 或 archived 值。
- 图题应选择 `verified-state-*`，不能误用 `retired-state-*`。
- 多跳题需要同时输出全部要求的证据；只答公式、只答表格值或换成相邻 cell 都不算完整。
- 拒答题不应猜测编号。只要明确说明文档未提供该信息即可；不同措辞在人工判断上等价，自动评分器目前的同义拒答覆盖仍有限。
