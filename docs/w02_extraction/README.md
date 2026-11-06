# W2 访谈提取工具骨架

模型调用默认单次 90 秒、输出截断后一次扩容重试，参数、运行记录和私有推理续接规则见 [模型配置与恢复](../model-configuration.md)。

> 工具版本 `w02-extractor-0.1.0`，契约 `w02-0.1.0-draft`。用于访谈导出后的信息整理和人工复核。自然语言业务模型入口已提供，公开总预检见 [检查说明](../w02_check/README.md)；本工具不裁定作业通过。

需要按 V05 组织持续任务时，使用[提取 Skill 与 Agent 工作流](../w02_workflow/README.md)；本文保留独立提取命令的用法。

工具提供文件读取、分批调用、结果解析、来源检查和运行记录。你负责选择提取问题、调整提示词和配置、核查遗漏与失真，并据此建模。模板没有青禾答案，也不会自动把候选变成已核实事实。

## 安装与工作目录

Python 3.11 或更高版本。从本包解压目录执行；如果已在课程工程中，使用该工程已有环境即可。

```sh
python3 -m venv .venv
.venv/bin/python -m pip --isolated install -r requirements.txt
.venv/bin/python tools/w02_interviews.py inspect --config examples/w02_extraction/extraction.json
```

最后一步不调用模型，预期显示 2 份会议、1 个提取批次和计划 2 次模型调用。`examples/w02_extraction/` 是虚构图书馆材料，来源标记 `fixture`；不包含青禾答案。Windows 激活虚拟环境后使用 `python`；Windows、Linux 如遇问题，请反馈给课程方。

所有命令可在子命令前加 `--workspace /path/to/你的客户仓库`。产物、配置和来源都写入这个仓库，工具代码及 Schema 从本工具安装目录读取并快照入运行记录；不要求覆盖已有 W1 工程。路径以工作仓库为基准，使用 `/`，不接受越界路径或指向仓库外的符号链接。

## 先配置自己的任务

```sh
.venv/bin/python tools/w02_interviews.py init
```

这会新建 `customer/w02/extraction.json` 和 `extraction_prompt.txt`；已有文件不会被覆盖。模板的 `sources` 有意留空，需要填写后才能运行。

1. 在访谈网页中选择会议，点击“导出提取用记录”，把文件保存到自己仓库的 `artifacts/w02/sources/`。
2. 在 `sources` 写入各文件的相对路径，在 `scope_id` 保留对应客户作用域。青禾是 `qinghe`，图书馆是 `fictional-library`，两者不能混在一次运行里。
3. 把 `focus_questions` 改成自己的提取问题，例如需要弄清的定义、工作环节、限制或分歧；结合试跑结果修改 `extraction_prompt.txt`。不要把自己猜的答案写成模型必须接受的事实。
4. 选择模型支持的 `profile`，再运行 `inspect` 查看输入是否有效及计划调用数。修改批次参数后也先检查。

如果保存的是“完整公开记录”而不是提取用记录，先把原件保留在仓库，再转换到新文件：

```sh
.venv/bin/python tools/w02_interviews.py convert artifacts/w02/sources/old-public.json --output artifacts/w02/sources/normalized.json
```

转换保留原周次和消息 ID，W1 不会变成 W2；系统操作消息留在原件里，不送进提取。非公开导出格式会被拒绝。已经规范化的记录可直接使用，不必再次转换。

## 实际提取

沿用 W1 的模型环境变量：`FDE_MODEL_URL` 为 Chat Completions 完整地址，`FDE_MODEL_NAME` 为模型标识，`FDE_MODEL_KEY` 为凭据。本工具不会读取教师私有配置，也不自动切换模型；仅对确认的输出预算截断执行一次扩容重试。密钥不要写入项目文件。

| profile | 请求特点 | 使用前提 |
| --- | --- | --- |
| `compatible` | 通用请求，不加厂商参数 | 默认起点；模型仍须按提示词返回完整 JSON |
| `json-object` | 加入 JSON 对象输出约束 | 端点支持 `response_format` |
| `deepseek-json` | JSON、顶层 `thinking`：提取与冲突判断均开启低推理，8192 输出额度 | 端点需支持 DeepSeek OpenAI 格式的思考开关 |

配置好环境后：

```sh
.venv/bin/python tools/w02_interviews.py inspect
.venv/bin/python tools/w02_interviews.py extract
```

也可先对独立材料试跑，在命令末尾加 `--config examples/w02_extraction/extraction.json`，并按自己的端点调整该配置的 profile。输入虚构与模型调用真实是两件事，程序分别记录。

成功时返回 `artifacts/w02/runs/RUN-…/run.json`，同目录有 `candidates.json`。复制命令中的占位运行路径时，替换成刚返回的实际路径。失败返回非零状态，保留已经发出的请求、公开响应和错误，不使用上次成功结果。

一次提取由若干提取调用和 1 次冲突检查组成：先按完整消息分批，保留会议与消息 ID，再在所有候选上集中寻找需要核实的分歧。模型不能通过冲突检查改写或删除陈述。候选可能重复、遗漏或错误，冲突组也只是待核建议。

冲突识别要求：访谈者的提问不代表其主张，提问和“不知道”本身不构成分歧；没有答案的内容继续保留为未知。不同角色、时间和范围下的说法仍需核查，不能直接裁定谁对谁错。候选结果仍需由学员检查。

普通候选提取由 `customer/w02/extraction_prompt.txt` 控制；冲突识别使用 `platform/fde_product/interview_extraction.py` 中的 `CONFLICT_PROMPT`，外层 Skill 不直接控制这段请求。修改时先定位实际处理阶段，检查请求中采用的版本，再验证效果。

`batch_chars` 控制每批来源文本的字符容量，不是 token 上限；`max_batches` 限制调用规模。单条消息太长会明确报错，不截断原话。`reconcile_chars` 限制集中检查的候选容量；超过时保留前面调用并报错。需要调整提取范围或参数后新建运行，不能静默漏掉后面的材料。跨批次的指代关系仍可能需要补充上下文和人工复核。

## 对照原文核查，写入业务模型

```sh
.venv/bin/python tools/w02_interviews.py check-run artifacts/w02/runs/RUN-实际编号/run.json
.venv/bin/python tools/w02_interviews.py evidence artifacts/w02/runs/RUN-实际编号/candidates.json
.venv/bin/python tools/w02_knowledge.py init
```

`evidence` 将候选与会议、角色、消息原文并列展示。核查准备采用的信息，在业务模型说明中写定义、建模选择和未知，在访谈与核查记录中写理由及下一步。候选 JSON 原样保留，不要求逐条手填 accepted／revised／rejected，也不要求提交另一份最终事实 JSON。

候选里的置信或复核状态是程序的中间数据，不证明真实情况已经核实。来源能够定位，仍需判断原文支持到什么程度。发现候选失真，可以改提取要求重跑，也可以在人工记录中说明采用时的修订；不用为了建模先修齐所有机器字段。

有多次成功提取时，初始化加 `--from-extraction` 指向采用的运行。业务模型加载与同题对照见[知识入口](../w02_knowledge/README.md)。

## 学员实现与检查边界

| 课程方已提供 | 学员需要完成 |
| --- | --- |
| 公开记录读取、完整公开记录转换、来源定位 | 选择访谈对象与问题，保留自己的原文 |
| 分批模型调用、严格 JSON 解析和错误提示 | 修改提取目标、提示词和配置；需要时调整或替换处理程序 |
| 跨记录冲突候选与来源检查 | 判断是否真有分歧、保留口径差异，规划下一步核实 |
| 候选与原文展示、两份文档模板 | 核查准备采用的信息，解释选择、修订与依据 |
| 输入、实现、Schema、配置、请求和响应快照 | 对照原始输出与修改结果，说明改动是否有效 |

只运行默认配置不证明掌握了提取；需要能说明自己的处理目标、漏项或错误，以及如何验证。采用其他实现或技术栈仍沿用相同产物契约；本工具的 `check-run` 当前只检查本骨架生成的运行记录，其他实现需导出相同公开运行证据；无法自动识别时由教师检查原始请求和实际复跑，不以采用其他语言判为不合格。

运行证据不含认证头和模型私有思考内容，但保留访谈原话与公开输出，应按项目资料管理。哈希、`mode=live` 和程序成功状态本身都不能证明业务正确，正式验收仍需复跑与人工审阅。

常见错误：`QUOTE_NOT_FOUND` 回看原文；`MODEL_CONTENT_EMPTY`、`MODEL_HTTP_*`、`MODEL_TIMEOUT` 检查端点和 profile；`HASH_MISMATCH` 使用旧快照或创建新版本。无模型配置、输入或路径错误发生在调用前，不会伪造一份模型运行记录。
