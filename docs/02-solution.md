# 02 方案设计

## 技术取舍

采用 Python 标准库和原生工具调用循环，不引入 LangGraph、ADK 等框架。本题只有一个任务状态机和四个业务工具，原生实现可以减少学习成本，让评审直接检查“模型选择—工具执行—观察—再决策”的过程。pytest 仅用于测试，版本约束为 `>=8.0,<9.0`；本次环境为 Python 3.13.12。

真实模型通过 OpenAI 兼容 HTTP 接口接入，使用标准库 `urllib`，不绑定某个模型厂商 SDK。业务工具与数据均在本地，便于故障注入和重复验证。

## 组件

- `src/agent.py`：ReAct 循环、前置条件保护、执行上限、最终状态归约和 Trace。
- `src/openai_model.py`：发送 tools，解析每轮一个 tool call；拒绝把普通文本当成动作。
- `src/mock_model.py`：脚本化离线策略，仅用于可重复流程测试。
- `src/tools.py`：四个业务工具及草稿存储。
- `src/cli.py`：单场景/全场景运行和材料生成。

## 真实模型 ReAct 调用流程

```text
系统约束 + 用户场景
        ↓
模型选择恰好一个工具动作
        ↓
参数与业务前置条件检查 → 执行本地工具
        ↓
将结构化工具结果作为 tool observation 加入对话
        ↓
模型读取观察，选择补查、调整保存参数、结束或停止
        ↓
模型调用 finish → 系统按实际观察生成最终结果
```

预设的只是允许动作和业务安全约束，不是预设决策日志。在真实模式下，下一步动作和参数来自当次 API 返回；例如模型看到 `requested_available=false` 后，可以自主从 `alternatives` 选择建议值。若模型试图修改原始 `request_id`、素材 ID 或硬性时间，保存前置条件会拒绝。

每轮最多一个工具调用，确保观察在下一轮决策之前返回。`max_steps` 统计模型决策轮数，`max_tool_calls` 统计包括 `finish` 在内的动作数。模型异常、未知工具、非法参数和工具失败均进入可观察结果或明确终止状态。

## 两条动态路径

### 原时段可用

```text
get_material → check_rules → query_slots
观察 requested_available=true
→ save_draft(suggested_start=requested_start)
观察 saved
→ finish → completed
```

### 原时段冲突

```text
get_material → check_rules → query_slots
观察 requested_available=false, alternatives=[19:00, 20:00]
→ save_draft(requested_start=18:00, suggested_start=19:00)
观察 saved + confirmation_status=pending
→ finish → waiting_confirmation
```

这两条已提交 Trace 来自 Mock 流程执行，用于核对路径和状态。真实模型是否作出同样选择，必须通过 `--mode model` 另行验证，不能由 Mock 推断。

缺信息和规则风险会形成另外两条提前结束路径：观察到 `incomplete` 或 `blocked` 后，模型选择 `finish`，不会调用保存。

## 工具职责

| 工具 | 职责 | 关键结果 |
|---|---|---|
| `get_material` | 读取指定素材 | 元数据或不存在错误 |
| `check_rules` | 检查必填字段和合成审核规则 | `passed/incomplete/blocked`、风险 |
| `query_slots` | 查询原时段与同日可用替代项 | 可用性、占用者、替代列表 |
| `save_draft` | 校验建议时段，幂等保存草稿 | `saved/duplicate/failed`、草稿 ID |
| `finish` | 由模型表示结束 | 系统仍按工具观察计算最终状态 |

## 确认边界与状态归约

`requested_start` 始终从用户输入复制，不能由模型最终文本修改。建议时段不同则存储 `confirmation_status=pending`，最终结果生成 `waiting_confirmation` 和待确认文本。项目没有“确认通过”或“发布”工具，因此不会越权完成后续动作。

最终 `task_status` 由工具观察计算，优先级包括执行上限、模型错误、审核阻断、保存失败、保存成功和待确认。即使模型在 `finish.summary` 中声称成功，也不能把保存失败改成成功；结果中的关键消息同样由系统生成。

状态按当前场景隔离：其他素材的审核结果和其他请求的时段查询可以进入对话与 Trace，但不能覆盖当前任务的归约状态。一旦草稿实际保存成功，后续失败尝试也不会把已有草稿 ID 和保存事实抹掉；所有尝试仍完整保留在 Trace 中。

## 幂等与保存真实性

- `request_id` 是幂等键，草稿 ID 由其 SHA-256 前缀确定。
- 相同键和相同业务内容返回已有记录，状态为 `duplicate`。
- 相同键但内容不同返回 `idempotency_conflict`，不覆盖。
- 写入采用同目录临时文件、`fsync` 和 `os.replace`，避免部分 JSON 文件。
- 注入失败或实际 I/O/JSON 错误均返回 `save_status=failed`，不生成草稿 ID。
- 草稿保存不占用真实排期，也不代表发布。

## Trace 设计

每一步记录 `decision_basis`、动作、参数、完整工具结果、观察摘要和下一动作。真实模型若有简短 `content`，将其作为依据；否则只记录“模型根据当前观察选择该动作”。不请求或保存模型的隐藏思维过程。Trace 标记 `agent_mode` 和 `real_model_decisions`，防止混淆 Mock 与真实决策。

## 已知设计边界

本地存储只用线程锁，不是多进程幂等方案；每次 CLI 是独立进程，不能恢复中途状态。静态排期适合验收，不包含时区扩展、并发抢档或真实审核服务。真实模型必须兼容本项目的 tool calling 消息格式。
