"""Read-only checks for the simplified W2 task; semantic judgement stays human."""
import hashlib
from pathlib import PurePosixPath
from .knowledge_io import AuditWorkspace
ROLES = {'ROLE-ZHOU', 'ROLE-LIN', 'ROLE-CHEN'}
def gate(item):
    return 'technical' if item.startswith('T') else 'delivery'
HINTS = {
 'SOURCE_NOT_FOUND':'补齐引用文件；路径以客户仓库为基准。',
 'HASH_MISMATCH':'恢复该次运行的原始快照；修改后另建运行，勿重写历史。',
 'LIVE_EXTRACTION_REQUIRED':'提供本人实际调用模型生成的候选及记录。',
 'REQUIRED_ROLES_MISSING':'补访缺少的角色；寒暄或 W1 访谈不能替代本周调查。',
 'W1_IDS_MISSING':'保留 W1 条目编号，将新增证据及修改理由写入访谈记录。',
}
from .natural_knowledge import CONFIG, VERSION, load_task, check_run
from .interview_extraction import ROOT, Workspace, require, CheckError, validate, index

RUBRIC = {
 'T01':'检查本人提取目标、提示词或配置及实际候选，能解释并复跑。',
 'T02':'抽查采用的信息是否得到原文支持，保留条件、观点与未知；不必手填复核枚举。',
 'T03':'读业务模型说明，解释对象、属性、关系、规则及候选动作的选择与依据。',
 'T04':'检查实际请求采用本次业务模型说明，回答是否使用相应定义。',
 'T05':'审阅本人问题的未提供／提供说明对照、未知与分歧判读；有错误再修改重跑，不要求四类固定题。',
 'D01':'三位必访的实际追问是否取得具体业务信息。',
 'D02':'核对至少一组针对同一问题、影响判断的不同说法或解释、条件、来源和下一步；共同未知不算冲突，不依模型冲突数组自动判定。',
 'D03':'在访谈与核查记录中审阅干系人四问和来源，不另交地图文件。',
 'D04':'对照 W1 快照和原编号 fit-gap，核对新增证据及修改理由或无变化的依据。'}
HINTS = {**HINTS,
 'COMPARISON_REQUIRED':'用 w02_knowledge.py compare 对本人问题运行同题对照；检查失败记录后再运行。',
 'CURRENT_MODEL_REQUIRED':'提交的业务模型或提取版本已改变，重新运行受影响的问题；旧运行保留。',
 'EXTRACTION_REQUIRED':'先运行自己的提取配置，使用成功候选，核查后再完善业务模型。',
 'RUN_LIMIT_EXCEEDED':'运行记录超过 100 条，未逐条检查；用重复的 --run 指定实际采用的同题对照 run.json，旧记录保留不删。'}


def precheck(workspace,*,runs=None,knowledge_config=CONFIG,
             business_model='docs/w02/business_model.md',interview_notes='docs/w02/interview_notes.md',
             w1_fit_gap='artifacts/w01/product_fit_gap.json',w2_fit_gap=None):
    ws=AuditWorkspace(workspace);issues=[];completed=[];runtime=[];roles=None
    def attempt(item,label,fn):
        try:
            value=fn();completed.append({'item':item,'check':label});return value
        except (CheckError,UnicodeError,KeyError,TypeError,ValueError,IndexError,StopIteration) as exc:
            code=exc.code if isinstance(exc,CheckError) else 'EVIDENCE_STRUCTURE_INVALID'
            issues.append({'gate':gate(item),'item':item,'code':code,
                'path':getattr(exc,'location','') or label,
                'next_step':HINTS.get(code,'核对该文件和运行记录；业务含义及取舍交人工审阅。')})
    for item,path in [('T03',business_model),('D03',interview_notes)]:
        attempt(item,path,lambda path=path:require(bool(ws.raw(path).decode('utf-8').strip()),'EMPTY_DOCUMENT',path))
    task=attempt('T01',knowledge_config,lambda:load_task(ws,knowledge_config))
    if task is not None:
        cfg,document,prompt,extraction,candidate=task
        attempt('T01',cfg['extraction_run'],lambda:require(extraction['mode']=='live','LIVE_EXTRACTION_REQUIRED'))
        attempt('T03',business_model,lambda:require(cfg['scope_id']=='qinghe' and cfg['business_model']==business_model,'SCOPE_MISMATCH'))
        def role_check():
            found=set()
            for source in candidate['sources']:
                t=ws.verify(source['file'])
                if (t['scope_id']=='qinghe' and t['week']==2 and t['origin']=='live' and t['state'] in ('active','ended')
                    and {'user','assistant'} <= {m['speaker'] for m in t['messages'] if m['content'].strip()}):found.add(t['role_id'])
            require(ROLES<=found,'REQUIRED_ROLES_MISSING',','.join(sorted(ROLES-found)))
            return sorted(found)
        roles=attempt('D01','必访记录',role_check)
    def fit_check(path):
        f=ws.load(path);validate(Workspace(ROOT).load('contracts/w01/product_fit_gap.schema.json'),f,path);index(f['items']);return f
    old=attempt('D04',w1_fit_gap,lambda:fit_check(w1_fit_gap))
    if w2_fit_gap is None and ws.path('artifacts/w02/product_fit_gap.json').exists():w2_fit_gap='artifacts/w02/product_fit_gap.json'
    if w2_fit_gap:
        def continuity():
            f=fit_check(w2_fit_gap);require(ws.path(w2_fit_gap)!=ws.path(w1_fit_gap),'W1_SNAPSHOT_OVERWRITTEN')
            if old is not None:require({x['id'] for x in old['items']}<={x['id'] for x in f['items']},'W1_IDS_MISSING')
        attempt('D04',w2_fit_gap,continuity)
    selected=(sorted(p.relative_to(ws.root).as_posix() for p in ws.path('artifacts/w02/knowledge_runs').glob('*/run.json'))
              if runs is None else list(dict.fromkeys(runs)))
    pairs={};seen=set()
    # Too many runs is reported like other issues, so the rest of the precheck stays visible.
    over_limit=len(selected)>100
    if over_limit:attempt('T04',f'{len(selected)} 条运行记录',lambda:require(False,'RUN_LIMIT_EXCEEDED',f'{len(selected)} 条运行记录'))
    for path in ([] if over_limit else selected):
        def inspect():
            run,settings,config,_=check_run(ws,path)
            require(run['run_id'] not in seen,'DUPLICATE_RUN_ID',path);seen.add(run['run_id'])
            result={'path':path,'run_id':run['run_id'],'mode':run['mode'],'status':run['status'],
                    'review':'pending','adopted':False,'reason':'failed_or_non_live'}
            if run['mode']!='live' or run['status']!='succeeded':return result
            if task is None:return result
            if (run['scope_id']!='qinghe' or not ws.matches(business_model,run['inputs'][0]['sha256'])
                or not ws.matches(cfg['extraction_run'],run['inputs'][1]['sha256'])):
                result['reason']='not_current_submission';return result
            if settings['pair_id'] is None:
                result['reason']='single_debug_run';return result
            request=ws.verify(run['request']);system=request['messages'][0]
            # Pair ID alone cannot make different questions, models, prompts or document versions a comparison.
            import json
            key=json.dumps([settings['pair_id'],settings['question'],run['model'],settings['profile'],
                            system,run['inputs'][0]['sha256'],run['inputs'][1]['sha256']],sort_keys=True,ensure_ascii=False)
            pairs.setdefault(key,set()).add(settings['include_knowledge'])
            result.update(adopted=True,reason='comparison_evidence_only',include_knowledge=settings['include_knowledge'])
            return result
        result=attempt('T04',path,inspect)
        if result is not None:runtime.append(result)
    if not over_limit:
        attempt('T05','本人同题对照',lambda:require(any(v=={False,True} for v in pairs.values()),'COMPARISON_REQUIRED'))
    return {'checker_version':'w02-checker-0.2.0','contract_version':VERSION,'workflow':'natural-language',
            'status':'needs_revision' if issues else 'ready_for_review','final_gate':'not_determined',
            'options':{'format':'natural','runs':selected,'knowledge_config':knowledge_config,
                       'business_model':business_model,'interview_notes':interview_notes,
                       'w1_fit_gap':w1_fit_gap,'w2_fit_gap':w2_fit_gap},
            'issues':issues,'completed':completed,'runtime':runtime,'roles':roles,
            'coverage':{'comparison_pairs':sum(v=={False,True} for v in pairs.values())},
            'review_queue':[{'gate':gate(i),'item':i,'status':'pending','instruction':text} for i,text in RUBRIC.items()],
            'files':{p:hashlib.sha256(data).hexdigest() for p,data in sorted(ws.files.items())},
            'limits':['结构通过不证明原文支持、人工核查或实际语义正确；九项人工判断均为 pending。',
                      'live 标签和哈希不证明远端执行或作者身份，需要时抽查复跑。',
                      '不强制本体 YAML、事实复核枚举、独立地图、图书馆或四类固定题。',
                      'W1 快照需与前周提交核对；无新增影响时在访谈记录说明。']}
