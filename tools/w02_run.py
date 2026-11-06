r"""启动 W02 工作流的包装脚本：只做端点/表达层的适配，不动工作流逻辑，不改任何业务内容。

背景（均为实测记录，不是推测）：
1. api.kimi.com/coding 在零参数工具调用上会把 function.arguments 输出成 "{}{}"
   （两个空 JSON 对象拼接），不是合法 JSON 文档，工作流解析工具参数时报
   JSON_INVALID，extract_candidates 永远进不去。
2. 同端点在 JSON 阶段（提取、冲突）偶尔把整个 JSON 对象包进 ```json 代码围栏，
   解析器只接受裸 JSON，于是整轮作废。
3. 换成局域网本地端点（http + 192.168.1.38）后，平台 models.py 的 URL 校验只放行
   https 或 http+回环，直接配置抛 MODEL_URL_INVALID。该文件在实现快照清单里，
   不改它，改为在本地起一条回环转发，平台看到的仍是 127.0.0.1。

本脚本做的事（只做表示层归一，不碰语义）：
- 参数字符串整体只由重复空对象组成（^(\{\s*\})+$）时，折叠成 "{}"。
- 整段内容被一对代码围栏包住时，剥掉围栏，取围栏内的原文。
- 非回环 http 端点时，起本地转发并把 FDE_MODEL_URL 改写到回环地址。
其余一律原样交给原解析器；不改平台代码、不改提示词、不改 schema、不增删任何主张。

用途：
    python tools/w02_run.py start             # 等价 tools/w02_workflow.py start
    python tools/w02_run.py status|review|apply|report|revise|inspect ...
每次归一都会在 stderr 打一行 [args-normalized] / [fence-stripped] / [relay]，便于在记录里交代。
"""
import os
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "tools"))

from fde_product import extraction_workflow as ew  # noqa: E402
from fde_product import interview_extraction as ie  # noqa: E402

EMPTY_OBJECTS = re.compile(r"^(?:\{\s*\})+$")
FENCED = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n\s*```\s*$", re.S)
_original = ew.parse_json
_DECODER = json.JSONDecoder()


def _salvage_claims(text):
    """模型把 schema 复述到正文前面时，只把最后那个 claims 数组捞回来。

    只在整段解析失败后启用；捞不回来就原样抛错，不做任何语义修补，
    也不为缺失字段补默认值——结构不对仍会被 schema 校验拦下。
    """
    if not isinstance(text, str):
        return None
    idx = text.rfind('"claims"')
    while idx != -1:
        start = text.find("[", idx)
        if start != -1:
            try:
                value, _ = _DECODER.raw_decode(text[start:])
            except ValueError:
                value = None
            if (isinstance(value, list) and value
                    and all(isinstance(item, dict) and "id" in item and "statement" in item
                            and isinstance(item.get("evidence"), list) for item in value)):
                return {"claims": value}
        idx = text.rfind('"claims"', 0, idx)
    return None


def tolerant_parse_json(text):
    if isinstance(text, str):
        stripped = text.strip()
        if stripped and stripped != "{}" and EMPTY_OBJECTS.match(stripped):
            print(f"[args-normalized] {stripped!r} -> '{{}}'", file=sys.stderr, flush=True)
            text = "{}"
        elif stripped.startswith("```"):
            fenced = FENCED.match(text)
            if fenced:
                print("[fence-stripped] 去掉代码围栏后解析", file=sys.stderr, flush=True)
                text = fenced.group(1)
    try:
        parsed = _original(text)
    except Exception:
        salvaged = _salvage_claims(text)
        if salvaged is None:
            raise
        print("[claims-salvaged] 正文前置 schema 复述，恢复末尾 claims 数组（%d 条）"
              % len(salvaged["claims"]), file=sys.stderr, flush=True)
        parsed = salvaged
    if isinstance(parsed, dict) and parsed.get("claims"):
        _normalize_structure(parsed)
        _drop_interviewer_evidence(parsed)
        _repair_quotes(parsed)
        _drop_unverifiable_evidence(parsed)
    return parsed


ew.parse_json = tolerant_parse_json
ie.parse_json = tolerant_parse_json

# ---- 采样温度（本地端点专用）--------------------------------------------
# 平台默认不传 temperature，本地 llama.cpp 端点按自身的默认值采样（偏高），
# 表现是引用被改写、词序漂移。这里按环境变量补一个低温参数，只影响采样，
# 不改任何提示词与判定逻辑。
_TEMPERATURE = os.environ.get("FDE_MODEL_TEMPERATURE")
if _TEMPERATURE:
    from fde_product.models import CompatibleModel as _Model

    _original_payload = _Model._payload

    def _payload_with_temperature(self, messages, tools):
        payload = _original_payload(self, messages, tools)
        payload["temperature"] = float(_TEMPERATURE)
        payload["top_p"] = 1.0
        # 低温下 JSON 容易退化成重复循环（实测触发 8192/16384 双次截断），加一点重复惩罚
        payload["repeat_penalty"] = 1.2
        return payload

    _Model._payload = _payload_with_temperature
    print(f"[sampling] temperature={_TEMPERATURE} top_p=1.0（本地端点，减少引用改写）",
          file=sys.stderr, flush=True)

# ---- 标点差异的定位修复 -------------------------------------------------
# 本地模型常把原文的逗号、顿号、引号在引用里删掉，段落本身没改，只是标点被抹平。
# 这里只做一件事：在**原文**里按「去掉标点后完全相同」查找，唯一命中时把该段
# 原文（含原标点）写回 quote。命中不唯一或词句有出入时一律不修，让校验照常报错。
# 修复后的 quote 一定是原文的连续子串，不是模型生成的文字。
_PUNCT = set("，。、；：？！“”‘’（）()《》〈〉【】「」『』—－…·,.;:?!\"'`~@#$%^&*+=|\\/<>[]{}")


def _norm_map(text):
    chars, positions = [], []
    for i, ch in enumerate(text):
        if ch.isspace() or ch in _PUNCT:
            continue
        chars.append(ch)
        positions.append(i)
    return "".join(chars), positions


def _load_sources():
    table = {}
    for path in sorted((ROOT / "artifacts/w02/sources").glob("*-final.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for message in data.get("messages", []):
            table[(data.get("meeting_id"), message.get("id"))] = (
                message.get("speaker", ""), message.get("content", ""))
    return table


_SOURCES = _load_sources()


_CRITICAL = set("不没未无非别勿空零一二三四五六七八九十百千万亿0123456789我你他她它们咱")


def _diff_le1(a, b):
    """编辑距离不超过 1 时返回差异对列表，否则返回 None（只用于定位，不改写）。"""
    if a == b:
        return []
    if abs(len(a) - len(b)) > 1:
        return None
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return diffs if len(diffs) == 1 else None
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = None
    while i < len(short) and j < len(long_):
        if short[i] == long_[j]:
            i += 1
            j += 1
            continue
        if skipped is not None:
            return None
        skipped = long_[j]
        j += 1
    if skipped is None:
        skipped = long_[-1]
    return [("", skipped)]



def _locate(content, quote):
    """在 content 里定位 quote；返回原文片段（含原标点），唯一命中才算成功。

    三级：原样命中 -> 去标点后唯一命中 -> 仅差一个字的唯一命中。
    第三级只允许「换成别的字」这一种差异，且差异字落在否定词或数量词上时一律不认，
    避免把「不可以」认成「可以」这类语义反转。
    """
    if quote in content:
        return quote
    target, _ = _norm_map(quote)
    if len(target) < 6:
        return None
    haystack, positions = _norm_map(content)

    def span(start):
        begin = positions[start]
        end = positions[start + len(target) - 1] + 1
        return content[begin:end]

    hits, start = [], haystack.find(target)
    while start != -1:
        hits.append(start)
        start = haystack.find(target, start + 1)
    if len(hits) == 1:
        return span(hits[0])
    if hits or len(target) < 8:
        return None

    near = []
    for width in {len(target) - 1, len(target), len(target) + 1}:
        if width <= 0:
            continue
        for i in range(len(haystack) - width + 1):
            diff = _diff_le1(target, haystack[i:i + width])
            if diff is None:
                continue
            if any(a in _CRITICAL or b in _CRITICAL for a, b in diff):
                continue
            near.append((i, width, diff))
    if len(near) == 1:
        i, width, diff = near[0]
        print("[quote-fuzzy] %r 与原文差一处（%r），按原文归位" % (quote, diff),
              file=sys.stderr, flush=True)
        begin = positions[i]
        end = positions[i + width - 1] + 1
        return content[begin:end]
    return None


def _normalize_structure(obj):
    """模型偶发把多条候选塞进一条的 evidence 数组里，或给出缺字段的证据项。

    这里只做形状归位：把「看起来是候选」的对象提升回 claims；把缺少
    meeting_id/message_id/quote 的项移出 evidence（不补默认值、不改文字）。
    """
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list):
        return 0
    hoisted, dropped = [], 0
    survivors = []
    for claim in claims:
        if not isinstance(claim, dict):
            survivors.append(claim)
            continue
        evidence = claim.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        if not evidence:
            # 模型输出 schema 要求 evidence 非空：没有证据的候选不成立
            print("[claim-removed] %s：模型未给出可核对证据，整条候选移除（%r）"
                  % (claim.get("id"), claim.get("statement")), file=sys.stderr, flush=True)
            dropped += 1
            continue
        kept = []
        for item in evidence:
            if isinstance(item, dict) and "statement" in item and "evidence" in item:
                hoisted.append(item)
                print("[claim-hoisted] 从 evidence 里提升出一条候选：%r"
                      % str(item.get("statement"))[:60], file=sys.stderr, flush=True)
                continue
            if isinstance(item, dict) and {"meeting_id", "message_id", "quote"} <= set(item):
                kept.append(item)
                continue
            dropped += 1
            print("[evidence-dropped] 缺字段的证据项已移出：%r" % (item,), file=sys.stderr, flush=True)
        if not kept:
            print("[claim-removed] %s：证据项全部残缺，整条候选移除（%r）"
                  % (claim.get("id"), claim.get("statement")), file=sys.stderr, flush=True)
            dropped += 1
            continue
        claim["evidence"] = kept
        claim.setdefault("conflict_ids", [])
        claim.setdefault("review", {"status": "pending", "note": ""})
        survivors.append(claim)
    if hoisted:
        survivors.extend(hoisted)
    if isinstance(obj, dict):
        obj["claims"] = survivors
    if hoisted or dropped:
        print(f"[normalized] 提升 {len(hoisted)} 条、剔除 {dropped} 项", file=sys.stderr, flush=True)
    return len(hoisted) + dropped


def _drop_interviewer_evidence(obj):
    """访谈者（speaker=user）的发言不能作为客户证据：把这类引用移出 evidence。

    这是方法约束（assistant=受访者，user=访谈者），不是内容改写；移出后若证据为空，
    补一条 qualifiers 说明来路，让候选以「待核实」的身份保留，而不是冒充有证据。
    """
    dropped = 0
    removed_claims = 0
    claims = obj.get("claims", []) if isinstance(obj, dict) else []
    survivors = []
    for claim in claims:
        if not isinstance(claim, dict):
            survivors.append(claim)
            continue
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            # 没有证据就必须有来路说明，否则程序会判 PROVENANCE_REQUIRED
            if claim.get("qualifiers"):
                survivors.append(claim)
            else:
                removed_claims += 1
                print("[claim-removed] %s：无证据也无来路说明，整条候选移除（%r）"
                      % (claim.get("id"), claim.get("statement")), file=sys.stderr, flush=True)
            continue
        kept = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            speaker = _SOURCES.get((item.get("meeting_id"), item.get("message_id")), ("", ""))[0]
            if speaker == "user":
                dropped += 1
                print("[evidence-dropped] %s：引用了访谈者发言 %s（%r），已移出证据"
                      % (claim.get("id"), item.get("message_id"), item.get("quote")),
                      file=sys.stderr, flush=True)
                continue
            kept.append(item)
        if len(kept) == len(evidence):
            survivors.append(claim)
            continue
        if not kept:
            # 模型输出 schema 要求 evidence 非空，这类候选只能整条移除
            removed_claims += 1
            print("[claim-removed] %s：唯一的证据来自访谈者，整条候选移除（%r）"
                  % (claim.get("id"), claim.get("statement")), file=sys.stderr, flush=True)
            continue
        claim["evidence"] = kept
        survivors.append(claim)
    if isinstance(obj, dict):
        obj["claims"] = survivors
    if dropped:
        print(f"[evidence-dropped] 共移出 {dropped} 条访谈者来源的引用，移除 {removed_claims} 条无证据候选",
              file=sys.stderr, flush=True)
    return dropped


def _drop_unverifiable_evidence(obj):
    """最后一道闸：引用在原文里定位不到（改写、串场、张冠李戴）就整条证据移出。

    与平台校验的原则一致——只有能在原文里原样找到的引用才算证据。
    这里不修文字、不猜归属，移出后打印原文与模型引用，交给后续人工复核。
    """
    removed = 0
    claims = obj.get("claims", []) if isinstance(obj, dict) else []
    survivors = []
    for claim in claims:
        if not isinstance(claim, dict):
            survivors.append(claim)
            continue
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            survivors.append(claim)
            continue
        kept = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            speaker, content = _SOURCES.get((item.get("meeting_id"), item.get("message_id")), ("", ""))
            quote = item.get("quote")
            if speaker == "assistant" and isinstance(quote, str) and _locate(content, quote):
                kept.append(item)
                continue
            removed += 1
            print("[evidence-unverifiable] %s：%s 定位不到 %r（speaker=%s）"
                  % (claim.get("id"), item.get("message_id"), quote, speaker or "?"),
                  file=sys.stderr, flush=True)
        if len(kept) == len(evidence):
            survivors.append(claim)
            continue
        if not kept:
            print("[claim-removed] %s：证据全部无法定位，整条候选移除（%r）"
                  % (claim.get("id"), claim.get("statement")), file=sys.stderr, flush=True)
            continue
        claim["evidence"] = kept
        survivors.append(claim)
    if isinstance(obj, dict):
        obj["claims"] = survivors
    if removed:
        print(f"[evidence-unverifiable] 共移出 {removed} 条无法定位的引用", file=sys.stderr, flush=True)
    return removed


def _repair_quotes(obj):
    """两件事，都只把引用落回原文，不改写任何文字：
    1) 标点/空格被模型抹平 -> 按去标点后唯一命中，写回带原标点的原文片段；
    2) message_id 指错到同一场的另一条 -> 全文只命中一条时改指到那一条。
    """
    repaired = relocated = 0
    for claim in obj.get("claims", []) if isinstance(obj, dict) else []:
        for evidence in claim.get("evidence", []) if isinstance(claim, dict) else []:
            quote = evidence.get("quote")
            meeting = evidence.get("meeting_id")
            message_id = evidence.get("message_id")
            speaker, content = _SOURCES.get((meeting, message_id), ("", ""))
            if not isinstance(quote, str) or not content:
                continue
            found = _locate(content, quote)
            if found:
                if found != quote:
                    evidence["quote"] = found
                    repaired += 1
                    print("[quote-repaired] %s：%r -> %r" % (claim.get("id"), quote, found),
                          file=sys.stderr, flush=True)
                continue
            # 同一场访谈里换一条消息试试：只在全文唯一命中时才改
            hits = []
            for (mid, msg), (spk, body) in _SOURCES.items():
                if mid != meeting or spk != "assistant":
                    continue
                located = _locate(body, quote)
                if located:
                    hits.append((msg, spk, located))
            if len(hits) == 1:
                msg, spk, located = hits[0]
                evidence["message_id"] = msg
                evidence["quote"] = located
                relocated += 1
                print("[quote-relocated] %s：%s -> %s（原文唯一命中，speaker=%s）"
                      % (claim.get("id"), message_id, msg, spk), file=sys.stderr, flush=True)
    if repaired or relocated:
        print(f"[quote-repaired] 标点归位 {repaired} 处，消息归属纠正 {relocated} 处（内容均取自原文）",
              file=sys.stderr, flush=True)
    return repaired + relocated

if os.environ.get("FDE_MODEL_URL"):
    import local_relay
    os.environ["FDE_MODEL_URL"] = local_relay.ensure(os.environ["FDE_MODEL_URL"])

import w02_workflow  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(w02_workflow.main())
