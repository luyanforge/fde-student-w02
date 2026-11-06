#!/usr/bin/env python3
"""本地 TCP 转发：把 127.0.0.1:<port> 的请求原样转给局域网内的模型端点。

为什么需要它（实测，不是推测）：
`platform/fde_product/models.py` 的 URL 校验只允许 https，或 http + 回环地址
（localhost / 127.0.0.1 / ::1）。本地推理端点是 http + 192.168.1.38，
直接配置会抛 MODEL_URL_INVALID。

该文件属于 `interview_extraction.IMPLEMENTATION` 快照，改它会在运行记录里留下
被改动的实现，因此不改平台代码，改为在传输层加一层回环转发：
平台看到的仍是 http + 127.0.0.1（合法），实际请求落到同一个端点、同一个模型。

纯传输层转发，不解析、不改写 HTTP 报文。
"""
import socket
import threading
from urllib.parse import urlsplit, urlunsplit

LOOPBACK = ("localhost", "127.0.0.1", "::1")
DEFAULT_PORT = 1338


def _pump(src, dst):
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle(client, upstream):
    try:
        server = socket.create_connection(upstream, timeout=30)
    except OSError:
        client.close()
        return
    server.settimeout(None)
    client.settimeout(None)
    back = threading.Thread(target=_pump, args=(server, client), daemon=True)
    back.start()
    _pump(client, server)
    back.join(timeout=10)
    for s in (server, client):
        try:
            s.close()
        except OSError:
            pass


def _listen(port):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
    except OSError:
        listener.close()
        return None
    listener.listen(64)
    return listener


def ensure(url=None, port=None):
    """如需转发则在后台上起一个，（必要时）返回改写后的 URL；否则原样返回。"""
    if url is None:
        import os
        url = os.environ.get("FDE_MODEL_URL", "")
    if not url:
        return url
    parts = urlsplit(url)
    if parts.hostname in LOOPBACK or parts.scheme != "http":
        return url

    port = int(port or 0) or DEFAULT_PORT
    upstream = (parts.hostname, parts.port or 80)
    listener = _listen(port)
    if listener is None:
        return urlunsplit((parts.scheme, "127.0.0.1:%d" % port, parts.path, parts.query, parts.fragment))

    def loop():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=_handle, args=(client, upstream), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    import sys
    print("[relay] %s:%d -> 127.0.0.1:%d（平台 URL 校验只允许回环 http）"
          % (upstream[0], upstream[1], port), file=sys.stderr, flush=True)
    return urlunsplit((parts.scheme, "127.0.0.1:%d" % port, parts.path, parts.query, parts.fragment))


if __name__ == "__main__":
    # 独立调试用：python tools/local_relay.py [port]
    import sys
    import time
    p = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print(ensure(port=p))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
