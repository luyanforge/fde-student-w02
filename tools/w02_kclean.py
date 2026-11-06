#!/usr/bin/env python3
"""知识库问答启动器：与 w02_clean.py 同一套传输层与解析层处理。

w02_clean.py 走的是提取工作流（w02_workflow.py），这里走知识库问答
（w02_knowledge.py）。两件事一样：让 URL 通过校验、让自由文本能被读成 JSON。
请求体与回答内容都不改。

用法：
    .venv/Scripts/python.exe tools/with_env.py -- \
        .venv/Scripts/python.exe tools/w02_kclean.py compare --question "..." --expected "..."
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "tools"))

from w02_transport import prepare  # noqa: E402

prepare()

from fde_product import interview_extraction as ie  # noqa: E402
from w02_parse import make_tolerant_parse  # noqa: E402


def _say(message):
    print(message, file=sys.stderr, flush=True)


tolerant = make_tolerant_parse(ie.parse_json, _say)
ie.parse_json = tolerant

# 各模块在 import 时就绑定了同名函数，要逐个替换，否则补丁不生效。
for module_name in ("natural_knowledge", "extraction_workflow", "knowledge_io", "agent"):
    try:
        module = __import__(f"fde_product.{module_name}", fromlist=["parse_json"])
    except Exception:
        continue
    if hasattr(module, "parse_json"):
        module.parse_json = tolerant

sys.argv = [str(ROOT / "tools" / "w02_knowledge.py")] + sys.argv[1:]
from w02_knowledge import main  # noqa: E402

raise SystemExit(main())
