"""Frozen twelve-question acceptance: default retrieval/input inspection, zero RSS/model.

--online explicitly permits one model call per selected question in a disposable DB.
No supplier raw responses are retained. Semantic judgments remain human.
"""
import argparse
import hashlib
import json
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from info_agent.agent import AgentCore,CoreError
from info_agent.agent_models import QueryUpdatesRequest
from info_agent.agent_core import selection_prompt,PROMPT_VERSION
from info_agent.schemas import Profile
from info_agent.storage import Store,dump
from info_agent.pool import upsert,namespace
from info_agent.agent_migrations import observation
from info_agent.retrieval import retrieve,resolve_range,terms_for

FIXTURE=Path(__file__).resolve().parents[1]/'tests/fixtures/agent_v4_20261026'
NAMES=['二重螺旋验收-采集快照.json','二重螺旋验收-题目与参考.json','二重螺旋验收-运行结果.json',
       '二重螺旋验收-诊断.json','二重螺旋真实验收报告-20261026.md']


def load_fixture():
    manifest=json.loads((FIXTURE/'manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest['sha256'].items():
        if hashlib.sha256((FIXTURE/name).read_bytes()).hexdigest()!=digest:raise ValueError('frozen fixture changed')
    snapshot=json.loads((FIXTURE/NAMES[0]).read_text(encoding='utf-8-sig'))
    questions=json.loads((FIXTURE/NAMES[1]).read_text(encoding='utf-8-sig'))
    return snapshot,questions


def seed(root,snapshot):
    now=datetime.fromisoformat(snapshot['frozen_at'])
    keys=list(dict.fromkeys(row['key'] for row in snapshot['items']))
    p=Profile(profile_id='acceptance-v4',rsshub_url='http://127.0.0.1:1',keywords=keys,interests='冻结验收',
        source_plan={'keyword_search':{'enabled':True}})
    (root/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    with closing(Store(root/'workspace/info_agent.sqlite3',clock=lambda:now,rebuild_preferences=False)) as store:
        with store.db:
            for row in snapshot['items']:
                ref=dict(kind=row['kind'],key=row['key'],label='关键词 · '+row['key'])
                upsert(store.db,p,ref,row['article'],now)
                observation(store.db,namespace(p),ref,'ok',now,1)
    return p,now


def request_for(case,p):
    return dict(profile_id=p.profile_id,question=case['q'],terms=case['terms'],
        **{k:case[k] for k in ('time_range','max_duration_seconds','content_types','sources','limit') if k in case})


class OfflineModel:
    model_name='no-paid-calls'
    def with_structured_output(self,*args,**kwargs):raise AssertionError('offline cannot call model')


def run_acceptance(*,online=False,numbers=None,model=None):
    snapshot,questions=load_fixture()
    report=dict(strategy_version=PROMPT_VERSION,frozen_at=snapshot['frozen_at'],
        mode='configured_model' if online and model is None else 'injected_model' if online else 'retrieval_input_only',
        semantic_verdict='requires_human_review' if online else 'not_evaluated_offline',cases=[])
    with tempfile.TemporaryDirectory(prefix='agent-v4-acceptance-') as directory:
        root=Path(directory);p,now=seed(root,snapshot)
        def no_rss(*a,**kw):raise AssertionError('frozen acceptance cannot request RSS')
        core=AgentCore(root,clock=lambda:now,model=model if online else OfflineModel(),fetcher=no_rss)
        for case in questions['cases']:
            if numbers and case['n'] not in numbers:continue
            request=request_for(case,p);r=QueryUpdatesRequest.model_validate(request)
            search=core.search_items({k:v for k,v in request.items() if k!='question'})
            record=dict(number=case['n'],reference=case,request=request,search=search.model_dump(),manual_review_required=True)
            absolute=resolve_range(r.time_range,r.question,now);_,groups=terms_for(r.question,r.terms,p.agent_query.aliases)
            with closing(Store(core.db_path,rebuild_preferences=False)) as store:entries,_,_=retrieve(store,p,r,absolute,groups)
            try:
                inputs,truncated=core._model_input(p,r,entries) if entries else ([],False)
                record.update(input_items=inputs,input_truncated=truncated,
                    input_chars=len(selection_prompt(r.question,inputs,r.limit,core._request_context(r,absolute))) if hasattr(core,'_request_context') else len(selection_prompt(r.question,inputs,r.limit)),
                    input_real_ids=[e['article']['id'] for e in entries[:len(inputs)]])
            except CoreError as exc:record['input_error']=exc.as_dict()
            if online:
                try:record['query']=core.query_updates(request).model_dump()
                except CoreError as exc:record['query_error']=exc.as_dict()
            report['cases'].append(record)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online',action='store_true',help='显式调用真实模型，每题最多一次；不重试，不采集 RSS')
    parser.add_argument('--question',type=int,choices=range(1,13),action='append',help='仅复验指定题号，可重复')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv);report=run_acceptance(online=args.online,numbers=args.question)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(dump(report)+'\n',encoding='utf-8')
    print(dump(report));return 0


if __name__=='__main__':raise SystemExit(main())
