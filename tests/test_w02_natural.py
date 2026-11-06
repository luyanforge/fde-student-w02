"""Synthetic mechanism tests only; local live adapters make no network requests."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'platform'),str(ROOT/'tools')]
from fde_product.agent import Agent
from fde_product.models import ScriptedModel
from fde_product.natural_knowledge import *
from fde_product.interview_extraction import extract, ExtractionModel
from fde_product.w02_natural_checker import precheck
from test_w02_extraction import Fixture

class LocalExtraction(ExtractionModel):
    def complete(self,messages,tools):
        self._payload(messages,tools)
        return Fixture().complete(messages,tools)

class LocalContext(ContextModel):
    def complete(self,messages,tools):
        self._payload(messages,tools)
        return {'role':'assistant','content':json.dumps({'answer':'合成测试回答，语义仍待人工判断。'}),'reasoning_content':'PRIVATE_REASONING'},{}

def put(ws,path,value):
    p=ws.path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
    return ws.ref(path)

@pytest.fixture
def ws(tmp_path):
    w=Workspace(tmp_path)
    for i,role in enumerate(('ROLE-ZHOU','ROLE-LIN','ROLE-CHEN')):
        put(w,f'sources/{i}.json',{'contract_version':'w02-0.1.0-draft','scope_id':'qinghe','origin':'live',
            'meeting_id':f'SYNTHETIC-{i}','week':2,'role_id':role,'scenario_version':'synthetic-test',
            'state':'ended','purpose':'虚构测试，不是真实访谈或青禾答案。','messages':[
            {'id':'U','speaker':'user','content':'合成测试问题？'},
            {'id':'A','speaker':'assistant','content':f'测试说法 {i}，需要核实。'}]})
    config=json.loads((ROOT/'templates/w02_extraction/extraction.json').read_text())
    config.update(scope_id='qinghe',sources=[f'sources/{i}.json' for i in range(3)],prompt='customer/w02/extraction_prompt.txt',profile='gateway-json')
    put(w,'customer/w02/extraction.json',config);put(w,config['prompt'],'提取原文支持的信息。')
    model=LocalExtraction(url='https://example.invalid/chat',key='FAKE_TEST_KEY',model='synthetic',profile=config['profile'])
    path=extract(w,'customer/w02/extraction.json',model)
    assert w.load(path)['status']=='succeeded'
    initialize(w,path)
    put(w,'docs/w02/business_model.md','合成业务模型：每次借阅恰好关联一位读者和一本副本；同时借阅上限未知。')
    put(w,'docs/w02/interview_notes.md','合成记录，人工核查与真实语义不由测试证明。')
    put(w,'artifacts/w01/product_fit_gap.json',{'contract_version':'w01-0.2.1','product_version':'0.2.0-w1',
        'capability_matrix_version':'w1-capabilities-0.2.0','items':[{'id':'GAP-001','request':'合成需求','category':'待判断',
        'capability_ids':[],'evidence_status':'unknown','evidence':[],'next_step':'待核实'}]})
    return w

def call(ws,model=None,**kwargs):
    a=Agent(ws.path('.runtime/test'),model or LocalContext(url='https://example.invalid/chat',key='FAKE_TEST_KEY',model='synthetic',profile='gateway-json'))
    try:return a.answer_business_model(ws.root,CONFIG,'一次借阅如何关联？',**kwargs)
    finally:a.close()

def pair(ws):
    reader=SnapshotReader(ws.root)
    return [call(ws,include_knowledge=x,pair_id='PAIR-SYNTHETIC',reader=reader,expected='DO_NOT_SEND_EXPECTED') for x in (False,True)]

def test_init_keeps_documents_and_no_yaml_required(ws):
    assert not list(ws.root.rglob('*.yaml'))
    old=ws.raw('docs/w02/business_model.md')
    with pytest.raises(CheckError,match='OUTPUT_EXISTS'):initialize(ws)
    assert ws.raw('docs/w02/business_model.md')==old
    cfg,_,_,_,facts=load_task(ws)
    assert all(c['review']['status']=='pending' for c in facts['claims'])
    ws.path(CONFIG).unlink();initialize(ws,cfg['extraction_run'])
    assert ws.raw('docs/w02/business_model.md')==old

def test_same_snapshot_request_record_and_private_fields(ws):
    results=pair(ws)
    for included,result in zip((False,True),results):
        run,settings,_,_=check_run(ws,result['run'])
        request=ws.verify(run['request']);data=json.loads(request['messages'][1]['content'])
        assert bool(data['business_model'])==included and data['question']=='一次借阅如何关联？'
        assert not request.get('tools') and 'DO_NOT_SEND_EXPECTED' not in json.dumps(request)
        public=json.dumps(ws.verify(run['response']));assert 'PRIVATE_REASONING' not in public and 'FAKE_TEST_KEY' not in public
        assert run['semantic_review']=='needs_review'
    report=precheck(ws.root)
    assert report['status']=='ready_for_review',report['issues']
    assert report['coverage']['comparison_pairs']==1 and report['final_gate']=='not_determined'
    assert len(report['review_queue'])==9 and all(r['status']=='pending' for r in report['review_queue'])

def test_new_model_requires_new_comparison_and_old_snapshot_survives(ws):
    results=pair(ws);put(ws,'docs/w02/business_model.md','修改后的合成模型。')
    assert all(check_run(ws,r['run'])[0]['status']=='succeeded' for r in results)
    assert precheck(ws.root)['status']=='needs_revision'
    pair(ws);assert precheck(ws.root)['status']=='ready_for_review'

def test_unpaired_debug_does_not_block_good_comparison(ws):
    call(ws);pair(ws)
    report=precheck(ws.root);assert report['status']=='ready_for_review',report['issues']
    assert any(r['reason']=='single_debug_run' for r in report['runtime'])

@pytest.mark.parametrize('reply,code',[
 ({'role':'assistant','content':'not JSON'},'JSON_INVALID'),
 ({'role':'assistant','content':''},'MODEL_CONTENT_EMPTY'),
 ({'role':'assistant','content':'{"answer":"x"}','tool_calls':[{'id':'T','type':'function','function':{'name':'send','arguments':'{}'}}]},'KNOWLEDGE_TOOL_FORBIDDEN'),
 ('MODEL_TIMEOUT','MODEL_TIMEOUT')])
def test_failed_requests_retained_and_never_count(ws,reply,code):
    result=call(ws,ScriptedModel([reply]));run,*_=check_run(ws,result['run'])
    assert result['status']=='failed' and run['error']['code']==code
    assert precheck(ws.root)['status']=='needs_revision'

def test_rehashed_request_tampering_detected(ws):
    result=call(ws);run=ws.load(result['run']);old=run['request'].copy()
    request=ws.verify(old);request['messages'][1]['content']='{}'
    new=put(ws,old['path'],request);run['request']=new
    run['outputs']=[new if r==old else r for r in run['outputs']];put(ws,result['run'],run)
    with pytest.raises(CheckError,match='CONTEXT_INPUT_MISMATCH'):check_run(ws,result['run'])

def test_partial_pair_or_changed_question_does_not_count(ws):
    call(ws,pair_id='PAIR-X',include_knowledge=True)
    assert precheck(ws.root)['status']=='needs_revision'
    a=Agent(ws.path('.runtime/test'),LocalContext(url='https://example.invalid/chat',key='x',model='synthetic',profile='gateway-json'))
    try:a.answer_business_model(ws.root,CONFIG,'不同的问题',include_knowledge=False,pair_id='PAIR-X')
    finally:a.close()
    assert precheck(ws.root)['status']=='needs_revision'

def test_limits_and_missing_documents_fail_before_call(ws):
    put(ws,'docs/w02/business_model.md','大'*200001)
    with pytest.raises(CheckError,match='CONTEXT_LIMIT'):call(ws)
    ws.path('docs/w02/business_model.md').unlink()
    with pytest.raises(CheckError,match='SOURCE_NOT_FOUND'):load_task(ws)

def test_direct_cli_external_workspace_and_non_overwrite(ws):
    pair(ws)
    command=[sys.executable,str(ROOT/'tools/check_w02.py'),'--workspace',str(ws.root),'--output','report.json']
    result=subprocess.run(command,cwd=ws.root,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    before=ws.raw('report.json')
    again=subprocess.run(command,cwd=ws.root,capture_output=True,text=True)
    # The rerun still shows the full result; only saving is refused, and the old report is kept.
    assert again.returncode==2 and json.loads(again.stdout)['status']=='ready_for_review'
    assert json.loads(again.stderr)['code']=='OUTPUT_EXISTS' and ws.raw('report.json')==before
    inspect=subprocess.run([sys.executable,str(ROOT/'tools/w02_knowledge.py'),'--workspace',str(ws.root),'inspect'],cwd=ws.root,capture_output=True,text=True)
    assert inspect.returncode==0,inspect.stdout+inspect.stderr
    assert json.loads(inspect.stdout)['planned_model_calls']==0



def test_knowledge_init_and_comparison_reuse_historical_extraction(ws, monkeypatch):
    import fde_product.interview_extraction as extraction
    cfg = ws.load(CONFIG)
    docs = {name: ws.raw('docs/w02/'+name) for name in ('business_model.md', 'interview_notes.md')}
    monkeypatch.setattr(extraction, 'GUARD', 'Updated extraction instruction')
    monkeypatch.setattr(extraction, 'CONFLICT_PROMPT', 'Updated conflict instruction')
    ws.path(CONFIG).unlink()
    initialize(ws, cfg['extraction_run'])
    assert all(ws.raw('docs/w02/'+name) == value for name, value in docs.items())
    assert load_task(ws)[0] == cfg
    for result in pair(ws):
        assert result['status'] == 'succeeded'
        assert check_run(ws, result['run'])[0]['status'] == 'succeeded'

def test_init_replace_switches_extraction_with_scope_and_profile(ws):
    put(ws,'docs/w02/business_model.md','示例之后自己的模型。');before=ws.raw('docs/w02/business_model.md')
    cfg=ws.load(CONFIG);cfg.update(scope_id='fictional-library',profile='compatible',max_context_chars=50000);ws.path(CONFIG).write_text(json.dumps(cfg))
    with pytest.raises(CheckError,match='SCOPE_MISMATCH'):load_task(ws)
    with pytest.raises(CheckError,match='OUTPUT_EXISTS'):initialize(ws,cfg['extraction_run'])
    r=initialize(ws,cfg['extraction_run'],replace=True)
    assert r['replaced']==[CONFIG] and not r['created'] and ws.raw('docs/w02/business_model.md')==before
    new=ws.load(CONFIG);assert new['scope_id']=='qinghe' and new['profile']=='gateway-json' and new['extraction_run']==cfg['extraction_run']
    assert new['max_context_chars']==50000 and new['prompt']==cfg['prompt']
    load_task(ws)
    command=[sys.executable,str(ROOT/'tools/w02_knowledge.py'),'--workspace',str(ws.root),'init','--from-extraction',cfg['extraction_run'],'--replace']
    result=subprocess.run(command,cwd=ws.root,capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['replaced']==[CONFIG],result.stdout+result.stderr

def test_run_limit_is_reported_as_issue_with_hint(ws):
    pair(ws)
    runs=[f'artifacts/w02/knowledge_runs/RUN-{i}/run.json' for i in range(101)]
    r=precheck(ws.root,runs=runs)
    codes=[(i['item'],i['code']) for i in r['issues']]
    assert r['status']=='needs_revision' and codes==[('T04','RUN_LIMIT_EXCEEDED')] and '--run' in r['issues'][0]['next_step']
    assert any(c['item']=='D01' for c in r['completed']) and r['runtime']==[]
