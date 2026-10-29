# 03 效果验证

## 验证方法与环境

- 验证环境：Python 3.13.12，macOS。
- 自动化命令：`python3 -m pytest -q`。
- 实际结果：**51 passed**，见 [`../examples/test-results.txt`](../examples/test-results.txt)。
- 端到端命令：`python3 -m src.cli run-all --mode mock --output-dir examples --reset-store`。
- 运行材料索引：[`../examples/manifest.json`](../examples/manifest.json)。
- 最终自动验收断言：[`../examples/final-verification.txt`](../examples/final-verification.txt)，覆盖 9 次运行、确认边界、幂等、失败、两类上限和交付链接。

以下结果均为实际执行生成，不是手写预期输出；但 Agent 模式为 Mock，只证明流程正确性。

## 场景验收结果

| 场景 | 输入 | 实际输出 | 实际 Trace | 关键结果 |
|---|---|---|---|---|
| 正常 | [`normal.json`](../examples/inputs/normal.json) | [`normal.json`](../examples/outputs/normal.json) | [`normal.json`](../examples/traces/normal.json) | `completed`，原 19:00，草稿 `saved` |
| 信息缺失 | [`missing_info.json`](../examples/inputs/missing_info.json) | [`missing_info.json`](../examples/outputs/missing_info.json) | [`missing_info.json`](../examples/traces/missing_info.json) | 缺 `owner`，`blocked`，未保存 |
| 规则风险 | [`rule_risk.json`](../examples/inputs/rule_risk.json) | [`rule_risk.json`](../examples/outputs/rule_risk.json) | [`rule_risk.json`](../examples/traces/rule_risk.json) | R01 风险，`blocked`，未保存 |
| 时段冲突 | [`slot_conflict.json`](../examples/inputs/slot_conflict.json) | [`slot_conflict.json`](../examples/outputs/slot_conflict.json) | [`slot_conflict.json`](../examples/traces/slot_conflict.json) | 原 18:00 不变，建议 19:00，`waiting_confirmation` |
| 首次重复用例 | [`duplicate.json`](../examples/inputs/duplicate.json) | [`duplicate_first.json`](../examples/outputs/duplicate_first.json) | [`duplicate_first.json`](../examples/traces/duplicate_first.json) | 首次 `saved` |
| 重复提交 | 同上 | [`duplicate_second.json`](../examples/outputs/duplicate_second.json) | [`duplicate_second.json`](../examples/traces/duplicate_second.json) | 第二次 `duplicate`，草稿 ID 相同 |
| 保存失败 | [`save_failure.json`](../examples/inputs/save_failure.json) | [`save_failure.json`](../examples/outputs/save_failure.json) | [`save_failure.json`](../examples/traces/save_failure.json) | `failed`，草稿 ID 为 null，未生成成功记录 |
| 步数上限 | [`step_limit.json`](../examples/inputs/step_limit.json) | [`step_limit.json`](../examples/outputs/step_limit.json) | [`step_limit.json`](../examples/traces/step_limit.json) | `incomplete`，原因 `model_step_limit` |
| 工具次数上限 | [`tool_call_limit.json`](../examples/inputs/tool_call_limit.json) | [`tool_call_limit.json`](../examples/outputs/tool_call_limit.json) | [`tool_call_limit.json`](../examples/traces/tool_call_limit.json) | `incomplete`，原因 `tool_call_limit` |

批量运行后的 [`../examples/drafts.json`](../examples/drafts.json) 只有正常、冲突、重复用例三份草稿。重复用例执行两次仍只有一份，缺信息、风险、保存失败和上限用例均无草稿。

## 动态决策证据

正常与冲突场景在第三步获得不同的时段观察：

- [`正常 Trace`](../examples/traces/normal.json)：`requested_available=true`，随后保存参数保持 19:00。
- [`冲突 Trace`](../examples/traces/slot_conflict.json)：`requested_available=false`，随后选择 19:00 替代 18:00，输出待确认。

两条 Trace 均记录工具参数、返回摘要、后续动作与简短依据。再次强调：这些是离线策略的流程证据，不冒充真实模型决策效果。

## 自动化测试覆盖

`tests/` 的 51 项测试覆盖：

1. 正常、缺信息、规则风险、冲突、保存失败、步数上限和工具次数上限的参数化路径；
2. 正常与冲突的不同观察导致不同保存参数；
3. 重复 Agent 及两个独立 CLI 进程提交时，草稿数量和 ID 保持一致；
4. 模型试图修改硬性请求、未审核先保存、原时段可用却改档时的拒绝逻辑；
5. 占用时段不可保存、幂等内容冲突、损坏存储和各阶段注入失败；
6. 保存成功后再失败仍保留真实草稿事实，其他素材审核不污染当前结果；
7. 非法 `finish` 返回失败观察，场景字段类型和布尔上限被拒绝；
8. OpenAI 兼容多轮观察循环、工具调用解析、普通文本、HTTP 429 和六类畸形响应；
9. CLI 实际子进程运行、输出/Trace 落盘和无效输入退出码。

## 失败与限制

- **未执行真实模型在线调用**：当前环境未提供模型凭据。适配器只通过模拟 HTTP 响应测试，不能据此声明真实模型决策达标。提交前若有条件，应运行 `--mode model` 并保存真实输出。
- **Mock 的能力边界**：离线模型是显式脚本化策略，可能覆盖不到真实模型的格式错误、不合理工具顺序或错误结束。真实模式有参数保护和状态归约，但仍需在线评估。
- **单素材、本地合成数据**：没有验证复杂业务规则、跨日排期、真实审核 API 或多素材资源竞争。
- **本地幂等不是分布式幂等**：线程锁和原子文件替换适合本考核，多进程/多实例需数据库唯一约束和事务。
- **无断点恢复**：进程退出后不能从未完成步骤续跑；已保存草稿仍保留。
- **故障注入是确定性演练**：证明失败状态处理，不代表真实存储服务的可用性。

未发现当前 51 项测试的失败。上述限制不是已解决项，文档和输出均保留明确边界。
