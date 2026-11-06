#!/usr/bin/env python3
"""干净提取启动器：只处理传输层与代理，不改请求内容、不改候选数据。

与 tools/w02_run.py 的区别（那是早期为让本地模型跑通做的「表示层适配」）：
  - 不注入 temperature / top_p / repeat_penalty（那是改请求体，会让
    validate_run 里 expected_model_request 重算的请求与记录不符）
  - 不修改解析后的 claims（不做 _locate / _repair_quotes / 结构归位），
    否则 make_facts 重算的候选与落盘的 candidates.json 对不上

保留的两件事都只影响「能不能发出去」，不影响记录内容：
  1. 端点位置：回环端点（局域网 http）需要起转发线程并清掉代理；远端 https
     端点相反，必须让系统代理生效。models.py 的 URL 校验只允许 https 或
     http+回环，所以本地端点必须走转发。
  2. 解析层容错，见 tools/w02_parse.py。

用法：
    .venv/Scripts/python.exe tools/with_env.py -- \
        .venv/Scripts/python.exe tools/w02_clean.py start
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "tools"))

# ---- 传输层 ------------------------------------------------------------
from w02_transport import prepare  # noqa: E402

prepare()
print("[clean] 不注入采样参数、不修改候选数据", file=sys.stderr, flush=True)

# ---- 解析层容错 ---------------------------------------------------------
# 只把模型的自由文本读成 JSON，不改变其中任何一条主张、证据或限定条件。
from fde_product import interview_extraction as ie  # noqa: E402
from w02_parse import make_tolerant_parse  # noqa: E402


def _say(message):
    print(message, file=sys.stderr, flush=True)


tolerant_parse_json = make_tolerant_parse(ie.parse_json, _say)

ie.parse_json = tolerant_parse_json
try:  # 工作流 Agent 侧另有一份同名引用
    from fde_product import extraction_workflow as ew  # noqa: E402
    ew.parse_json = tolerant_parse_json
except Exception:  # 模块名变化时不影响主流程
    pass

sys.argv = [str(ROOT / "tools" / "w02_workflow.py")] + sys.argv[1:]
from w02_workflow import main  # noqa: E402

raise SystemExit(main())
