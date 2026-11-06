# 图书馆：持续提取工作流示例

材料、角色和起始模型均为教学虚构。起点承接 V04 的对象草图，明确保留待核内容；本例不预置 AI 候选或核查答案。该示例用于熟悉方法和检查环境，正式作业使用自己的青禾访谈，无需完整重跑此例作为额外必交。

在独立解压的示例工程中：

1. 运行 `tools/w02_workflow.py init`。
2. 将本目录 `business_model_start.md`、`interview_notes_start.md` 的内容复制到新建的两份文档。若目录里已有自己的客户文档，请改用独立工作目录，保留原文档。
3. 在 `examples/w02_workflow/extraction.json` 中选择自己的模型 profile。保留期和自动提醒为本轮问题，示例配置没有写入标准答案。
4. 运行 `tools/w02_workflow.py inspect --task examples/w02_workflow/workflow.json`，再运行 `start --task examples/w02_workflow/workflow.json`。
5. 按 [工作流说明](../../docs/w02_workflow/README.md)查看真实候选、逐组反馈、审阅草稿并确认。`report` 输出真实运行的只读报告，可在浏览器查看。

上面命令需在前面加当前 Python 入口，例如 `.venv/bin/python`。Skill 默认取自课程模板；研究修改时复制到自己的客户目录，并更新任务的 `skill` 路径。自己的任务使用 `customer/w02/workflow.json`，无需修改课程公共示例。

文档确认后沿用 [自然语言模型接入](../../docs/w02_knowledge/README.md)，检查业务 Agent 是否实际采用。不要把虚构图书馆拿去运行青禾必访覆盖验收。
