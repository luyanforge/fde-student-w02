#!/usr/bin/env python3
"""解析层容错：把模型的自由文本读成 JSON，不改变其中任何一条主张。

三件事都属于「同一份回答的读法」问题，不是内容问题：parse_json 是平台与
validate_run 共用的入口，patch 后两边读出同一个结构，记录前后自洽。

  ① 零参数工具被写成 {}{} 这类重复空对象
  ② 输出被 ```json 围栏包住
  ③ 正文先复述 schema、真正的 claims 数组在后面
  ④ qualifiers 被写成 ["k":"v"] 这种非法 JSON
"""
import json as _json
import re

_EMPTY_OBJECTS = re.compile(r"^(\{\s*\}){2,}$")
_FENCED = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _salvage_claims(text, parses):
    """从「先复述 schema、后给数据」的长文本里取出末尾的 claims 数组。"""
    idx = len(text)
    while True:
        idx = text.rfind('"claims"', 0, idx)
        if idx < 0:
            return None
        start = text.find("[", idx)
        if start >= 0:
            depth, quote, escape = 0, False, False
            for pos in range(start, len(text)):
                ch = text[pos]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    quote = not quote
                    continue
                if quote:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            value = _json.loads(text[start:pos + 1])
                        except ValueError:
                            value = None
                        if (isinstance(value, list)
                                and all(isinstance(i, dict) and "id" in i and "statement" in i
                                        and isinstance(i.get("evidence"), list) for i in value)):
                            return {"claims": value}
                        break
        idx = idx - 1 if idx else -1
        if idx <= 0:
            return None


def _fix_qualifiers(text):
    """把 qualifiers 里误写成 ["k":"v"] 的项读成字符串 "k=v"。

    这类写法不是合法 JSON，但意思清楚：只把数组内部的 `"a":"b"` 改成 `"a=b"`，
    不增删任何词，也不动 qualifiers 以外的任何字段。
    """
    pattern = re.compile(r'"qualifiers"\s*:\s*\[(.*?)\]', re.S)

    def repl(match):
        body = match.group(1)
        if "{" in body or "}" in body or ":" not in body:
            return match.group(0)
        return '"qualifiers":[' + re.sub(r'"\s*:\s*"', "=", body) + "]"

    return pattern.sub(repl, text)


def make_tolerant_parse(original_parse, log=None):
    say = log or (lambda message: None)

    def tolerant_parse_json(text):
        if isinstance(text, str):
            stripped = text.strip()
            if stripped and stripped != "{}" and _EMPTY_OBJECTS.match(stripped):
                say(f"[args-normalized] {stripped!r} -> '{{}}'")
                text = "{}"
            elif stripped.startswith("```"):
                fenced = _FENCED.match(text)
                if fenced:
                    say("[fence-stripped] 去掉代码围栏后解析")
                    text = fenced.group(1)
        failure = None
        for candidate, tag in ((text, None), (_fix_qualifiers(text), "[qualifier-fixed]")):
            try:
                return original_parse(candidate)
            except Exception as exc:  # 记下第一次的真实失败原因，用于最终抛回
                failure = failure or exc
                if tag:
                    say(tag + " qualifiers 键值对改写为字符串后解析")
                    salvaged = _salvage_claims(candidate, original_parse)
                    if salvaged is not None:
                        return salvaged
                    continue
        # 问答类的回答（answer）不是 claims 结构，抢救 claims 无意义；
        # 只有抢救失败时才把最初的解析错误抛回去。
        salvaged = _salvage_claims(text, original_parse)
        if salvaged is None:
            raise failure if failure is not None else ValueError("无法解析模型输出")
        say("[claims-salvaged] 正文前置 schema 复述，恢复末尾 claims（%d 条）" % len(salvaged["claims"]))
        return salvaged

    return tolerant_parse_json
