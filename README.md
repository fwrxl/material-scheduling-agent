# 素材审核与发布排期 Agent

- 考核：小影科技 2027 校招 · AI Agent 开发岗位
- 题号：**1（素材审核与发布排期 Agent）**
- 候选人：任鑫龙
- 运行方式：Python CLI
- 实现：原生 ReAct 工具调用循环，无 Agent 框架，运行时仅使用 Python 标准库

本项目读取合成素材与审核规则，查询已有排期，并保存**待确认或已就绪的排期草稿**。项目没有真实发布工具，任何路径都不会执行发布。

## 评审入口

- 需求与验收标准：[`docs/01-requirements.md`](docs/01-requirements.md)
- ReAct 流程、动态决策和取舍：[`docs/02-solution.md`](docs/02-solution.md)
- 逐项验收结果与限制：[`docs/03-evaluation.md`](docs/03-evaluation.md)
- AI 使用与个人贡献记录：[`docs/04-ai-usage.md`](docs/04-ai-usage.md)
- 复盘：[`docs/05-retrospective.md`](docs/05-retrospective.md)
- 实际测试报告：[`examples/test-results.txt`](examples/test-results.txt)
- 最终自动验收报告：[`examples/final-verification.txt`](examples/final-verification.txt)
- 场景清单：[`examples/manifest.json`](examples/manifest.json)
- 冲突调整 Trace：[`examples/traces/slot_conflict.json`](examples/traces/slot_conflict.json)
- 正常路径 Trace：[`examples/traces/normal.json`](examples/traces/normal.json)

> 当前提交的 `examples/` 是**离线 Mock 的真实执行结果**，只证明流程、工具与状态约束。它们不证明真实模型的决策效果。真实模型适配器已经实现并通过模拟 HTTP 测试，但本次未配置真实模型凭据，因此没有伪称完成在线模型验证。

## 环境与安装

要求 Python 3.11 或更高版本；本次验证使用 Python 3.13.12。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

运行时没有第三方依赖；`requirements-dev.txt` 只安装测试工具 pytest。

## 快速运行

在仓库根目录执行。离线模式无需环境变量、网络或密钥。

### 运行正常场景

```bash
python3 -m src.cli run \
  --mode mock \
  --input examples/inputs/normal.json \
  --store-path .runtime/drafts.json \
  --output-dir .runtime/normal
```

终端打印最终 JSON，同时写入 `.runtime/normal/output.json` 和 `.runtime/normal/trace.json`。

### 运行时段冲突场景

```bash
python3 -m src.cli run \
  --mode mock \
  --input examples/inputs/slot_conflict.json \
  --store-path .runtime/conflict-drafts.json \
  --output-dir .runtime/conflict
```

预期：保留原硬性要求 18:00，建议可用的 19:00，`task_status=waiting_confirmation`，只保存草稿并生成待确认项。

### 重建全部离线验收材料

```bash
python3 -m src.cli run-all \
  --mode mock \
  --output-dir examples \
  --reset-store
```

该命令重新运行七个场景；重复提交场景会连续运行两次。`--reset-store` 只删除本次指定的批量草稿文件，不会清空其他目录。

### 执行测试

```bash
python3 -m pytest -q
```

当前结果：**51 passed**。测试覆盖工具、完整决策循环、状态边界、幂等、失败、上限、CLI 和 OpenAI 兼容适配器。

## 真实模型模式

模型必须支持 OpenAI 风格的 Chat Completions 和 function/tool calling。配置通过环境变量读取，`.env` 不会自动加载，也不会被提交。

```bash
cp .env.example .env
# 编辑 .env，填入自己的兼容接口、模型名和密钥
set -a
source .env
set +a

python3 -m src.cli run \
  --mode model \
  --input examples/inputs/slot_conflict.json \
  --store-path .runtime/model-drafts.json \
  --output-dir .runtime/model-conflict
```

也可以一次生成全部真实模型材料：

```bash
python3 -m src.cli run-all \
  --mode model \
  --inputs-dir examples/inputs \
  --output-dir .runtime/model-examples \
  --reset-store
```

真实模式下，每轮由模型返回一个工具调用；工具结果加入下一轮消息，模型再根据观察调整动作或调用 `finish`。普通文本“已完成”不会被接受为工具决策。最终保存状态和任务状态由实际工具结果生成，而不是信任模型自述。

## 输出与限制

每个结果包含素材审核状态、风险原因、原始要求时段、建议时段、待确认项、草稿保存状态、发布状态和任务状态。`max_steps` 限制模型决策轮数，`max_tool_calls` 限制包含 `finish` 在内的动作数；达到上限返回 `task_status=incomplete`。

```text
src/
  agent.py          ReAct 循环、输出状态归约、Trace
  tools.py          素材/规则/时段工具、原子草稿存储与幂等
  openai_model.py   OpenAI 兼容真实模型适配器
  mock_model.py     明确标注的离线流程桩
  cli.py            单场景与批量 CLI
examples/
  inputs/           8 组输入
  outputs/          9 次实际运行输出（重复场景运行两次）
  traces/           每步参数、观察摘要、后续动作和依据
  drafts.json       批量运行后的实际草稿存储
  test-results.txt  pytest 实际报告
data/               合成素材、规则与排期
tests/              51 项可执行测试
docs/               需求、方案、评估、AI 使用与复盘
```

## 提交前检查

1. 核对 `docs/04-ai-usage.md` 中的个人贡献、验证边界和约 4 小时实际工时记录。
2. 如有可用模型，执行真实模型模式并保留实际 Trace；不要删掉 Mock 边界说明。
3. 再运行 `python3 -m pytest -q` 和至少一条 CLI 命令。
4. 提交题号 **1**、GitHub URL 和最终 commit SHA；不要提交 `.env`、真实密钥或隐私数据。
