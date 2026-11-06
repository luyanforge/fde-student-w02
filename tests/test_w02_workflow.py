from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"platform")]
from fde_product.extraction_workflow import *
from fde_product.workflow_report import render_report

class ExtractFixture:
    mode="fixture"
    def complete(self,messages,tools):
        payload=parse_json(messages[-1]["content"])
        if "transcripts" in payload:
            claims=[]
            for t in payload["transcripts"]:
                for m in t["messages"]:
                    if m["speaker"]=="assistant":
                        claims.append({"id":f"c{len(claims)}","statement":m["content"],"kind":"fact","evidence_status":"reported","qualifiers":[],"evidence":[{"meeting_id":t["meeting_id"],"message_id":m["id"],"quote":m["content"]}],"conflict_ids":[],"review":{"status":"pending","note":""}})
            result={"claims":claims}
        else:result={"conflicts":[]}
        return {"role":"assistant","content":json.dumps(result,ensure_ascii=False),"reasoning_content":"PRIVATE_TEST_REASONING"},{}

class NativeFixture:
    mode="fixture"
    def __init__(self,turns):self.turns=iter(turns)
    def complete(self,messages,tools):
        t=next(self.turns)
        if isinstance(t,Exception):raise t
        return {"role":"assistant","content":None,"reasoning_content":"PRIVATE_TEST_REASONING","tool_calls":[{"id":"call-"+uuid4().hex,"type":"function","function":{"name":t[0],"arguments":json.dumps(t[1],ensure_ascii=False)}}]},{}

@pytest.fixture
def sample(tmp_path):
    ws=Workspace(tmp_path)
    initialize(ws)
    for name in ["front_desk.json","manager.json"]:
        ws.write("sources/"+name,(ROOT/"examples/w02_extraction"/name).read_bytes(),raw=True)
    cfg=ws.load("customer/w02/extraction.json");cfg.update(scope_id="fictional-library",sources=["sources/front_desk.json","sources/manager.json"],focus_questions=["定义、保留期与未知。"])
    save(ws,"customer/w02/extraction.json",cfg)
    task=ws.load(DEFAULT_TASK);task["goal"]="明确借阅与预约规则。";save(ws,DEFAULT_TASK,task)
    return ws

def first(ws):
    p=start(ws)
    s=advance(ws,p,NativeFixture([("inspect_materials",{}),("extract_candidates",{}) ,("show_evidence",{"claim_ids":["CLM-001-0001","CLM-001-0002","CLM-001-0003"]}),("request_review",{"claim_ids":["CLM-001-0001","CLM-001-0002"],"question":"请核对前台原文及未知。"})]),ExtractFixture())
    return p,s

def proposal(ws):
    p,s=first(ws)
    assert s["status"]=="awaiting_review",s
    review(ws,p,"采用定义，自动提醒保留未知。")
    s=advance(ws,p,NativeFixture([("request_review",{"claim_ids":["CLM-001-0003"],"question":"核对馆长说法，不裁决期限。"})]),ExtractFixture())
    review(ws,p,"保留馆长理解与待核条件。")
    args={"business_model":"# 当前模型\n借阅关联读者和副本；保留期限待核。\n","notes_update":"两位角色的说法保留，来源见本轮原文。","next_questions":["核对制度和前台操作依据。"],"claim_ids":["CLM-001-0001","CLM-001-0002","CLM-001-0003"],"summary":"补充关系并保留未知。"}
    s=advance(ws,p,NativeFixture([("propose_update",args)]),ExtractFixture())
    assert s["status"]=="awaiting_apply",s
    return p,s

def test_native_calls_pause_resume_and_only_human_apply_writes(sample):
    ws=sample;before=ws.raw("docs/w02/business_model.md");p,s=proposal(ws)
    assert ws.raw("docs/w02/business_model.md")==before
    assert len(s["human_reviews"])==2
    assert [x["name"] for x in s["tool_events"]]==["inspect_materials","extract_candidates","show_evidence","request_review","request_review","propose_update"]
    done=apply(ws,p);assert done["status"]=="completed"
    final=ws.raw("docs/w02/interview_notes.md")
    assert "自动提醒保留未知" in final.decode() and "下一轮起点" in final.decode()
    apply(ws,p);assert ws.raw("docs/w02/interview_notes.md")==final
    assert ws.load(s["extraction_run"])["status"]=="succeeded"

def test_snapshot_and_next_round_use_confirmed_current_documents(sample):
    ws=sample;p,s=proposal(ws);apply(ws,p)
    cfg=ws.load(DEFAULT_TASK);cfg["goal"]="继续核对剩余问题。";save(ws,DEFAULT_TASK,cfg)
    q=start(ws,previous=p);n=load_session(ws,q)
    assert n["previous"]==p and n["base_hashes"][cfg["business_model"]]==digest(ws.raw(cfg["business_model"]))
    assert "核对制度" in n["messages"][1]["content"]
    assert text(ws,s["snapshots"]["business_model"]["path"])!=text(ws,n["snapshots"]["business_model"]["path"])

def test_concurrent_manual_edit_stops_apply_without_changing_either_document(sample):
    ws=sample;p,s=proposal(ws);ws.path("docs/w02/business_model.md").write_text("人工已更新")
    before=ws.raw("docs/w02/interview_notes.md")
    with pytest.raises(CheckError,match="MODEL_CHANGED_SINCE_START"):apply(ws,p)
    assert ws.raw("docs/w02/business_model.md").decode()=="人工已更新" and ws.raw("docs/w02/interview_notes.md")==before

def test_model_cannot_approve_or_propose_before_review(sample):
    ws=sample;p,s=first(ws)
    with pytest.raises(CheckError,match="NO_PENDING_PROPOSAL"):apply(ws,p)
    with pytest.raises(CheckError,match="HUMAN_ACTION_REQUIRED"):advance(ws,p,NativeFixture([]),ExtractFixture())
    assert "apply" not in [x[0] for x in specs()]

def test_source_and_skill_changes_after_start_do_not_change_inputs(sample):
    ws=sample;p=start(ws);s=load_session(ws,p)
    ws.path("sources/front_desk.json").write_text("changed")
    ws.path(s["task"]["skill"]).write_text("changed")
    s=advance(ws,p,NativeFixture([("inspect_materials",{}),("extract_candidates",{}),("show_evidence",{"claim_ids":["CLM-001-0001"]}),("request_review",{"claim_ids":["CLM-001-0001"],"question":"核对原文"})]),ExtractFixture())
    assert s["status"]=="awaiting_review"
    assert "changed" not in json.dumps(s["tool_events"][0]["result"])

def test_tampered_proposal_cannot_be_written(sample):
    ws=sample;p,s=proposal(ws);ws.path(s["folder"]+"/proposal.json").write_text('{}')
    with pytest.raises(CheckError,match="PROPOSAL_CHANGED"):apply(ws,p)

def test_failed_request_is_retained_and_no_previous_success_reused(sample):
    ws=sample;p=start(ws);s=advance(ws,p,NativeFixture([ModelError("MODEL_TIMEOUT")]),ExtractFixture())
    assert s["status"]=="failed" and s["error"]=="MODEL_TIMEOUT"
    assert s["calls"][0]["request"] and not s["calls"][0]["response"]
    assert not s["extraction_run"]

def test_private_reasoning_absent_and_real_report_escapes_data(sample):
    ws=sample;p,s=proposal(ws)
    alltext=''.join(f.read_text() for f in ws.path(s["folder"]).rglob('*.json'))
    assert "PRIVATE_TEST_REASONING" not in alltext
    report=render_report(ws,p).read_text()
    assert "假设" not in report and "机制测试／固定回复" in report
    assert "自动提醒保留未知" in report and "本次运行的源码快照" in report

def test_gateway_workflow_protocol_enables_low_thinking():
    model=WorkflowModel(url="https://example.invalid/chat",key="test",model="test",profile="gateway-json")
    payload=model._payload([{"role":"user","content":"call a tool"}],schemas())
    assert "thinking" not in payload
    assert payload["extra_body"]["thinking"]=={"type":"enabled"}
    assert payload["max_tokens"]==8192 and "temperature" not in payload
    assert payload["reasoning_effort"]=="low"
    assert "response_format" not in payload

def test_workspace_lock_excludes_concurrent_writers(sample):
    with lock(sample):
        with pytest.raises(CheckError,match="WORKSPACE_BUSY"):
            with lock(sample): pass

def test_initialization_preserves_existing_documents(sample):
    before=sample.raw("docs/w02/business_model.md")
    assert not initialize(sample)["created"]
    assert sample.raw("docs/w02/business_model.md")==before

def test_cannot_start_from_unfinished_round(sample):
    p=start(sample)
    with pytest.raises(CheckError,match="PREVIOUS_ROUND_INCOMPLETE"): start(sample,previous=p)

def test_scope_mismatch_and_path_escape_rejected(sample):
    cfg=sample.load("customer/w02/extraction.json");cfg["scope_id"]="another";save(sample,"customer/w02/extraction.json",cfg)
    with pytest.raises(CheckError,match="SCOPE_MISMATCH"):start(sample)
    with pytest.raises(CheckError,match="PATH_INVALID"):sample.path('../outside')


def test_revision_keeps_old_draft_and_requires_new_confirmation(sample):
    ws=sample;p,s=proposal(ws);old=ws.raw(s["folder"]+"/proposal.json")
    revise(ws,p,"请在模型里明确提醒可用性仍未知。")
    args={k:s["proposal"][k] for k in ("business_model","notes_update","next_questions","claim_ids","summary")}
    args["business_model"]+="自动提醒是否可用仍未知。"
    updated=advance(ws,p,NativeFixture([("propose_update",args)]),ExtractFixture())
    assert updated["status"]=="awaiting_apply" and updated["proposal_revision"]==2
    assert ws.raw(s["folder"]+"/proposal.json")==old
    assert "自动提醒" not in ws.raw("docs/w02/business_model.md").decode()
    apply(ws,p);assert "自动提醒" in ws.raw("docs/w02/business_model.md").decode()

def test_unadopted_candidates_can_remain_deferred(sample):
    ws=sample;p,s=first(ws);review(ws,p,"只采用第一条，第二条暂不采用，其他留待后续。")
    args={"business_model":"# 模型\n借阅关联读者与副本。","notes_update":"采用第一条关系陈述。","next_questions":["继续核对规则。"],"claim_ids":["CLM-001-0001"],"summary":"本轮仅补充关系。"}
    s=advance(ws,p,NativeFixture([("propose_update",args)]),ExtractFixture())
    assert s["status"]=="awaiting_apply" and len(s["proposal"]["deferred_claim_ids"])==2
    apply(ws,p)
    assert "未纳入本轮模型草稿" in ws.raw("docs/w02/interview_notes.md").decode()

def test_tool_reasoning_is_retained_privately_for_next_call(sample):
    from copy import deepcopy
    import stat
    class Capture(NativeFixture):
        def __init__(self):
            super().__init__([('inspect_materials',{}),ModelError('MODEL_TIMEOUT')]);self.seen=[]
        def complete(self,messages,tools):
            self.seen.append(deepcopy(messages));return super().complete(messages,tools)
    ws=sample;p=start(ws);model=Capture();s=advance(ws,p,model,ExtractFixture())
    assert s['status']=='failed' and len(model.seen)==2
    assert any(m.get('reasoning_content')=='PRIVATE_TEST_REASONING' for m in model.seen[1])
    private=list(ws.path('.runtime/w02-workflow').glob('*.json'))
    assert len(private)==1 and stat.S_IMODE(private[0].stat().st_mode)==0o600
    assert 'PRIVATE_TEST_REASONING' not in ''.join(f.read_text() for f in ws.path(s['folder']).rglob('*.json'))

def test_previous_latest_skips_failed_later_round(sample):
    ws=sample;p,_=proposal(ws);apply(ws,p)
    q=start(ws,previous=resolve_previous(ws,"latest"))
    s=advance(ws,q,NativeFixture([ModelError("MODEL_TIMEOUT")]),ExtractFixture());assert s["status"]=="failed"
    assert resolve_session(ws,"latest")==q and resolve_previous(ws,"latest")==p
    assert load_session(ws,start(ws,previous=resolve_previous(ws,"latest")))["previous"]==p

def test_previous_latest_without_completed_pointer_keeps_old_behaviour(sample):
    p=start(sample)
    assert resolve_previous(sample,"latest")==p and resolve_previous(sample,p)==p
    with pytest.raises(CheckError,match="PREVIOUS_ROUND_INCOMPLETE"): start(sample,previous=resolve_previous(sample,"latest"))

class InterruptingExtractor(ExtractFixture):
    def complete(self,messages,tools):raise KeyboardInterrupt

def test_interrupt_inside_tool_marks_session_failed(sample):
    ws=sample;p=start(ws)
    with pytest.raises(KeyboardInterrupt):
        advance(ws,p,NativeFixture([("inspect_materials",{}),("extract_candidates",{})]),InterruptingExtractor())
    s=load_session(ws,p)
    assert s["status"]=="failed" and s["error"]=="SESSION_INTERRUPTED" and s["messages"][-1].get("tool_calls")
    assert "start" in public_status(s,p)["next"]
    with pytest.raises(CheckError,match="HUMAN_ACTION_REQUIRED"): advance(ws,p,NativeFixture([]),ExtractFixture())

def test_interrupt_during_model_call_keeps_session_resumable(sample):
    ws=sample;p=start(ws)
    with pytest.raises(KeyboardInterrupt): advance(ws,p,InterruptingExtractor(),ExtractFixture())
    s=load_session(ws,p);assert s["status"]=="active" and not interrupted(s)
