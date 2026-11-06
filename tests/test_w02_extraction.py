"""Extraction boundaries, batching, trace association and human review."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'platform')]
from fde_product.interview_extraction import *
from tools.w02_interviews import convert,initialize


class Fixture:
    mode='fixture'
    def __init__(self,change=None): self.change,self.calls=change,[]
    def complete(self,messages,tools):
        data=parse_json(messages[-1]['content']);self.calls.append(deepcopy(data))
        if 'transcripts' in data:
            claims=[]
            for t in data['transcripts']:
                for m in t['messages']:
                    if m['speaker']!='assistant': continue
                    claims.append(dict(id=f'C{len(claims)+1}',statement=m['content'],kind='fact',evidence_status='reported',qualifiers=[],
                       evidence=[dict(meeting_id=t['meeting_id'],message_id=m['id'],quote=m['content'])],conflict_ids=[],review=dict(status='pending',note='')))
            result={'claims':claims}
        else:
            ids=[c['id'] for c in data['claims']]
            result={'conflicts':[dict(id='CONFLICT-001',claim_ids=ids[:2],question='虚构测试分歧，需核实。',next_step='回看原文。')] if len(ids)>=2 else []}
        if self.change: result=self.change(data,result)
        return {'role':'assistant','content':json.dumps(result,ensure_ascii=False),'reasoning_content':'PRIVATE_REASONING'},{}


@pytest.fixture
def sample(tmp_path):
    ws=Workspace(tmp_path)
    for name in ('front_desk.json','manager.json'):
        ws.write('sources/'+name,(ROOT/'examples/w02_extraction'/name).read_bytes(),raw=True)
    cfg=json.loads((ROOT/'examples/w02_extraction/extraction.json').read_text())
    cfg.update(sources=['sources/front_desk.json','sources/manager.json'],prompt='customer/w02/prompt.txt')
    ws.write('customer/w02/config.json',cfg)
    ws.write(cfg['prompt'],b'Extract the defined business statements.',raw=True)
    return ws,cfg


def run_sample(sample,model=None):
    ws,_=sample;path=extract(ws,'customer/w02/config.json',model or Fixture());run=ws.load(path)
    return ws,path,run


def test_end_to_end_candidates_stay_pending_and_source_bytes_survive(sample):
    ws,path,run=run_sample(sample)
    assert run['status']=='succeeded' and run['mode']=='fixture'
    assert validate_run(ws,path)==run
    facts=ws.verify(next(r for r in run['outputs'] if r['path'].endswith('/candidates.json')))
    assert all(c['review']=={'status':'pending','note':''} for c in facts['claims'])
    assert facts['conflicts']
    for original,ref in zip(sample[1]['sources'],run['inputs']): assert ws.raw(original)==ws.raw(ref['path'])
    assert 'PRIVATE_REASONING' not in ''.join(ws.raw(r['path']).decode() for r in run['outputs'])


def test_batching_keeps_each_message_once_and_reconciles_globally(sample):
    ws,cfg=sample;cfg['batch_chars']=1000
    # Two complete statements plus protocol context span separate batches.
    for i,path in enumerate(cfg['sources']):
        t=ws.load(path);t['messages']=[{'id':'MSG-1','speaker':'assistant','content':('虚构记录%d。'%i)*100}]
        ws.path(path).write_text(json.dumps(t,ensure_ascii=False))
    ws.path('customer/w02/config.json').write_text(json.dumps(cfg))
    model=Fixture();_,path,run=run_sample(sample,model)
    assert run['status']=='succeeded' and len(model.calls)==3
    sent=[(t['meeting_id'],m['id'],m['content']) for call in model.calls[:-1] for t in call['transcripts'] for m in t['messages']]
    assert len(sent)==len(set(sent))==2
    assert len(model.calls[-1]['claims'])==2
    validate_run(ws,path)


@pytest.mark.parametrize('change,code',[
    (lambda d,r:{'claims':[{**r['claims'][0],'review':{'status':'accepted','note':'auto'}}]},'MODEL_REVIEW_FORBIDDEN'),
    (lambda d,r:{'claims':[{**r['claims'][0],'evidence':[dict(meeting_id='missing',message_id='MSG-1',quote='x')]}]},'REFERENCE_INVALID'),
    (lambda d,r:{'claims':[{**r['claims'][0],'evidence':[{**r['claims'][0]['evidence'][0],'quote':'原文没有的句子'}]}]},'QUOTE_NOT_FOUND'),
    (lambda d,r:{'claims':[r['claims'][0],r['claims'][0]]},'DUPLICATE_ID'),
    (lambda d,r:{'claims':[{**r['claims'][0],'extra':'private'}]},'SCHEMA_INVALID'),
])
def test_bad_model_output_keeps_failure_and_does_not_reconcile(sample,change,code):
    model=Fixture(change);ws,path,run=run_sample(sample,model)
    assert run['status']=='failed' and run['error']['code']==code and len(model.calls)==1
    assert not any(r['path'].endswith('/candidates.json') for r in run['outputs'])
    assert any(r['path'].endswith('.response.json') for r in run['outputs'])
    assert validate_run(ws,path)['status']=='failed'


def test_non_json_and_provider_failure_are_retained(sample):
    class Broken(Fixture):
        def complete(self,*args): return {'role':'assistant','content':'not JSON'},{}
    ws,path,run=run_sample(sample,Broken());assert run['error']['code']=='JSON_INVALID'
    class Failed(Fixture):
        def complete(self,*args): raise ModelError('MODEL_TIMEOUT')
    _,other,failure=run_sample(sample,Failed());assert failure['error']['code']=='MODEL_TIMEOUT' and other!=path
    assert ws.load(path)==run


def test_cross_scope_duplicate_meeting_and_private_transcript_rejected(sample):
    ws,cfg=sample;t=ws.load(cfg['sources'][1])
    for mutation,code in [({'scope_id':'other'},'SCOPE_MISMATCH'),({'meeting_id':ws.load(cfg['sources'][0])['meeting_id']},'DUPLICATE_ID'),({'teacher_audit':{}},'SCHEMA_INVALID')]:
        ws.path(cfg['sources'][1]).write_text(json.dumps({**t,**mutation}))
        with pytest.raises(CheckError,match=code): load_config(ws,'customer/w02/config.json')


@pytest.mark.parametrize('path',['../outside.json','/tmp/outside.json','C:/private.json','a\\b.json',''])
def test_workspace_escape_rejected(sample,path):
    with pytest.raises(CheckError): sample[0].path(path)


def test_symlink_and_duplicate_json_keys_rejected(sample,tmp_path):
    ws,_=sample;ws.path('escape').symlink_to(tmp_path.parent)
    with pytest.raises(CheckError,match='PATH_ESCAPE'): ws.raw('escape/outside.json')
    ws.write('duplicate.json',b'{"a":1,"a":2}',raw=True)
    with pytest.raises(CheckError,match='DUPLICATE_KEY'): ws.load('duplicate.json')


def test_limits_do_not_silently_drop_messages(sample):
    ws,cfg=sample;transcripts=[ws.load(p) for p in cfg['sources']]
    transcripts[0]['messages'][0]['content']='x'*2000
    with pytest.raises(CheckError,match='MESSAGE_TOO_LARGE'): batches(transcripts,{**cfg,'batch_chars':1000})
    with pytest.raises(CheckError,match='BATCH_LIMIT'): batches(transcripts,{**cfg,'batch_chars':2300,'max_batches':1})


def test_review_starts_pending_and_accepted_cannot_hide_edits(sample):
    ws,path,_=run_sample(sample);draft=start_review(ws,path,'artifacts/w02/review-v1.json');facts=ws.load(draft)
    with pytest.raises(CheckError,match='REVIEW_REQUIRED'): validate_facts(ws,facts,reviewed=True,origin=True)
    for c in facts['claims']: c['review']={'status':'accepted','note':'虚构测试中的逐字核对，不代表业务已核实。'}
    validate_facts(ws,facts,reviewed=True,origin=True)
    facts['claims'][0]['statement']+='修改'
    with pytest.raises(CheckError,match='REVISION_NOTE_REQUIRED'): validate_facts(ws,facts,reviewed=True,origin=True)
    facts['claims'][0]['review']['status']='revised'
    validate_facts(ws,facts,reviewed=True,origin=True)
    with pytest.raises(CheckError,match='OUTPUT_EXISTS'): start_review(ws,path,draft)


def test_review_cannot_drop_initial_claim_or_hide_rejected_conflict(sample):
    ws,path,_=run_sample(sample);draft=start_review(ws,path,'review.json');facts=ws.load(draft)
    for c in facts['claims']: c['review']={'status':'accepted','note':'test'}
    facts['claims'][-1]['review']['status']='rejected'
    if facts['claims'][-1]['conflict_ids']:
        with pytest.raises(CheckError,match='CONFLICT_INVALID'): validate_facts(ws,facts,reviewed=True,origin=True)
    else:
        facts['claims'].pop()
        with pytest.raises(CheckError,match='CLAIM_HISTORY_REQUIRED'): validate_facts(ws,facts,reviewed=True,origin=True)


def test_run_detects_changed_candidate_even_with_updated_hash(sample):
    ws,path,run=run_sample(sample);ref=next(r for r in run['outputs'] if r['path'].endswith('/candidates.json'))
    data=ws.verify(ref);data['claims'][0]['statement']='篡改输出'
    ws.path(ref['path']).write_text(json.dumps(data));ref.update(ws.ref(ref['path']))
    ws.path(path).write_text(json.dumps(run))
    with pytest.raises(CheckError,match='EXTRACTION_MISMATCH'): validate_run(ws,path)


def test_run_detects_request_not_corresponding_to_inputs(sample):
    ws,path,run=run_sample(sample);ref=next(r for r in run['outputs'] if r['path'].endswith('.request.json'))
    data=ws.verify(ref);data['messages'][-1]['content']='{}';ws.path(ref['path']).write_text(json.dumps(data))
    with pytest.raises(CheckError,match='HASH_MISMATCH'): validate_run(ws,path)


def test_evidence_exposes_role_week_original_message_for_review(sample):
    ws,path,_=run_sample(sample);draft=start_review(ws,path,'review.json');view=evidence_view(ws,draft)
    assert all('role_id' in e and 'week' in e and 'message' in e for row in view for e in row['source_messages'])


def test_init_is_non_destructive_and_requires_learner_sources(tmp_path):
    ws=Workspace(tmp_path);initialize(ws)
    with pytest.raises(CheckError,match='OUTPUT_EXISTS'): initialize(ws)
    with pytest.raises(CheckError,match='SCHEMA_INVALID'): load_config(ws,'customer/w02/extraction.json')


def test_legacy_public_conversion_keeps_w1_and_rejects_teacher_metadata(sample):
    ws,_=sample
    source={'meeting_id':'old-w1','week':1,'role_id':'ROLE-SHEN','scenario_version':'w1','state':'active','purpose':'test','mode':'live',
       'messages':[{'id':'MSG-1','speaker':'assistant','content':'原话。'},{'id':'MSG-2','speaker':'system','content':'系统消息。'}]}
    ws.write('public.json',source);convert(ws,'public.json','normalized.json')
    result=ws.load('normalized.json');assert result['week']==1 and result['messages']==source['messages'][:1]
    with pytest.raises(CheckError,match='OUTPUT_EXISTS'): convert(ws,'public.json','normalized.json')
    source['messages'][0]['metadata']={'hidden':True};ws.write('teacher.json',source)
    with pytest.raises(CheckError,match='PUBLIC_EXPORT_REQUIRED'): convert(ws,'teacher.json','rejected.json')


def test_live_adapter_records_parameters_but_not_secrets_or_reasoning(sample):
    ws,cfg=sample;cfg['profile']='deepseek-json';ws.path('customer/w02/config.json').write_text(json.dumps(cfg))
    class LocalLive(ExtractionModel):
        def complete(self,messages,tools):
            self._payload(messages,tools)
            return Fixture().complete(messages,tools)
    model=LocalLive(url='https://example.invalid/chat',key='PRIVATE_TEST_KEY',model='test',profile='deepseek-json')
    ws,path,run=run_sample(sample,model);assert run['status']=='succeeded' and run['mode']=='live'
    validate_run(ws,path)
    request=ws.verify(run['outputs'][0]);assert request['thinking']=={'type':'enabled'}
    assert 'PRIVATE_TEST_KEY' not in ''.join(ws.raw(r['path']).decode() for r in run['outputs'])


def test_empty_response_has_specific_error(sample):
    class Empty(Fixture):
        def complete(self,*args): return {'role':'assistant','content':''},{'completion_tokens':8192}
    _,_,run=run_sample(sample,Empty())
    assert run['error']['code']=='MODEL_CONTENT_EMPTY'


def test_invented_conflict_reference_is_rejected(sample):
    def invalid(data,result):
        if 'claims' in data:
            result['conflicts'][0]['claim_ids'][0]='missing'
        return result
    model=Fixture(invalid);_,_,run=run_sample(sample,model)
    assert run['error']['code']=='CONFLICT_INVALID' and len(model.calls)==2


def test_source_and_prompt_edits_after_call_do_not_rewrite_old_evidence(sample):
    ws,cfg=sample
    class Editing(Fixture):
        def complete(self,messages,tools):
            result=super().complete(messages,tools)
            ws.path(cfg['prompt']).write_text('CHANGED_AFTER_SNAPSHOT')
            return result
    ws,path,run=run_sample(sample,Editing())
    assert run['status']=='succeeded'
    ws.path(cfg['sources'][0]).write_text('{}')
    validate_run(ws,path)


def test_request_profile_cannot_be_changed_without_detection(sample):
    ws,path,run=run_sample(sample)
    ref=next(r for r in run['outputs'] if r['path'].endswith('.request.json'));old=deepcopy(ref)
    request=ws.verify(ref);request['temperature']=100;ws.path(ref['path']).write_text(json.dumps(request));ref.update(ws.ref(ref['path']))
    steps_ref=next(r for r in run['outputs'] if r['path'].endswith('/steps.json'))
    steps=ws.verify(steps_ref)
    for step in steps:
        if step['request']==old: step['request']=ref
    ws.path(steps_ref['path']).write_text(json.dumps(steps));steps_ref.update(ws.ref(steps_ref['path']))
    ws.path(path).write_text(json.dumps(run))
    with pytest.raises(CheckError,match='MODEL_CONFIGURATION_MISMATCH'): validate_run(ws,path)


def test_reconcile_budget_failures_preserve_completed_extraction(sample):
    ws,cfg=sample;cfg['reconcile_chars']=1000;ws.path('customer/w02/config.json').write_text(json.dumps(cfg))
    model=Fixture();_,_,run=run_sample(sample,model)
    assert run['status']=='failed' and run['error']['code']=='RECONCILE_LIMIT' and len(model.calls)==1




def test_json_output_protocol_is_requested_at_both_stages(sample):
    _,config=sample
    model=ExtractionModel(url="https://example.invalid/chat",key="test",model="test",profile="gateway-json",extraction_thinking="disabled")
    for stage in ("extract","conflicts"):
        messages=model_messages(stage,[],config,"test")
        model.phase=stage
        payload=model._payload(messages,[])
        assert payload["response_format"]=={"type":"json_object"}
        assert "thinking" not in payload
        assert payload["extra_body"]["thinking"]=={"type":"disabled" if stage=="extract" else "enabled"}
        assert payload["reasoning_effort"]=="low"
        assert "json" in " ".join(m["content"] for m in messages).lower()


# Historical evidence must remain valid after the installed prompts change.
def test_saved_prompts_survive_installed_prompt_updates(sample, monkeypatch):
    import fde_product.interview_extraction as extraction
    ws, path, run = run_sample(sample)
    before = {r["path"]: ws.raw(r["path"]) for group in
              ("implementation", "configuration", "inputs", "outputs") for r in run[group]}
    monkeypatch.setattr(extraction, "GUARD", "New extraction instruction")
    monkeypatch.setattr(extraction, "CONFLICT_PROMPT", "New conflict instruction")
    assert validate_run(ws, path) == run
    assert all(ws.raw(p) == data for p, data in before.items())


def rewrite_reference(ws, path, run, old, data):
    """Simulate a changed artifact whose manifest hashes were also updated."""
    ws.path(old["path"]).write_text(data if isinstance(data, str) else json.dumps(data))
    new = ws.ref(old["path"])
    for step_ref in [r for r in run["outputs"] if r["path"].endswith("/steps.json")]:
        steps = ws.verify(step_ref)
        for step in steps:
            for field in ("request", "response"):
                if step[field] == old:
                    step[field] = new
        ws.path(step_ref["path"]).write_text(json.dumps(steps))
        step_ref.update(ws.ref(step_ref["path"]))
    for group in ("implementation", "configuration", "inputs", "outputs"):
        run[group] = [new if r == old else r for r in run[group]]
    ws.path(path).write_text(json.dumps(run))


@pytest.mark.parametrize("phase", ["extract", "conflicts"])
def test_rehashed_system_prompt_must_match_saved_implementation(sample, phase):
    ws, path, run = run_sample(sample)
    steps = ws.verify(next(r for r in run["outputs"] if r["path"].endswith("/steps.json")))
    ref = next(step["request"] for step in steps if step["phase"] == phase)
    request = ws.verify(ref)
    request["messages"][0]["content"] += "Changed system instruction"
    rewrite_reference(ws, path, run, ref, request)
    with pytest.raises(CheckError, match="EXTRACTION_INPUT_MISMATCH"):
        validate_run(ws, path)


@pytest.mark.parametrize("change", ["hash", "literal", "expression", "missing", "duplicate", "syntax", "adapter"])
def test_changed_implementation_is_checked_without_executing_it(sample, change):
    ws, path, run = run_sample(sample)
    ref = next(r for r in run["implementation"] if r["path"].endswith("/interview_extraction.py"))
    source = ws.raw(ref["path"]).decode()
    if change == "hash":
        ws.path(ref["path"]).write_text(source + "# Changed without updating hash\n")
        code = "HASH_MISMATCH"
    else:
        source = {"literal": source.replace(GUARD, "Changed saved extraction instruction"),
                  "expression": source.replace('GUARD = """', 'GUARD = __import__("builtins").exec("raise RuntimeError(\"MUST_NOT_EXECUTE\")") + """', 1),
                  "missing": source.replace('GUARD = """', 'OLD_GUARD = """', 1),
                  "duplicate": source + '\nGUARD = "duplicate"\n',
                  "syntax": source + '\ninvalid python :\n',
                  "adapter": source.replace('ADAPTER = "'+ADAPTER+'"', 'ADAPTER = "unsupported"')}[change]
        rewrite_reference(ws, path, run, ref, source)
        code = "EXTRACTION_INPUT_MISMATCH" if change == "literal" else (
            "ADAPTER_VERSION_UNSUPPORTED" if change == "adapter" else "IMPLEMENTATION_SNAPSHOT_INVALID")
    with pytest.raises(CheckError, match=code):
        validate_run(ws, path)


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_implementation_reference_must_be_unique(sample, change):
    ws, path, run = run_sample(sample)
    ref = next(r for r in run["implementation"] if r["path"].endswith("/interview_extraction.py"))
    if change == "missing": run["implementation"].remove(ref)
    else: run["implementation"].append(ref.copy())
    ws.path(path).write_text(json.dumps(run))
    with pytest.raises(CheckError, match="RUN_REFERENCE_INVALID"):
        validate_run(ws, path)


def test_stage_policy_resets_after_failure_and_does_not_disable_knowledge():
    from unittest.mock import patch
    from fde_product.models import ModelError
    model=ExtractionModel(url='https://example.invalid/chat',key='test',model='test',profile='gateway-json',extraction_thinking='disabled')
    with patch.object(model,'_send',side_effect=TimeoutError()):
        with pytest.raises(ModelError,match='MODEL_TIMEOUT'):model.complete_phase('extract',[],[])
    assert model.last_call['attempts'][0]['request']['extra_body']['thinking']=={'type':'disabled'}
    assert model.phase is None
    assert model._payload([],[])['extra_body']['thinking']=={'type':'enabled'}
    assert model.policy_config()['extraction_thinking']=='disabled'


def test_old_live_records_keep_top_level_enabled_policy(sample):
    ws,cfg=sample;cfg['profile']='gateway-json';ws.path('customer/w02/config.json').write_text(json.dumps(cfg))
    class HistoricalLive(ExtractionModel):
        def policy_config(self):
            p=super().policy_config();p.pop('wire_format');p.pop('extraction_thinking');p.pop('gateway_cache_bypass');return p
        def complete(self,messages,tools):
            self._payload(messages,tools);return Fixture().complete(messages,tools)
    model=HistoricalLive(url='https://example.invalid/chat',key='test',model='test',profile='gateway-json',
                         wire_format='legacy-top-level',extraction_thinking='enabled',gateway_cache_bypass=False)
    ws,path,run=run_sample(sample,model)
    assert run['status']=='succeeded'
    validate_run(ws,path)
    settings=ws.verify(next(r for r in run['configuration'] if r['path'].endswith('/settings.json')))
    assert 'wire_format' not in settings['model_policy']
    for ref in run['outputs']:
        if ref['path'].endswith('.request.json'):
            req=ws.verify(ref);assert req['thinking']=={'type':'enabled'} and 'extra_body' not in req
