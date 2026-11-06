#!/usr/bin/env python3
"""单批试跑：只调模型、只做本地校验，不创建运行记录。

用途是在正式 start 之前先摸清一批的真实表现（耗时、输出规模、quote 命中率），
避免把「注定失败的运行」写进 artifacts/w02/runs。它不写 run.json，
也不改动任何候选内容。

用法：
    .venv/Scripts/python.exe tools/with_env.py -- \
        .venv/Scripts/python.exe tools/w02_probe.py 0
    # 参数是批次序号（从 0 开始）；省略则逐批全跑
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "tools"))

from fde_product import interview_extraction as ie  # noqa: E402
from w02_parse import make_tolerant_parse  # noqa: E402
from w02_transport import prepare  # noqa: E402

prepare()
ie.parse_json = make_tolerant_parse(ie.parse_json, lambda m: print(m, file=sys.stderr, flush=True))

CONFIG = ROOT / "customer/w02/extraction.json"
OUT = ROOT / "artifacts/w02/probe"


def check(parsed, transcripts):
    """按平台的校验口径逐条检查，返回 (通过数, 问题清单)。"""
    problems = []
    claims = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(claims, list) or not claims:
        return 0, [("SCHEMA", "claims 不是非空数组")], 0
    total = 0
    by_meeting = {}
    for t in transcripts:
        by_meeting[t["meeting_id"]] = {m["id"]: m for m in t["messages"]}
    for claim in claims:
        total += 1
        label = claim.get("id", "?")
        for evidence in claim.get("evidence") or []:
            meeting = by_meeting.get(evidence.get("meeting_id"))
            if meeting is None:
                problems.append((label, f"meeting_id 不存在：{evidence.get('meeting_id')}"))
                continue
            message = meeting.get(evidence.get("message_id"))
            if message is None:
                problems.append((label, f"message_id 不在该场访谈：{evidence.get('message_id')}"))
                continue
            if evidence.get("quote") not in message["content"]:
                problems.append((label, f"quote 原文搜不到：{evidence.get('quote')!r}"))
            elif message.get("speaker") != "assistant":
                problems.append((label, f"引用了访谈者的话（不阻断校验，但方法上不成立）：{evidence.get('quote')!r}"))
    return total - len({p[0] for p in problems}), problems, total


def main() -> int:
    cfg, transcripts = ie.load_config(ie.Workspace(ROOT), CONFIG.relative_to(ROOT).as_posix())
    parts = ie.batches(transcripts, cfg)
    prompt = (ROOT / cfg["prompt"]).read_text(encoding="utf-8")
    indexes = [int(sys.argv[1])] if len(sys.argv) > 1 else range(len(parts))
    OUT.mkdir(parents=True, exist_ok=True)

    model = ie.ExtractionModel(profile=cfg["profile"])
    print(f"批次总数 {len(parts)}｜批次消息数 {[len(p) for p in parts]}｜模型 {model.model}", flush=True)

    for index in indexes:
        part = parts[index]
        messages = ie.model_messages("extract", part, cfg, prompt)
        started = time.monotonic()
        message, usage = model.complete_phase("extract", messages, [])
        elapsed = time.monotonic() - started
        content = message.get("content") or ""
        print(f"\n=== 批次 {index}｜时长 {elapsed:.1f}s｜usage {json.dumps(usage, ensure_ascii=False)}"
              f"｜正文 {len(content)} 字符", flush=True)
        (OUT / f"probe-{index}.raw.txt").write_text(content, encoding="utf-8")
        try:
            parsed = ie.parse_json(content)
        except Exception as exc:
            print(f"  解析失败：{type(exc).__name__}: {exc}", flush=True)
            print(f"  原文已存到 {OUT / f'probe-{index}.raw.txt'}", flush=True)
            continue
        try:
            ie.validate(ie.payload_schema("extract"), parsed)
            print("  schema：通过", flush=True)
        except Exception as exc:
            print(f"  schema：失败 {getattr(exc, 'code', type(exc).__name__)} {getattr(exc, 'location', '')}", flush=True)
        passed, problems, total = check(parsed, transcripts)
        print(f"  候选 {total} 条｜字段齐全 {passed} 条｜问题 {len(problems)} 条", flush=True)
        for label, detail in problems:
            print(f"    - {label}: {detail}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
