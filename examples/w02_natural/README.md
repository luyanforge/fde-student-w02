# 青橡图书馆：提取、核查与自然语言接入

这是 V04～V06 共用的虚构教学材料。程序实际调用自己的模型，示例不预置模型回答；材料来源和调用方式分别记录。以下操作可用于熟悉工具，不是青禾作业的额外必交项。

## 1. 在独立解压的示例工程中提取

另解压一份工程包专门运行本示例，不要在自己的客户仓库或准备做青禾作业的目录里运行。示例会生成提取记录和 `customer/w02/knowledge.json`，配置绑定图书馆的范围和模型配置。如果已经在同一目录运行过示例，改做青禾时按[知识入口](../../docs/w02_knowledge/README.md)用 `--replace` 改用自己的提取，并替换两份文档里的图书馆内容。

按根 README 安装依赖、配置模型环境变量，在 `examples/w02_extraction/extraction.json` 中选择端点支持的 profile，然后运行：

```sh
.venv/bin/python tools/w02_interviews.py inspect --config examples/w02_extraction/extraction.json
.venv/bin/python tools/w02_interviews.py extract --config examples/w02_extraction/extraction.json
```

程序返回此次 `run.json` 的路径。查看同目录的 `candidates.json`，结合 [front_desk.json](../w02_extraction/front_desk.json) 和 [manager.json](../w02_extraction/manager.json) 核对条件、定义及两种保留期限。

## 2. 核查后写业务模型

```sh
.venv/bin/python tools/w02_knowledge.py init
```

只有一次成功提取时自动采用；有多次时在命令后加 `--from-extraction` 和实际运行路径。初始化会复制空白模板。核对后，可将 [business_model.md](business_model.md) 的内容复制到 `docs/w02/business_model.md`，理解其中哪些来自材料、哪些是教师的建模选择。自己的核查写入另一份文档 `docs/w02/interview_notes.md`。

## 3. 运行并判读

```sh
.venv/bin/python tools/w02_knowledge.py inspect
.venv/bin/python tools/w02_knowledge.py compare --question '一次借阅怎样关联读者和馆藏副本？' --expected '提供定义后，应说明一次借阅恰好关联一位读者和一本副本。'
.venv/bin/python tools/w02_knowledge.py compare --question '每位读者能同时借多少本？' --expected '材料没有给出上限，回答应保留未知，不能编造数量。'
```

这两道题仅供示范。每组会实际调用模型两次，分别保存未提供／提供业务模型的请求与回答。检查 `request.json` 与 `answer.json`，不能凭运行成功判断业务正确。详细操作见 [知识入口](../../docs/w02_knowledge/README.md)。

图书馆没有青禾三位必访角色和 W1 提交，因此不要拿图书馆目录运行青禾作业总预检并期待通过。正式作业用自己的客户仓库和访谈记录，问题自己选择。
