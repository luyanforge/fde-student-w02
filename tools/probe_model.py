#!/usr/bin/env python3
"""模型端点连通性预检：确认 URL / 模型名 / key 可用，再决定是否跑全量验证。

用法:
    .venv/Scripts/python.exe tools/with_env.py -- .venv/Scripts/python.exe tools/probe_model.py

只发一条最小请求，报告状态码、耗时、结束原因与用量；不打印密钥。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

REQUIRED = ("FDE_MODEL_URL", "FDE_MODEL_NAME", "FDE_MODEL_KEY")


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("缺少环境变量：" + ", ".join(missing), file=sys.stderr)
        return 3

    url = os.environ["FDE_MODEL_URL"]
    model = os.environ["FDE_MODEL_NAME"]
    key = os.environ["FDE_MODEL_KEY"]
    profile = os.environ.get("FDE_MODEL_PROFILE") or "compatible"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是连通性测试助手。"},
            {"role": "user", "content": "只回复两个字：收到"},
        ],
        "max_tokens": 32,
    }
    if profile.startswith("gateway"):
        body["extra_body"] = {"thinking": {"type": "disabled"}}
        body["cache"] = {"no-cache": True, "no-store": True}
    elif profile.startswith("deepseek"):
        body["thinking"] = {"type": "disabled"}

    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)

    print(f"端点 {url.split('?')[0]} | 模型 {model} | profile {profile}")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        detail = e.read(400).decode("utf-8", "replace")
        print(f"FAIL HTTP {e.code} | 耗时 {time.monotonic()-start:.1f}s")
        print("响应片段:", detail[:400].replace(key, "***"))
        return 1
    except Exception as e:  # 网络层失败
        print(f"FAIL {type(e).__name__} | 耗时 {time.monotonic()-start:.1f}s | {str(e)[:200]}")
        return 1

    elapsed = time.monotonic() - start
    try:
        data = json.loads(raw)
    except ValueError:
        print(f"FAIL 非 JSON 响应 | HTTP {status} | 片段: {raw[:300]}")
        return 1

    choices = data.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    content = (msg.get("content") or "").strip()
    finish = choices[0].get("finish_reason") if choices else None
    usage = data.get("usage") or {}

    print(f"PASS HTTP {status} | 耗时 {elapsed:.1f}s | finish_reason={finish}")
    print(f"回复内容: {content[:60]!r}")
    print(f"用量: {json.dumps(usage, ensure_ascii=False)}")
    if not content:
        print("注意：内容为空，可能该模型把正文放在 reasoning 字段，或需要不同的 profile。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
