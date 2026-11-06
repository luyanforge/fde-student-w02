#!/usr/bin/env python3
"""探测本地端点（Atomic Chat / llama.cpp）对工作流所需三项能力的支持：

1. 正文输出（思考模式下 content 是否能拿到）
2. 原生 tool_calls（且零参数工具的 arguments 形态）
3. response_format=json_object

只读探测，不写任何运行记录。
"""
import json
import os
import time
import urllib.error
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
URL = os.environ["FDE_MODEL_URL"]
KEY = os.environ["FDE_MODEL_KEY"]
MODEL = os.environ["FDE_MODEL_NAME"]


def post(payload, timeout=180):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        URL, data=body,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0


def show(tag, status, data, secs):
    print(f"\n=== {tag} | HTTP {status} | {secs:.1f}s ===")
    if not isinstance(data, dict):
        print("  报错体:", str(data)[:300])
        return
    msg = data["choices"][0]["message"]
    usage = data.get("usage", {})
    print("  finish:", data["choices"][0].get("finish_reason"),
          "| completion:", usage.get("completion_tokens"),
          "| reasoning:", usage.get("completion_tokens_details", {}).get("reasoning_tokens"))
    print("  content:", repr(msg.get("content"))[:300])
    if msg.get("reasoning_content"):
        print("  reasoning(前120):", repr(msg["reasoning_content"])[:120])
    if msg.get("tool_calls"):
        print("  tool_calls:", json.dumps(msg["tool_calls"], ensure_ascii=False)[:400])


TEXT = [{"role": "user", "content": "只回答一个词：可用"}]

# 1. 正文输出（给足 token 让思考跑完）
show("1 正文输出 max_tokens=600", *post({"model": MODEL, "messages": TEXT, "max_tokens": 600, "stream": False}))

# 1b. 尝试关思考：chat_template_kwargs.enable_thinking=false
show("1b enable_thinking=false", *post({
    "model": MODEL, "messages": TEXT, "max_tokens": 300, "stream": False,
    "chat_template_kwargs": {"enable_thinking": False}}))

# 2. 零参数工具调用（模拟 extract_candidates）
TOOLS = [{
    "type": "function",
    "function": {
        "name": "extract_candidates",
        "description": "从访谈记录中提取候选事实。参数为空对象。",
        "parameters": {"type": "object", "properties": {}},
    },
}, {
    "type": "function",
    "function": {
        "name": "list_sources",
        "description": "列出已载入的访谈记录文件名。",
        "parameters": {"type": "object", "properties": {}},
    },
}]
show("2 零参数 tool_calls", *post({
    "model": MODEL,
    "messages": [{"role": "user", "content": "请调用 list_sources 工具，不要用文字回答。"}],
    "tools": TOOLS, "tool_choice": "auto", "max_tokens": 600, "stream": False}))

# 3. json_object
show("3 response_format=json_object", *post({
    "model": MODEL,
    "messages": [{"role": "user", "content": '只输出 JSON：{"ok":true}'}],
    "response_format": {"type": "json_object"}, "max_tokens": 600, "stream": False}))
