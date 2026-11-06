# W02：让 AI 协助持续整理材料与更新业务模型

模型调用默认单次 90 秒、输出截断后一次扩容重试，参数、运行记录和私有推理续接规则见 [模型配置与恢复](../model-configuration.md)。

本入口使用 `w02-workflow-0.1.0`。课程提供通用 Skill、有限步数的工具调用 Agent、提取与来源检查、运行报告和确认后写入。你负责本轮目标、材料、必要任务调整，以及业务核查和取舍。也可以单独使用[提取命令](../w02_extraction/README.md)。

## 1. 准备本轮任务

按根 README 安装 Python 3.11 以上环境和依赖，配置 `FDE_MODEL_URL`、`FDE_MODEL_NAME`、`FDE_MODEL_KEY`。使用与端点匹配的 profile；工作流还要求模型支持 Chat Completions 的原生工具调用。密钥放在环境变量中，不写入任务或 Skill。

在工程目录运行：

```sh
.venv/bin/python tools/w02_workflow.py init
```

初始化保留已有文件。需要更改的内容集中在以下位置：

| 文件 | 你改什么 |
| --- | --- |
| `customer/w02/workflow.json` | `goal`：本轮需要补充或核实什么；既有模型与核查记录的位置 |
| `customer/w02/extraction.json` | `sources`：自己的原始访谈；`focus_questions`：具体提取问题；与模型匹配的 `profile` |
| `customer/w02/extraction_prompt.txt` | 按本轮发现的问题，补充需要保留的条件、不同说法或表达要求 |
| `customer/w02/skills/extract-business/SKILL.md` | 需要时调整复用的方法；客户事实留在材料和模型中，避免写死进通用 Skill |

不是每次都要修改全部文件。首次用自己的材料时明确目标与输入；发现具体问题后，再调整相应要求。课程不要求重写通用 Agent，不按改动代码行数判分。

```sh
.venv/bin/python tools/w02_workflow.py inspect
.venv/bin/python tools/w02_workflow.py start
```

`inspect` 不调用模型。`start` 冻结本轮材料、方法、文档和源码快照；Agent 读取 Skill 后选择工具。一个工作仓库只操作一个当前任务，也可在子命令前加 `--workspace /path/to/客户仓库`。Windows 激活虚拟环境后使用 `python`；Windows、Linux 如遇问题，请反馈给课程方。

## 2. 分组核查，给出自己的判断

状态为 `awaiting_review` 时先查看报告：

```sh
.venv/bin/python tools/w02_workflow.py report
```

在浏览器打开返回的 `report.html`，选择“逐项核查”，查看候选和对应的完整原文。报告是本轮保存结果的只读展示，不会自动继续调用或替你批准。

检查准备采用的候选是否保留原意、对象和条件；再从目标与材料反查遗漏。给当前组写自然语言反馈，说明采用、修订或暂不采用及理由。可以直接输入简短反馈，也可读取你自己写的临时文本：

```sh
.venv/bin/python tools/w02_workflow.py review --feedback '写下你对当前组的核查判断、具体修订和理由。'
.venv/bin/python tools/w02_workflow.py resume
```

命令中的文字需要替换成自己的判断。若反馈较长，改用 `review --feedback-file feedback.txt`，该文件只是输入便捷方式，不是新增必交文档。程序将反馈自动留存。单组最多 4 项是本工具的交互容量，不是作业指标；暂不处理的候选可以保留到后续，不要求修齐所有机器字段。每次继续运行后重新生成报告，查看最新状态。

## 3. 审阅模型修改，确认后保存

状态为 `awaiting_apply` 时，报告的“模型修改”显示旧模型和 AI 草稿。核对原有有效内容是否保留、修改是否有依据、两种说法及未知是否完整。

需要修改草稿时：

```sh
.venv/bin/python tools/w02_workflow.py revise --feedback '写出哪一处需要修改，以及原文或业务判断的依据。'
.venv/bin/python tools/w02_workflow.py resume
.venv/bin/python tools/w02_workflow.py report
```

确认当前草稿后：

```sh
.venv/bin/python tools/w02_workflow.py apply
.venv/bin/python tools/w02_workflow.py report
```

`apply` 是你的确认动作。它保存业务模型，并向原有核查记录追加本轮理由、原始人工反馈和下一轮问题。旧模型、各版草稿和实际调用均自动保留；重复执行不会重复追加。若文件在本轮开始后被人工修改，工具会停止写入，保留草稿，请对照当前版本重新组织任务。

## 4. 接续下一轮，或交给 V06

补充材料或有新的调查目标时，先调整任务和提取配置，然后：

```sh
.venv/bin/python tools/w02_workflow.py start --previous latest
```

`latest` 指最近一次 `apply` 确认完成的任务，之后新建的任务即使失败或未完成也不影响接续；也可以直接写上一轮 `session.json` 的路径。上一轮需已确认完成。新任务读取当前业务模型与核查记录，同时接收上一轮的后续问题；原运行不覆盖。目标和材料只决定本轮工作范围。误判若来自提取要求或方法，需要修改提示词、配置或 Skill，再用有关材料验证；工具不会自动改写自己的方法。

需要把当前业务模型交给 V06 时，沿用 [知识入口](../w02_knowledge/README.md)。首次运行 `w02_knowledge.py init` 会保留已有两份文档；存在多次提取时，用 `--from-extraction` 指定工作流状态中显示的本轮 `extraction_run`。后续如何核对 Agent 是否采用，见 V06。

## 失败后怎么继续

`status` 查看当前停点，`report` 查看已有证据。调用失败、输出不完整、格式错误和步数达到上限会保留请求与公开返回，并标记未完成。工具不自动切换模型、降级或反复重试。核对错误、端点能力与任务规模后，明确重新 `start`；失败任务不能当成已完成，也不能 `apply`。

如果进程被强制关闭，先确认没有该仓库的工作流进程仍在运行，再移除 `artifacts/w02/.workflow.lock` 并查看状态。已进入 `applying` 的任务可再次执行 `apply` 继续完成保存；不覆盖人工另外修改过的文件。工具执行中按 Ctrl-C 或进程中断时，任务标记为未完成（`SESSION_INTERRUPTED`），不能 `resume`；其他执行中断同样按未完成处理，保留记录后重新 `start`。

报告、任务状态和候选属于程序产物。人工交付仍只有 `docs/w02/business_model.md` 与 `docs/w02/interview_notes.md`；按外层任务书保存同仓库程序与运行记录，不新增手填状态表或验证报告。结构检查和真实调用成功都不能替代业务判断。
