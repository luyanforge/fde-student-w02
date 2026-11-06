#!/usr/bin/env python3
"""青禾访谈站最小客户端（本机自用，非课程工具）。

作用：走与网页相同的接口做访谈，便于把"提取用记录"直接落到客户仓库。
不打印、不落盘登录码；登录码只从文件读取。

登录码文件（默认）：D:/0_courses/fde/interview.key
    首行非空内容即为登录码。可用环境变量 FDE_INTERVIEW_CODE_FILE 覆盖路径。

用法（工程根目录）：
    .venv/Scripts/python.exe tools/w02_site.py login
    .venv/Scripts/python.exe tools/w02_site.py state
    .venv/Scripts/python.exe tools/w02_site.py create --week 2 --role ROLE-ZHOU --purpose "…"
    .venv/Scripts/python.exe tools/w02_site.py send --meeting <id> --text "…"
    .venv/Scripts/python.exe tools/w02_site.py send --meeting <id> --text-file q.txt
    .venv/Scripts/python.exe tools/w02_site.py show --meeting <id>
    .venv/Scripts/python.exe tools/w02_site.py export --meeting <id> --out artifacts/w02/sources/x.json
    .venv/Scripts/python.exe tools/w02_site.py end --meeting <id>
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("FDE_INTERVIEW_BASE", "http://43.179.179.101")
SESSION_FILE = ROOT / ".runtime" / "interview" / "session.json"
DEFAULT_KEY_FILE = Path("D:/0_courses/fde/interview.key")

ROLE_NAMES = {
    "ROLE-SHEN": "沈总",
    "ROLE-ZHOU": "老周",
    "ROLE-LIN": "小林",
    "ROLE-CHEN": "陈姐",
    "ROLE-WANG": "王经理",
    "ROLE-ZHAO": "赵总监",
}


class SiteError(RuntimeError):
    pass


def read_code() -> str:
    path = Path(os.environ.get("FDE_INTERVIEW_CODE_FILE") or DEFAULT_KEY_FILE)
    if not path.is_file():
        raise SiteError(f"未找到登录码文件：{path}（请把登录码写入该文件首行）")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SiteError(f"登录码文件为空：{path}")


class Client:
    def __init__(self) -> None:
        self.s = requests.Session()
        # 站点做 Origin/Referer 校验，缺了会 403 ORIGIN_REJECTED
        self.s.headers.update(
            {
                "Origin": BASE,
                "Referer": BASE + "/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            }
        )
        self.csrf = ""
        self.identity = ""
        self._load()

    # ---- 会话持久化 -------------------------------------------------
    def _load(self) -> None:
        if not SESSION_FILE.is_file():
            return
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        self.s.cookies.update(data.get("cookies") or {})
        self.csrf = data.get("csrf") or ""
        self.identity = data.get("identity") or ""

    def _save(self) -> None:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cookies": requests.utils.dict_from_cookiejar(self.s.cookies),
            "csrf": self.csrf,
            "identity": self.identity,
        }
        SESSION_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ---- 请求 -------------------------------------------------------
    def call(self, path: str, body=None, timeout: int = 30, _retry: bool = True):
        headers = {"Content-Type": "application/json"}
        if body is not None:
            headers["X-FDE-CSRF"] = self.csrf
        try:
            r = self.s.request(
                "POST" if body is not None else "GET",
                BASE + path,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            raise SiteError("REQUEST_TIMEOUT")
        try:
            data = r.json()
        except ValueError:
            raise SiteError(f"非 JSON 响应 HTTP {r.status_code}")
        if not r.ok:
            code = data.get("error") or "ERROR"
            # 会话过期：自动重新登录一次再重试，避免长访谈中途掉线
            if code == "LOGIN_REQUIRED" and _retry and path != "/api/login":
                self.relogin()
                return self.call(path, body, timeout, _retry=False)
            raise SiteError(f"{code} (HTTP {r.status_code})")
        self._save()
        return data

    def relogin(self) -> None:
        self.s.cookies.clear()
        self.csrf = ""
        self.call("/api/login", {"code": read_code()}, _retry=False)
        self.state()
        print("（会话已过期，已自动重新登录）")

    def login(self) -> dict:
        code = read_code()
        self.call("/api/login", {"code": code})
        state = self.state()
        print(f"登录成功：{state.get('label')} · {state.get('cohort')} · mode={state.get('mode')}")
        return state

    def state(self) -> dict:
        data = self.call("/api/state")
        self.csrf = data.get("csrf") or self.csrf
        self.identity = data.get("member_id") or self.identity
        self._save()
        return data

    # ---- 业务动作 ---------------------------------------------------
    def create(self, week: int, role: str, purpose: str) -> dict:
        body = {"week": week, "role_id": role, "purpose": purpose, "request_id": str(uuid.uuid4())}
        return self.call("/api/create", body)

    def send(self, meeting_id: str, text: str) -> dict:
        rid = str(uuid.uuid4())
        try:
            return self.call("/api/message", {"meeting_id": meeting_id, "text": text, "request_id": rid}, timeout=120)
        except SiteError as e:
            if str(e) != "REQUEST_TIMEOUT":
                raise
        print("… 页面响应超时，改为轮询原请求（不重发）")
        deadline = time.monotonic() + 660
        while time.monotonic() < deadline:
            time.sleep(5)
            res = self.call("/api/request", {"meeting_id": meeting_id, "request_id": rid}, timeout=20)
            st = res.get("request_state")
            if st == "done":
                return res["meeting"]
            print(f"… request_state={st}，继续等待")
            if st == "not_found":
                raise SiteError("REQUEST_NOT_FOUND（原问题未送达，请人工核对）")
        raise SiteError("REQUEST_PENDING_TOO_LONG")

    def meeting(self, meeting_id: str) -> dict:
        return self.call("/api/meeting", {"meeting_id": meeting_id})

    def export(self, meeting_id: str) -> dict:
        return self.call("/api/export", {"meeting_id": meeting_id})

    def end(self, meeting_id: str) -> dict:
        return self.call("/api/end", {"meeting_id": meeting_id})


def fmt_state(data: dict) -> str:
    lines = [f"账号 {data.get('label')} · {data.get('cohort')} · mode={data.get('mode')}"]
    for w in data.get("weeks") or []:
        st = {"locked": "未开放", "open": "开放中", "closed": "已关闭"}.get(w.get("status"), w.get("status"))
        roles = "；".join(
            f"{r.get('role_name')}({r.get('role_id')}) 剩{r.get('remaining')}次"
            + ("［必访］" if r.get("required") else "")
            for r in (w.get("roles") or [])
        )
        lines.append(f"W{w.get('week')} {st} · {roles}")
    return "\n".join(lines)


def fmt_meeting(m: dict, tail: int = 0) -> str:
    head = (
        f"会议 {m.get('meeting_id')} · W{m.get('week')} · {ROLE_NAMES.get(m.get('role_id'), m.get('role_id'))} "
        f"· state={m.get('state')} · 消息 {len(m.get('messages') or [])} 条"
    )
    msgs = m.get("messages") or []
    if tail:
        msgs = msgs[-tail:]
    out = [head]
    for msg in msgs:
        who = {"user": "我", "assistant": ROLE_NAMES.get(m.get("role_id"), "客户")}.get(msg.get("speaker"), "系统")
        out.append(f"\n[{msg.get('id')}] {who}：\n{msg.get('content')}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="青禾访谈站最小客户端")
    sub = ap.add_subparsers(dest="action", required=True)

    sub.add_parser("login")
    sub.add_parser("state")

    p = sub.add_parser("create")
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--purpose", required=True)

    p = sub.add_parser("send")
    p.add_argument("--meeting", required=True)
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--tail", type=int, default=2)

    p = sub.add_parser("show")
    p.add_argument("--meeting", required=True)
    p.add_argument("--tail", type=int, default=0)

    p = sub.add_parser("export")
    p.add_argument("--meeting", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("end")
    p.add_argument("--meeting", required=True)

    args = ap.parse_args()
    try:
        if args.action == "login":
            print(fmt_state(Client().login()))
            return 0
        c = Client()
        if args.action == "state":
            print(fmt_state(c.state()))
        elif args.action == "create":
            m = c.create(args.week, args.role, args.purpose)
            print(fmt_meeting(m))
        elif args.action == "send":
            text = args.text
            if args.text_file:
                text = Path(args.text_file).read_text(encoding="utf-8")
            if not text or not text.strip():
                raise SiteError("消息为空")
            m = c.send(args.meeting, text.strip())
            print(fmt_meeting(m, tail=args.tail or 2))
        elif args.action == "show":
            print(fmt_meeting(c.meeting(args.meeting), tail=args.tail))
        elif args.action == "export":
            data = c.export(args.meeting)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            n = len(data.get("messages") or [])
            print(f"已导出 {out} · {n} 条消息 · contract={data.get('contract_version')}")
        elif args.action == "end":
            print(fmt_meeting(c.end(args.meeting)))
        return 0
    except SiteError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
