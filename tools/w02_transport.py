#!/usr/bin/env python3
"""传输层准备：让模型的 URL 能通过 models.py 的校验，并且真的连得上。

两件事，都只影响「请求能不能发出去」，不改请求体、不改响应：

  1. URL 校验：models.py 只允许 https，或 http + 回环地址。局域网端点是
     http + 192.168.1.38，直接配置会被判 MODEL_URL_INVALID，所以起一个
     回环转发，平台看到 http + 127.0.0.1，实际落到原端点。
  2. 代理：回环端点必须绕开 HTTP(S)_PROXY，否则请求被代理拦下，表现为
     MODEL_NETWORK_ERROR；远端 https 端点反过来必须让代理生效。
"""
import os
from urllib.parse import urlsplit

LOOPBACK = ("localhost", "127.0.0.1", "::1")
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


def prepare(verbose=True):
    """按 FDE_MODEL_URL 决定是否起回环转发，返回实际可用的 URL。"""
    configured = os.environ.get("FDE_MODEL_URL", "")
    parsed = urlsplit(configured)
    host = (parsed.hostname or "").lower()
    if not host:
        if verbose:
            print("[transport] 未设置 FDE_MODEL_URL：本次不调用模型（只读命令如 status/report 属正常）", flush=True)
        return configured
    needs_relay = parsed.scheme == "http" and host not in LOOPBACK

    if needs_relay:
        for name in PROXY_VARS:
            os.environ.pop(name, None)
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        import sys

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from local_relay import ensure

        url = ensure()
        if verbose:
            print(f"[transport] 回环转发 {parsed.netloc} -> {url}（仅传输层）", flush=True)
    else:
        url = configured
        if verbose:
            print(f"[transport] {host} 直连（{parsed.scheme}，沿用系统代理设置）", flush=True)
    os.environ["FDE_MODEL_URL"] = url
    return url
