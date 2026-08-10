# I0（P0）：实验可信性底座

## 目标

让一次运行成为可审计的实验单元。用户必须能知道：实际执行了什么配置、使用了
什么数据、任务是否只执行一次，以及失败发生在何时何处。

这项迭代首先解决一个已知风险：当前在线基线将界面中的 `model` 记录在 baseline
中，但查询请求不会据此切换正在运行的 LightRAG 服务模型。因此运行标签不能被
误解为实际模型配置。

## 用户故事

- 作为实验用户，我想在报告中看到实际生效的模型和索引信息，而不是只看到我在
  表单里填写的值。
- 作为实验用户，我想在任务失败后看到失败阶段和原因，以便决定重试还是修复环境。
- 作为平台维护者，我想在服务重启或多 worker 部署时避免同一任务被重复执行。

## 功能需求

### FR-0.1 运行身份与输入指纹

- 每个新 run 必须写入 `execution_manifest`，至少包含：
  - `dataset_id`、manifest SHA-256、oracle SHA-256、文档文件清单及 SHA-256；
  - 生成器版本、模板版本、随机种子（存在时）；
  - 实验 ID、实验类型、代码 Git commit、框架版本；
  - 请求参数、参数来源（默认值/模板/用户输入）和启动时间。
- `execution_manifest` 在运行开始后不可原地修改；需要重跑时创建新的 run。
- 无法获得的字段必须记录为 `unknown` 和原因，不能伪造默认值。

### FR-0.2 实际环境快照

- 新增服务端 `runtime_snapshot`：LLM/Embedding/VLM/Reranker 的 provider、模型名、
  endpoint 标识、解析引擎、存储后端、workspace、LightRAG 版本和关键检索默认值。
- 快照必须来自被测实例的配置或健康/诊断接口，而非浏览器传入的字段。
- `model` 保留为兼容展示字段，但新增 `declared_model` 与 `effective_model`；两者
  不一致时必须标记 `configuration_mismatch`。
- 凭据字段只能记录 `configured`、hash 或 provider 标识，禁止持久化明文。

### FR-0.3 任务状态与失败事件

- job/run 的状态模型至少包含：`pending`、`claiming`、`running`、`cancelling`、
  `cancelled`、`complete`、`failed`、`stale`。
- 运行写入追加式 `events.jsonl`，记录时间、阶段、严重级别、可读消息和脱敏的错误
  摘要；报告页可展示时间线。
- 失败时须写入 `failure`：阶段、错误类型、可重试性、重试建议、关联日志偏移。
- 不允许用空的 `failed` 覆盖已有失败原因。

### FR-0.4 跨进程原子认领

- 作业从 `pending` 到 `running` 的认领必须使用跨进程原子机制：数据库条件更新、
  可移植文件锁或等价的持久化 compare-and-set。
- 进程内 `threading.Lock` 仅可作为优化，不可作为正确性边界。
- 认领记录必须包含 owner ID、PID、进程启动时间、租约到期时间；过期租约可由恢复
  流程接管并记录事件。
- 取消、重启恢复与认领之间必须有幂等语义。

### FR-0.5 历史运行兼容

- 所有缺少 `execution_manifest` 或实际环境快照的历史 run 显示为 `legacy`。
- 新旧口径不能默认放在同一统计图中；用户手动选择时显示可比性警告。

## API 与数据变更

- 扩展运行 envelope：`schema_version` 升级，添加 `execution_manifest`、
  `runtime_snapshot`、`compatibility_level`、`failure`。
- 扩展 job 记录：`claim`、`lease_expires_at`、`events_path`。
- `GET /eval/runs/{id}` 返回结构化状态、兼容性和失败信息；现有字段保持兼容。

## 非目标

- 本迭代不负责创建独立 LightRAG 实例或自动上传数据集。
- 本迭代不改变现有指标公式，也不实现模型排名。

## 验收标准

- 创建新 run 后，详情页能在 5 秒内展示数据指纹和实际环境快照。
- 模拟服务端模型与声明模型不同，run 标记为配置不匹配且不能进入模型排名。
- 两个 API worker 并发扫描同一 pending job 时，仅一个获得执行权。
- 故意制造子进程异常后，页面显示失败阶段、错误摘要和日志跳转。
- 现有 run 仍可读取，并明确显示 `legacy`。
