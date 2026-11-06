# W2：让 Agent 使用自然语言业务模型

模型调用默认单次 90 秒、输出截断后一次扩容重试，参数、运行记录和私有推理续接规则见 [模型配置与恢复](../model-configuration.md)。

`w02-natural-0.1.0`，工程包 `w02-agent-0.5.4`。本入口读取自然语言业务模型，保存实际请求和回答，供学员判断是否正确采用。

## 从提取结果创建两份文档

先按[提取说明](../w02_extraction/README.md)处理自己的访谈。然后运行：

```sh
.venv/bin/python tools/w02_knowledge.py init
```

只找到一次成功提取时自动采用；有多次时用 `--from-extraction` 指定命令返回的实际 `run.json` 路径。不需自己编写运行编号或哈希。初始化新建配置 `customer/w02/knowledge.json` 及表达提示词，并在缺少时复制两份模板：

- `docs/w02/business_model.md`：业务模型说明。
- `docs/w02/interview_notes.md`：访谈与核查记录，含干系人地图和 Agent 判读。

已有文档不会覆盖；重复初始化配置会提示 `OUTPUT_EXISTS`。改用另一次提取时运行：

```sh
.venv/bin/python tools/w02_knowledge.py init --from-extraction artifacts/w02/runs/RUN-实际编号/run.json --replace
```

程序按该次提取同时改写 `customer/w02/knowledge.json` 的 `extraction_run`、`scope_id` 和 `profile`，保留业务模型路径、表达提示词路径和上下文上限，不改动两份文档。手动编辑配置时，这三项也要一起与新提取一致，只改 `extraction_run` 会报 `SCOPE_MISMATCH`。业务内容继续由自己核查、修改，不自动生成客户答案。

核对候选与原话，将采用的信息、关键选择与尚未解决的问题写入两份文档。可用自然语言、小表格或草图，但图里的重要含义需要用文字说明，本入口读取的是文字，不识别图片中的业务定义。保留可定位的原文引用，不抄写同一段材料多次。

## 检查并完成实际对照

沿用根 README 的模型环境变量；profile 从提取配置继承，端点不支持时需明确调整，不自动换模型或重试。所有命令均可在子命令前加 `--workspace /path/to/客户仓库`；Windows 使用 `.venv\Scripts\python.exe`。

```sh
.venv/bin/python tools/w02_knowledge.py inspect
.venv/bin/python tools/w02_knowledge.py compare --question '这里换成自己希望核实的业务问题' --expected '这里写本次希望采用的定义或应保留的未知'
```

`inspect` 不调用模型。`compare` 自动进行两次全新上下文调用：未提供业务模型、提供业务模型。两次使用同一份输入快照、同一模型和问题；预期只保存在记录中，不发送给模型。如果任一调用失败会停止这一组，保留失败记录，核对后再运行 `compare`。调用成功不是语义通过，结果仍为 `needs_review`。

也可以用 `run --question '问题'` 单独调试，或加 `--without-knowledge` 查看单次基线；这些运行会保留，不能单独替代同题对照。

## 查看运行结果

每次返回 `artifacts/w02/knowledge_runs/RUN-…/run.json`。同目录包含：

| 文件 | 用途 |
| --- | --- |
| `snapshot/`、`source_manifest.json` | 自动保存本次业务模型、配置、提取候选及来源链的版本；不要求手填哈希 |
| `request.json` | 实际发出的消息和模型参数，确认业务定义是否进入请求 |
| `response.json`、`answer.json` | 公开响应和回答；不保存认证头或私有思考 |
| `settings.json` | 本次问题、预期及对照分组；不进入模型请求 |
| `run.json` | 结果、错误和记录关联；由程序生成 |

```sh
.venv/bin/python tools/w02_knowledge.py check-run artifacts/w02/knowledge_runs/RUN-实际编号/run.json
.venv/bin/python tools/check_w02.py
```

检查器自动找到运行记录。业务模型改变后应重跑受影响的问题，旧记录保持不动。需要排除不采用的历史运行时可用 `check_w02.py --run 路径 --run 路径` 选择一组对照，运行编号仍由程序生成。

最终判读写入访谈与核查记录：请求有没有采用当前说明，回答有没有使用定义、丢失条件、编造未知或声称执行了候选动作。回答正确时说明依据，有错误再修改重跑，不要求人为制造错误。

## 当前能力

入口调用标准 `Agent.answer_business_model`，每次只回答当前知识问题，不读取 W1 样例业务数据，不注册候选动作，不接入青禾计算或外部业务系统。自然语言说明没有自动带来这些能力。
