"""Frozen Q4/Q5/Q6 acceptance. Default: retrieval only, zero network/model calls.

--online explicitly permits up to three configured-model calls in a disposable DB.
It never refreshes RSS or writes the real profile/database. Semantic checks remain human.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from info_agent.agent import AgentCore,CoreError
from info_agent.schemas import Profile,Article
from info_agent.storage import Store,dump
from info_agent.pool import upsert,namespace
from info_agent.agent_migrations import observation

FIXTURE=Path(__file__).resolve().parents[1]/'tests/fixtures/agent_acceptance_20261025.json'


class OfflineModel:
    model_name='no-paid-calls'
    def with_structured_output(self,*args,**kwargs):raise AssertionError('offline mode must not invoke a model')


def load_fixture():return json.loads(FIXTURE.read_text(encoding='utf-8'))


def seed(root,data):
    """Create only an isolated fixture profile, with snapshot source identities."""
    root=Path(root);now=datetime.fromisoformat(data['frozen_at'])
    p=Profile(profile_id='acceptance-v3',rsshub_url='http://127.0.0.1:1',keywords=['codex'],interests='冻结公开文字验收',
        source_plan={'keyword_search':{'enabled':True},'ups':[{'uid':'285286947','scope':'uploads'}],
        'following':{'enabled':True,'uid':'481963153','scope':'dynamics','content_type':'all'}})
    (root/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    with closing(Store(root/'workspace/info_agent.sqlite3',clock=lambda:now,rebuild_preferences=False)) as store:
        with store.db:
            for row in data['items']:
                dynamic=row['kind'] in ('following_dynamics','up_dynamics','following_videos')
                a=Article(id=row['id'],title=row['title'],url=row['url'],author=row['author'],published_at=row['published'],
                    body_text=row['body'],excerpt=row['body'][:1200],is_dynamic=dynamic,
                    dynamic_id=row['url'].rsplit('/',1)[-1] if dynamic else None,content_type=row['type'])
                ref=dict(kind=row['kind'],key=row['key'],label=row['kind']+' · '+row['key'])
                upsert(store.db,p,ref,a.model_dump(),now)
                observation(store.db,namespace(p),ref,'ok',now,1)
    return p,now


def run_acceptance(*,online=False,model=None):
    data=load_fixture();report=dict(frozen_at=data['frozen_at'],mode='configured_model' if online and model is None else 'injected_model' if online else 'retrieval_only',
        scope='冻结公开 RSS 文字；不访问 RSS、不识图、不独立核实产品规格；相关性与作者主张仍需人工判定。',cases=[])
    with tempfile.TemporaryDirectory(prefix='agent-v3-acceptance-') as directory:
        root=Path(directory);p,now=seed(root,data)
        def no_rss(*args,**kwargs):raise AssertionError('frozen acceptance cannot request RSS')
        core=AgentCore(root,clock=lambda:now,model=model if online else OfflineModel(),fetcher=no_rss)
        for case in data['cases']:
            request=case['request']
            search={k:v for k,v in request.items() if k!='question'}
            found=core.search_items(search)
            entry=dict(number=case['number'],request=request,expected_ids=case['expected_ids'],forbidden_ids=case['forbidden_ids'],
                manual_checks=case['manual_checks'],search=found.model_dump(),reference_found=bool(set(case['expected_ids'])&{i.id for i in found.items}),
                semantic_verdict='not_evaluated_offline',manual_review_required=True)
            if case.get('model_request'):
                entry['model_request']=case['model_request'];entry['fixture_note']=case['fixture_note']
            if online:
                try:
                    result=core.query_updates(case.get('model_request',request));entry['query']=result.model_dump()
                    selected={e.item.id for e in result.evidence}
                    entry['reference_selected']=bool(selected&set(case['expected_ids']))
                    entry['forbidden_selected']=bool(selected&set(case['forbidden_ids']))
                    entry['semantic_verdict']='requires_human_review'
                except CoreError as exc:entry['query_error']=exc.as_dict()
            report['cases'].append(entry)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online',action='store_true',help='显式允许现有付费模型最多三次筛选；每次运行使用新隔离库，不复用上次缓存')
    parser.add_argument('--output',type=Path,help='可选：保存安全结果 JSON；不含原始供应商响应')
    args=parser.parse_args(argv)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True)
    report=run_acceptance(online=args.online)
    if args.output:args.output.write_text(dump(report)+'\n',encoding='utf-8')
    print(dump(report));return 0


if __name__=='__main__':raise SystemExit(main())
