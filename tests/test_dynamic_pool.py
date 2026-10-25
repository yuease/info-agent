"""Dynamic pool contracts: offline fixtures, fixed clocks and isolated processes."""
import json
import logging
import multiprocessing
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from info_agent.schemas import Profile,Article,Choice
from info_agent.storage import Store,dump
from info_agent.dynamics import parse_dynamic_feed,FeedBatch
from info_agent.sources import subscriptions
from info_agent.pool import snapshot,upsert,namespace
from info_agent.collection import collect
from info_agent.filtering import marks,Rule,register,RULES
from info_agent.workflow import build_briefing
from info_agent.errors import RunFailure
from info_agent.web import WebApp,RequestError,make_server
from info_agent.preferences import SHANGHAI
from info_agent import jobs

NOW=datetime(2026,10,25,12,tzinfo=SHANGHAI)


def profile(**kw):
    return Profile.model_validate(dict(rsshub_url='http://127.0.0.1:1200',keywords=kw.pop('keywords',[]),interests='项目实践',
        source_plan=kw.pop('source_plan',dict(keyword_search={'enabled':False},ups=[dict(uid='123',scope='both')],following={'enabled':False})),**kw))


def article(i='a',dynamic=False,kind='text',**kw):
    return Article.model_validate(dict(id=i,title='项目实践 '+i,url='https://t.bilibili.com/'+('100'+str(len(i))) if dynamic else 'https://www.bilibili.com/video/av1',
        excerpt='项目实践说明',is_dynamic=dynamic,dynamic_id=i if dynamic else None,
        content_type=kind if dynamic else 'video',published_at=NOW.isoformat(),author_uid='123',**kw)).model_dump()


def ref(kind='up_dynamics',key='123'):
    return dict(kind=kind,key=key,label=kind+' · '+key)


@pytest.fixture
def store(tmp_path):
    with closing(Store(tmp_path/'data.sqlite3',clock=lambda:NOW)) as s:yield s


def put(store,p,a,r=None,now=NOW):
    with store.db:upsert(store.db,p,r or ref(),a,now)


def run_collect(store,p,fetch,run='collection',**kw):
    return collect(p.model_dump(),store=store,clock=lambda:NOW,run_id=run,fetcher=fetch,**kw)


class Model:
    def __init__(self,exc=None,hook=None):self.calls=0;self.exc=exc;self.hook=hook;self.prompts=[]
    def with_structured_output(self,*a,**kw):return self
    def invoke(self,prompt):
        self.calls+=1;self.prompts.append(prompt)
        if self.hook:self.hook()
        if self.exc:raise self.exc
        payload=json.loads(prompt.split('\n',1)[1])
        return {'picks':[Choice(id=a['id'],summary='作者介绍项目。',reason='符合兴趣',action='查看原文').model_dump() for a in payload['articles'][:1]]}


def generate(store,p,m,**kw):
    def forbidden(*a,**k):pytest.fail('generation fetched RSS')
    return build_briefing(p.model_dump(),store=store,model=m,fetcher=forbidden,clock=lambda:NOW,from_pool=True,**kw).invoke({})


def test_profile_migration_projection_and_identity():
    old=dict(rsshub_url='http://localhost',keywords=['a'],interests='项目实践',sources={'up_uploads':{'enabled':True,'uids':['123']}})
    p=Profile.model_validate(old)
    assert p.source_plan.ups[0].scope=='uploads' and p.source_plan.ups[0].enabled
    new=p.model_dump()
    new['source_plan']['ups'][0]['scope']='dynamics'
    new['source_plan']['keyword_search']['enabled']=False;new['keywords']=[]
    q=Profile.model_validate(new)
    assert not q.sources.up_uploads.enabled and not q.sources.keyword_search.enabled
    assert q.context==p.context and q.legacy_context!=p.legacy_context
    new['sources']['keyword_search']['enabled']=True
    assert not Profile.model_validate(new).source_plan.keyword_search.enabled


@pytest.mark.parametrize('change',[{'source_plan':{'keyword_search':{'enabled':False}}},
    {'source_plan':{'ups':[{'uid':'123'},{'uid':'123'}]}},
    {'source_plan':{'ups':[{'uid':'123','scope':'invalid'}]}},
    {'source_plan':{'following':{'enabled':True,'uid':'abc'}}},
    {'collection':{'dynamic_ttl_hours':48}},{'collection':{'interval_minutes':0}},
    {'collection':{'up_dynamic_batches':4}},{'filters':{'blocked_ups':[{'uid':'x'}]}},
    {'filters':{'rules':[{'id':'lottery'},{'id':'lottery'}]}}])
def test_invalid_new_configuration(change):
    with pytest.raises(ValueError):profile(**change)


def test_all_routes_and_disabled_configuration():
    p=profile(keywords=['python'],source_plan={'keyword_search':{'enabled':True},'ups':[
        {'uid':'123','scope':'both'},{'uid':'456','scope':'dynamics','enabled':False}],
        'following':{'enabled':True,'uid':'789','scope':'dynamics'}})
    paths=[r['path'] for r in subscriptions(p)]
    assert len(paths)==4 and not any('/456/' in s for s in paths)
    assert '/bilibili/user/dynamic/123/directLink=0&hideGoods=0' in paths
    assert '/bilibili/followings/dynamic/789/embed=0' in paths
    assert all('offset' not in s for s in paths)


def test_real_public_template_fixture():
    batch=parse_dynamic_feed((Path(__file__).parent/'fixtures/up-dynamics-public.xml').read_bytes(),allow_channel_author=True)
    assert batch.raw_count==3 and len(batch)==3 and batch.cursor==batch[-1].dynamic_id
    assert batch[0].content_type=='video' and batch[0].source_ids==['bilibili:BV1Evb562EfN']
    assert batch[0].author_uid=='481963153' and batch[1].original_author_uid is None
    assert batch[-1].content_type=='image' and batch[-1].images and batch[-1].duration_seconds is None
    assert marks(batch[1].model_dump())
    following=parse_dynamic_feed((Path(__file__).parent/'fixtures/up-dynamics-public.xml').read_bytes())
    assert following[0].author_uid is None  # following account != per-item author


def xml_item(typ='',description='',uid='123',did='99',original=''):
    import xml.sax.saxutils as x
    return ('<rss><channel><item><title>测试动态</title><link>https://t.bilibili.com/'+did+'</link><guid>'+did+'</guid>'
        '<author uid="'+uid+'">作者</author><dynamic_type>'+typ+'</dynamic_type><original_type>'+original+'</original_type>'
        '<pubDate>Sun, 25 Oct 2026 04:00:00 GMT</pubDate><description>'+x.escape(description)+'</description></item></channel></rss>').encode()


@pytest.mark.parametrize('marker,expected',[('DYNAMIC_TYPE_DRAW','image'),('DYNAMIC_TYPE_WORD','text'),
    ('DYNAMIC_TYPE_ARTICLE','article'),('DYNAMIC_TYPE_UNKNOWN','unknown'),('DYNAMIC_TYPE_MUSIC','audio')])
def test_explicit_dynamic_types(marker,expected):
    a=parse_dynamic_feed(xml_item(marker,'文字 <img src="https://i0.hdslb.com/bfs/new_dyn/test.jpg">'))[0]
    assert a.content_type==expected and a.images
    assert a.author_uid=='123' and a.duration_seconds is None


def test_unknown_images_repost_and_unsafe_images():
    a=parse_dynamic_feed(xml_item('DYNAMIC_TYPE_FORWARD','我的说明 //转发自: @原作者: 原内容 <img src="https://evil.invalid/x.jpg"><img src="https://i0.hdslb.com/bfs/face/avatar.jpg">',original='DYNAMIC_TYPE_WORD'))[0]
    assert a.content_type=='text' and a.repost_text=='我的说明' and not a.images
    a=parse_dynamic_feed(xml_item('NEW_TYPE','<img src="https://i0.hdslb.com/bfs/new_dyn/test.jpg">'))[0]
    assert a.content_type=='unknown' and not a.text_available and a.images
    raw=xml_item('DYNAMIC_TYPE_WORD','<script>private</script>普通正文')
    assert 'script' not in parse_dynamic_feed(raw)[0].excerpt and 'private' not in parse_dynamic_feed(raw)[0].excerpt


@pytest.mark.parametrize('text,expected',[
    ('本期讲解抽奖算法，不是活动。',False),('抽奖结果公布，感谢关注。',False),
    ('不要参与陌生人抽奖。',False),('抽奖',False),('关注并转发本条动态，我们将随机抽选3位送出以下奖品。',True),
    ('转发本条动态，即有机会获得亲签照。',True),('转发本条动态观看讲解，介绍抽奖算法。',False)])
def test_conservative_lottery(text,expected):
    a=article(dynamic=True);a['excerpt']=text
    assert bool(marks(a))==expected
    a['is_dynamic']=False;assert not marks(a)


def test_rule_marking_toggle_restore_and_registry(store):
    p=profile();a=article(dynamic=True);a['excerpt']='关注并转发本条动态，我们将随机抽选3位送出以下奖品。'
    put(store,p,a)
    assert snapshot(store,p,NOW)['eligible_count']==0
    assert snapshot(store,p,NOW)['filter_counts']['lottery']==1
    q=p.model_copy(update={'filters':p.filters.model_copy(update={'rules':[]})})
    assert snapshot(store,q,NOW)['eligible_count']==1
    saved=json.loads(store.db.execute('SELECT data FROM pool_items').fetchone()[0])
    assert saved['filter_hits'][0]['id']=='lottery' and saved['filter_hits'][0]['version']==1
    try:
        register(Rule('test_extension',2,'测试','扩展',lambda a:'测试命中' if a['title'].startswith('项目') else None))
        data=p.model_dump();data['filters']['rules']=[{'id':'test_extension','version':2,'enabled':True}]
        assert snapshot(store,Profile.model_validate(data),NOW)['eligible_count']==0
    finally:RULES.pop('test_extension',None)


def test_block_scope_and_independent_video_contribution(store):
    p=profile();a=article('same',dynamic=True,kind='video');b=article('same')
    put(store,p,a);put(store,p,b,ref('up_uploads'))
    data=p.model_dump();data['filters']['blocked_ups']=[{'uid':'123','scope':'dynamics'}]
    blocked=Profile.model_validate(data);view=snapshot(store,blocked,NOW)
    assert view['eligible_count']==1 and not view['articles'][0]['is_dynamic']
    assert [s['kind'] for s in view['articles'][0]['sources']]==['up_uploads']
    data['filters']['blocked_ups'][0]['scope']='all'
    assert snapshot(store,Profile.model_validate(data),NOW)['eligible_count']==0
    assert snapshot(store,p,NOW)['eligible_count']==1 and len(snapshot(store,p,NOW)['articles'][0]['sources'])==2
    b['author_uid']=None;put(store,p,b,ref('up_uploads'))
    assert snapshot(store,Profile.model_validate(data),NOW)['eligible_count']==1  # no nickname/request UID guess


def test_repost_actor_and_original_author_blocking(store):
    p=profile();a=article(dynamic=True);a['original_author_uid']='456';put(store,p,a)
    data=p.model_dump();data['filters']['blocked_ups']=[{'uid':'456','scope':'dynamics'}]
    assert snapshot(store,Profile.model_validate(data),NOW)['eligible_count']==1
    data['filters']['blocked_ups'][0]['scope']='all'
    assert snapshot(store,Profile.model_validate(data),NOW)['eligible_count']==0


@pytest.mark.parametrize('hours',[24,72,168])
def test_expiry_boundaries_and_repeated_collection(store,hours):
    p=profile(collection={'dynamic_ttl_hours':hours});a=article(dynamic=True)
    put(store,p,a)
    assert snapshot(store,p,NOW+timedelta(hours=hours,microseconds=-1))['eligible_count']==1
    assert snapshot(store,p,NOW+timedelta(hours=hours))['eligible_count']==0
    a['published_at']=(NOW+timedelta(hours=hours)).isoformat();put(store,p,a,now=NOW+timedelta(hours=hours))
    assert snapshot(store,p,NOW+timedelta(hours=hours))['eligible_count']==0
    assert store.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==1


@pytest.mark.parametrize('publication',[None,'invalid',(NOW+timedelta(days=30)).isoformat()])
def test_invalid_time_finite_first_seen_fallback(store,publication):
    p=profile();a=article(dynamic=True);a['published_at']=publication;put(store,p,a)
    put(store,p,a,now=NOW+timedelta(hours=71))
    assert snapshot(store,p,NOW+timedelta(hours=71))['eligible_count']==1
    assert snapshot(store,p,NOW+timedelta(hours=72))['eligible_count']==0


def test_videos_no_dynamic_ttl_text_no_video_duration(store):
    p=profile(max_duration_seconds=1)
    video=article('video');video['duration_seconds']=1;video['published_at']=(NOW-timedelta(days=50)).isoformat()
    put(store,p,video,ref('up_uploads'))
    put(store,p,article('text',dynamic=True))
    put(store,p,article('unknown-video'))
    assert snapshot(store,p,NOW)['eligible_count']==2


@pytest.mark.parametrize('wanted,expected',[('all',3),('video',1),('non_video',1)])
def test_dynamic_type_filter_unknown_only_all(store,wanted,expected):
    p=profile(source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':'123','scope':'dynamics','content_type':wanted}]})
    for i,kind in enumerate(('video','text','unknown')):put(store,p,article(str(i),dynamic=True,kind=kind))
    assert snapshot(store,p,NOW)['eligible_count']==expected


def test_source_disable_and_fair_budget_before_stats(store):
    p=profile(max_candidates=2,source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':'123','scope':'both'},{'uid':'456','scope':'dynamics'}]})
    for i in range(5):
        put(store,p,article('upload'+str(i)),ref('up_uploads'))
        put(store,p,article('dynamic'+str(i),dynamic=True))
        put(store,p,article('other'+str(i),dynamic=True),ref(key='456'))
    view=snapshot(store,p,NOW)
    assert view['eligible_count']==15 and len(view['articles'])==2
    assert {a['sources'][0]['kind'] for a in view['articles']}=={'up_uploads','up_dynamics'}
    data=p.model_dump();data['source_plan']['ups'][0]['enabled']=False
    q=Profile.model_validate(data);view=snapshot(store,q,NOW)
    assert view['eligible_count']==5 and all(a['sources'][0]['key']=='456' for a in view['articles'])


def dynamic_only(uids=('123',),**kw):
    return profile(source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':u,'scope':'dynamics'} for u in uids]},**kw)


def test_round_robin_three_batches_and_cursor_before_filters(store):
    p=dynamic_only(('123','456'));calls=[]
    def fetch(url):
        calls.append(url);uid='123' if '/123/' in url else '456';n=sum('/'+uid+'/' in u for u in calls)
        a=article(uid+str(n),dynamic=True)
        a['excerpt']='关注并转发本条动态，我们将随机抽选3位送出以下奖品。'
        return FeedBatch([a],raw_ids=[uid+str(n)],oldest=NOW)
    r=run_collect(store,p,fetch)
    assert len(calls)==6 and ['/123/' in u for u in calls]==[True,False,True,False,True,False]
    assert '&offset=1231' in calls[2] and '&offset=4561' in calls[3]
    assert all('directLink=0&hideGoods=0' in u for u in calls)
    assert all(s['batches']==3 and s['stop_reason']=='batch_limit' for s in r['source_results'])
    assert store.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==6
    assert snapshot(store,p,NOW)['eligible_count']==0 and r['metrics']['logical_model_calls']==0


@pytest.mark.parametrize('mode,expected,ncalls',[
    ('empty','empty_batch',1),('missing','missing_cursor',1),('stalled','cursor_not_advanced',2),
    ('nonew','no_new_items',2),('old','time_boundary',1),('failed','request_failed',1)])
def test_pagination_stops_without_false_end(store,mode,expected,ncalls):
    calls=[]
    def fetch(url):
        calls.append(url)
        if mode=='failed':raise TimeoutError('PRIVATE_SENTINEL')
        ids=[] if mode=='empty' else [None] if mode=='missing' else ['1','2'] if mode=='nonew' and len(calls)==1 else ['2','1'] if mode=='nonew' else ['1']
        return FeedBatch([article('1',dynamic=True)] if ids else [],raw_ids=ids,oldest=NOW-timedelta(hours=73) if mode=='old' else NOW)
    if mode=='failed':
        with pytest.raises(RunFailure) as exc:run_collect(store,dynamic_only(),fetch)
        r=json.loads(store.db.execute('SELECT data FROM runs WHERE id=?',(exc.value.run_id,)).fetchone()[0])
    else:r=run_collect(store,dynamic_only(),fetch)
    assert len(calls)==ncalls and r['source_results'][0]['stop_reason']==expected
    if mode=='failed':assert r['source_results'][0]['error_code']=='SOURCE_TIMEOUT'


def test_global_request_time_budgets_and_nonpaged_routes(store):
    p=profile(collection={'max_requests':2},source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':'123','scope':'both'}],'following':{'uid':'456','enabled':True,'scope':'dynamics'}})
    calls=[]
    def fetch(url):calls.append(url);return FeedBatch([article('a'+str(len(calls)),dynamic='/dynamic/' in url)],raw_ids=[str(len(calls))],oldest=NOW)
    r=run_collect(store,p,fetch)
    assert len(calls)==2 and all('offset' not in u for u in calls)
    assert r['source_results'][-1]['error_code']=='SOURCE_BUDGET'
    ticks=iter([0,0,200,200,200,200]);calls.clear()
    r=run_collect(store,dynamic_only(('123','456')),fetch,run='time-budget',timer=lambda:next(ticks))
    assert len(calls)==1 and r['source_results'][1]['stop_reason']=='global_time_budget'


def test_failed_later_batch_keeps_first_and_other_sources(store):
    p=profile();calls=[]
    def fetch(url):
        calls.append(url)
        if 'offset=' in url:raise TimeoutError('PRIVATE_SENTINEL')
        return FeedBatch([article('dynamic',dynamic=True)] if '/dynamic/' in url else [article('video')],raw_ids=['9'],oldest=NOW)
    r=run_collect(store,p,fetch)
    assert r['status']=='collection' and len(r['failures'])==1
    assert r['source_results'][1]['count']==1 and r['source_results'][1]['status']=='failed'
    assert snapshot(store,p,NOW)['eligible_count']==2
    assert 'PRIVATE_SENTINEL' not in dump(r)


def test_collect_generate_snapshot_failure_and_success_cooldown(store):
    p=profile();put(store,p,article('first'),ref('up_uploads'))
    bad=Model(ValueError('PRIVATE_SENTINEL'))
    with pytest.raises(RunFailure):generate(store,p,bad)
    assert snapshot(store,p,NOW)['eligible_count']==1 and bad.calls==1
    def add_new():put(store,p,article('second'),ref('up_uploads'))
    m=Model(hook=add_new);result=generate(store,p,m)
    assert len(result['articles'])==1 and result['articles'][0]['id']=='first' and m.calls==1
    assert snapshot(store,p,NOW)['eligible_count']==1 and snapshot(store,p,NOW)['articles'][0]['id']=='second'
    assert 'images' not in json.loads(m.prompts[0].split('\n',1)[1])['articles'][0]
    assert result['metrics']['rss_requests']==0


def test_empty_pool_zero_model_and_image_summary_guard(store):
    p=profile();m=Model();result=generate(store,p,m)
    assert result['status']=='empty' and m.calls==0
    a=article(dynamic=True,kind='image');a.update(excerpt='',text_available=False,images=['https://i0.hdslb.com/bfs/new_dyn/pic.jpg'])
    put(store,p,a);r=generate(store,p,m)
    assert m.calls==1 and '无法根据图片' in r['selection']['picks'][0]['summary']
    assert 'bfs/new_dyn' not in m.prompts[0]


def task_job(i):return dict(id=i,run_id=i,status='running',started=NOW.isoformat(),record_available=False)


def claim_process(path,queue,gate,scheduled):
    with closing(Store(path,clock=lambda:NOW)) as store:
        owner=jobs.claim(store,'collect',task_job(str(os.getpid())),NOW,scheduled=scheduled,interval=60)
        queue.put(bool(owner));gate.wait(10)


@pytest.mark.parametrize('scheduled',[False,True])
def test_multi_process_claim_and_schedule_no_double_start(tmp_path,scheduled):
    path=tmp_path/'data.db'
    with closing(Store(path)) as s:
        if scheduled:
            with s.db:s.db.execute('INSERT INTO collection_schedule VALUES(1,1,?,60)',(NOW.timestamp()-1,))
    ctx=multiprocessing.get_context('spawn');q=ctx.Queue();gate=ctx.Event()
    processes=[ctx.Process(target=claim_process,args=(path,q,gate,scheduled)) for _ in range(2)]
    try:
        for process in processes:process.start()
        results=[q.get(timeout=25) for _ in processes]
        assert sorted(results)==[False,True]
    finally:
        gate.set()
        for process in processes:process.join(20)
    assert all(p.exitcode==0 for p in processes)
    with closing(Store(path)) as s:
        assert s.db.execute('SELECT count(*) FROM task_jobs').fetchone()[0]==1
        if scheduled:assert jobs.claim(s,'collect',task_job('no-catchup'),NOW,scheduled=True,interval=60) is None
        else:assert jobs.claim(s,'collect',task_job('restart'),NOW) is not None


def test_independent_leases_live_owner_and_recovery(store):
    owner=jobs.claim(store,'collect',task_job('c'),NOW)
    assert owner and jobs.claim(store,'generate',task_job('g'),NOW)
    with store.db:store.db.execute('UPDATE task_leases SET expires=0')
    assert jobs.claim(store,'collect',task_job('blocked'),NOW) is None
    assert jobs.latest(store,'collect')['status']=='running'
    with store.db:store.db.execute('UPDATE task_leases SET pid=-1')
    assert jobs.latest(store,'collect')['error_code']=='PROCESS_INTERRUPTED'
    assert jobs.claim(store,'collect',task_job('recovered'),NOW)


def test_schedule_first_due_disable_reenable_no_backlog(store):
    assert jobs.claim(store,'collect',task_job('first'),NOW,scheduled=True,interval=60) is None
    assert jobs.claim(store,'collect',task_job('early'),NOW+timedelta(minutes=59),scheduled=True,interval=60) is None
    assert jobs.claim(store,'collect',task_job('due'),NOW+timedelta(minutes=60),scheduled=True,interval=60)
    with store.db:store.db.execute('DELETE FROM task_leases')
    jobs.disable_schedule(store)
    assert jobs.claim(store,'collect',task_job('reenable'),NOW+timedelta(days=20),scheduled=True,interval=60) is None


def test_pool_migration_backup_idempotent_preserves_feedback(tmp_path):
    path=tmp_path/'old.db';p=profile()
    with closing(Store(path)) as s:
        a=article();s.run('old',p.context,NOW,'completed',{'profile':p.model_dump()},[dict(a,sources=[ref('up_uploads')])],[Choice(id='a',summary='摘要',reason='理由',action='阅读').model_dump()])
        event=s.feedback('old:1','useful','',NOW)
        with s.db:
            for table in ('pool_items','pool_collections','task_leases','task_jobs','collection_schedule'):s.db.execute('DROP TABLE '+table)
            s.db.execute('PRAGMA user_version=2')
    with closing(Store(path)) as s:
        assert s.migration_backup and '.pre-pool-' in s.migration_backup.name
        backup=sqlite3.connect(s.migration_backup)
        assert backup.execute('PRAGMA user_version').fetchone()[0]==2;backup.close()
        assert s.db.execute('PRAGMA user_version').fetchone()[0]==4
        assert s.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==1
        assert s.db.execute('SELECT id FROM feedback_events').fetchone()[0]==event
        s.feedback(None,'undo','',NOW,undo=event)
    with closing(Store(path)) as s:
        assert s.migration_backup is None and s.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==1
        assert not s.effective_feedback(p.context)


def test_atomic_batch_failure_rolls_back_pool_but_keeps_previous_batch(store,monkeypatch):
    import info_agent.collection as collection
    original=collection.upsert;count=0
    def fail(*args,**kw):
        nonlocal count
        count+=1
        if count==2:raise sqlite3.OperationalError('PRIVATE_SENTINEL')
        return original(*args,**kw)
    monkeypatch.setattr(collection,'upsert',fail)
    with pytest.raises(RunFailure) as e:run_collect(store,profile(),lambda _:[article('one'),article('two')])
    assert e.value.diagnosis['error_code']=='PERSISTENCE_FAILED'
    assert store.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==0


def test_web_tasks_can_overlap_config_locked_and_pool_state_readonly(tmp_path):
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    collected=threading.Event();release=threading.Event();calls=[]
    def fetch(url):calls.append(url);collected.set();assert release.wait(10);return []
    app=WebApp(tmp_path,clock=lambda:NOW,fetcher=fetch)
    first=app.start_collect();assert collected.wait(5)
    try:
        with pytest.raises(RequestError):app.start_collect()
        gen=app.start_generate()
        for _ in range(500):
            if app.job['status']!='running':break
            time.sleep(.01)
        assert app.job['status']=='completed'  # empty pool, never imports a paid model
        with pytest.raises(RequestError):app.save_profile({'expected':p.model_dump(),'changes':{'max_picks':2}})
        before=len(calls);app.state();app.state();assert len(calls)==before
        assert app.collection_job['run_id']==first['run_id'] and app.job['run_id']==gen['run_id']
    finally:release.set()
    for _ in range(500):
        if app.collection_job['status']!='running':break
        time.sleep(.01)
    assert app.collection_job['status']=='completed'


def test_profile_snapshot_rules_and_source_changes_keep_feedback_permissions(tmp_path):
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    before=app.state();old_context=app.profile().context
    app.save_profile({'expected':before['profile'],'changes':{'collection':{'enabled':False,'interval_minutes':120,'dynamic_ttl_hours':24},
        'filters':{'rules':[{'id':'lottery','enabled':False},{'id':'future_rule','enabled':True,'version':9}],'blocked_ups':[{'uid':'123','scope':'dynamics'}]}}})
    assert app.profile().context==old_context and app.state()['filter_warnings'][0]['id']=='future_rule'
    with pytest.raises(RequestError):app.save_profile({'expected':before['profile'],'changes':{'max_picks':3}})
    assert app.state()['capabilities']['candidate_pool_v1']


def test_following_video_expiry_is_not_upload_expiry(store):
    p=profile(source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':'123','scope':'uploads'}],
        'following':{'uid':'456','enabled':True,'scope':'videos'}})
    a=article('same');a['published_at']=(NOW-timedelta(hours=80)).isoformat()
    put(store,p,a,ref('following_videos','456'));put(store,p,a,ref('up_uploads'))
    view=snapshot(store,p,NOW)
    assert view['eligible_count']==1 and [r['kind'] for r in view['articles'][0]['sources']]==['up_uploads']


def test_mixed_pinned_old_and_new_batch_does_not_stop_early(store):
    p=dynamic_only();calls=[]
    def fetch(url):
        calls.append(url)
        batch=FeedBatch([article(str(len(calls)),dynamic=True)],raw_ids=[str(len(calls))],oldest=NOW-timedelta(days=30))
        batch.newest=NOW
        return batch
    r=run_collect(store,p,fetch)
    assert len(calls)==3 and r['source_results'][0]['stop_reason']=='batch_limit'


def test_cross_source_real_video_identity_and_context_merge(store):
    from info_agent.rss import parse_feed
    dynamic=parse_dynamic_feed((Path(__file__).parent/'fixtures/up-dynamics-public.xml').read_bytes(),allow_channel_author=True)[0].model_dump()
    dynamic['published_at']=NOW.isoformat();dynamic['excerpt']='转发者补充说明';dynamic['dynamic_contexts'][0]['text']='转发者补充说明'
    video=parse_feed(b'<rss><channel><item><title>original</title><link>https://www.bilibili.com/video/BV1Evb562EfN</link></item></channel></rss>')[0].model_dump()
    p=profile()
    with store.db:
        upsert(store.db,p,ref(),store.canonicalize(dynamic),NOW)
        upsert(store.db,p,ref('up_uploads'),store.canonicalize(video),NOW)
    view=snapshot(store,p,NOW);a=view['articles'][0]
    assert view['eligible_count']==1 and len(a['sources'])==2
    assert a['dynamic_contexts'][0]['text']=='转发者补充说明' and a['content_type']=='video'


def test_model_failure_safe_diagnostic_keeps_pool(store):
    from langchain_core.exceptions import OutputParserException
    p=profile();put(store,p,article(),ref('up_uploads'))
    with pytest.raises(RunFailure) as error:generate(store,p,Model(OutputParserException('PRIVATE_SENTINEL',llm_output='PRIVATE_SENTINEL')))
    assert error.value.diagnosis['error_code']=='MODEL_PARSE'
    assert snapshot(store,p,NOW)['eligible_count']==1
    assert 'PRIVATE_SENTINEL' not in ''.join(x.read_text(encoding='utf-8') for x in store.output.rglob('*.json'))


def test_scheduler_tick_collects_without_model(tmp_path):
    p=dynamic_only(collection={'enabled':True,'interval_minutes':30})
    (tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    now=[NOW];calls=[]
    app=WebApp(tmp_path,clock=lambda:now[0],fetcher=lambda url:calls.append(url) or [])
    scheduler=jobs.Scheduler(app)
    assert scheduler.tick() is None and not calls
    now[0]+=timedelta(minutes=31)
    job=scheduler.tick();assert job and job['operation']=='collect'
    for _ in range(500):
        if app.collection_job['status']!='running':break
        time.sleep(.01)
    assert len(calls)==1 and app.job is None and app.collection_job['status']=='completed'
    assert scheduler.tick() is None and len(calls)==1


def test_collection_task_export_failure_does_not_lose_pool(store,monkeypatch):
    original=store.export
    def export(run_id):
        if store.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]=='collection':raise OSError('PRIVATE_SENTINEL')
        original(run_id)
    monkeypatch.setattr(store,'export',export)
    with pytest.raises(RunFailure) as error:run_collect(store,profile(),lambda url:[] if '/dynamic/' in url else [article()])
    assert error.value.diagnosis['error_code']=='EXPORT_FAILED' and not error.value.diagnosis['briefing_saved']
    assert snapshot(store,profile(),NOW)['eligible_count']==1
    monkeypatch.setattr(store,'export',original);store.recover()
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0]==0


def test_unsupported_rule_version_preserved_but_not_applied(store):
    p=profile(filters={'rules':[{'id':'lottery','version':99,'enabled':True}]})
    a=article(dynamic=True);a['excerpt']='关注并转发本条动态，我们将随机抽选3位送出以下奖品。';put(store,p,a)
    assert snapshot(store,p,NOW)['eligible_count']==1
    assert p.filters.rules[0].version==99


def test_cli_collect_generate_preview_feedback_and_undo(tmp_path,monkeypatch,capsys):
    from info_agent import cli
    import info_agent.collection as collection
    p=profile();path=tmp_path/'briefing_profile.json';path.write_text(dump(p.model_dump()),encoding='utf-8')
    monkeypatch.setattr(cli,'ROOT',tmp_path);monkeypatch.setattr(cli,'local_now',lambda:NOW)
    original_collect=collection.collect
    monkeypatch.setattr(collection,'collect',lambda profile,**kw:original_collect(profile,fetcher=lambda _:[article()],**kw))
    m=Model();monkeypatch.setattr(cli,'build_briefing',lambda profile,**kw:build_briefing(profile,model=m,clock=lambda:NOW,**kw))
    assert cli.main(['--preview'])==0 and m.calls==0
    assert cli.main(['--show'])==0 and m.calls==1
    assert cli.main([])==0 and m.calls==1
    with closing(Store(tmp_path/'workspace/info_agent.sqlite3')) as store:
        run=store.db.execute('SELECT run FROM recommendations').fetchone()[0]
    assert cli.main(['feedback','--run',run,'--item','1','--value','useful','--key','cli-rate'])==0
    with closing(Store(tmp_path/'workspace/info_agent.sqlite3')) as store:
        event=store.db.execute("SELECT id FROM feedback_events WHERE value='useful'").fetchone()[0]
    assert cli.main(['feedback','--undo',event])==0
    alternate=tmp_path/'other.json';alternate.write_text(dump(p.model_dump()),encoding='utf-8')
    assert cli.main(['--profile',str(alternate),'collect'])==0
    assert '未调用模型' in capsys.readouterr().out


def test_cli_failure_records_safe_job_instead_of_masking_exception(tmp_path,monkeypatch,capsys):
    from info_agent import cli
    from langchain_core.exceptions import OutputParserException
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    monkeypatch.setattr(cli,'ROOT',tmp_path);monkeypatch.setattr(cli,'local_now',lambda:NOW)
    with closing(Store(tmp_path/'workspace/info_agent.sqlite3')) as store:put(store,p,article(),ref('up_uploads'))
    model=Model(OutputParserException('PRIVATE_SENTINEL',llm_output='PRIVATE_SENTINEL'))
    monkeypatch.setattr(cli,'build_briefing',lambda profile,**kw:build_briefing(profile,model=model,clock=lambda:NOW,**kw))
    assert cli.main([])==1 and model.calls==1
    output=capsys.readouterr();assert 'MODEL_PARSE' in output.err and 'PRIVATE_SENTINEL' not in output.err
    with closing(Store(tmp_path/'workspace/info_agent.sqlite3')) as store:
        job=jobs.latest(store,'generate')
        assert job['status']=='failed' and job['error_code']=='MODEL_PARSE' and job['finished']


def test_nested_credentials_not_accepted_as_rule_configuration():
    with pytest.raises(ValueError):profile(filters={'rules':[{'id':'lottery','cookie':'PRIVATE_SENTINEL'}]})


def test_following_composite_and_uploads_never_paginate(store):
    p=profile(source_plan={'keyword_search':{'enabled':False},'ups':[{'uid':'123','scope':'uploads'}],
        'following':{'uid':'456','enabled':True,'scope':'dynamics'}})
    calls=[]
    def fetch(url):calls.append(url);return FeedBatch([article(str(len(calls)),dynamic='/dynamic/' in url)],raw_ids=['100'],oldest=NOW)
    result=run_collect(store,p,fetch)
    assert len(calls)==2 and all('offset' not in u for u in calls)
    assert all(r['batches']==1 and r['stop_reason']=='single_batch' for r in result['source_results'])


def test_pool_migration_failure_rolls_back_and_can_retry(tmp_path,monkeypatch):
    import info_agent.pool as pool
    path=tmp_path/'migration.db';p=profile()
    with closing(Store(path)) as store:
        a=dict(article(),sources=[ref('up_uploads')])
        store.run('old',p.context,NOW,'preview',{'profile':p.model_dump()},[a])
        original_json=store.db.execute('SELECT data FROM runs').fetchone()[0]
        with store.db:
            for table in ('pool_items','pool_imports','pool_collections','task_jobs','task_leases','collection_schedule'):store.db.execute('DROP TABLE '+table)
            store.db.execute('PRAGMA user_version=2')
    original=pool.upsert
    def fail(*a,**kw):raise sqlite3.OperationalError('PRIVATE_SENTINEL')
    monkeypatch.setattr(pool,'upsert',fail)
    with pytest.raises(sqlite3.OperationalError):Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='pool_items'").fetchone()
        assert db.execute('SELECT data FROM runs').fetchone()[0]==original_json
    assert list(tmp_path.glob('*.pre-pool-*.sqlite3'))
    monkeypatch.setattr(pool,'upsert',original)
    with closing(Store(path)) as store:
        assert store.db.execute('SELECT count(*) FROM pool_items').fetchone()[0]==1
        assert store.db.execute('SELECT data FROM runs').fetchone()[0]==original_json


def test_service_startup_persists_dead_task_diagnostics_without_consuming_pool(store):
    p=profile();put(store,p,article(),ref('up_uploads'))
    job=task_job('interrupted');jobs.claim(store,'generate',job,NOW)
    store.run('interrupted',p.context,NOW,'collected',dict(profile=p.model_dump(),process_id=-1,metrics={'logical_model_calls':1}))
    with store.db:store.db.execute('UPDATE task_leases SET pid=-1')
    jobs.recover_dead(store,NOW)
    row=store.db.execute("SELECT status,data FROM runs WHERE id='interrupted'").fetchone()
    data=json.loads(row['data'])
    assert row['status']=='failed' and data['error_code']=='PROCESS_INTERRUPTED'
    assert data['metrics']['http_attempts'] is None
    assert jobs.latest(store,'generate')['error_code']=='PROCESS_INTERRUPTED'
    assert snapshot(store,p,NOW)['eligible_count']==1 and not store.db.execute('SELECT * FROM recommendations').fetchone()
    jobs.recover_dead(store,NOW)
    assert store.db.execute('SELECT count(*) FROM runs').fetchone()[0]==1


def test_startup_leaves_live_task_untouched(store):
    p=profile();job=task_job('live');jobs.claim(store,'collect',job,NOW)
    store.run('live',p.context,NOW,'started',dict(profile=p.model_dump(),process_id=os.getpid()))
    jobs.recover_dead(store,NOW)
    assert jobs.latest(store,'collect')['status']=='running'
    assert store.db.execute("SELECT status FROM runs WHERE id='live'").fetchone()[0]=='started'


def test_http_new_and_legacy_endpoints_share_pool_and_never_implicitly_fetch(tmp_path):
    from urllib.request import Request,urlopen
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    calls=[]
    def fetch(url):calls.append(url);return [] if '/dynamic/' in url else [article()]
    model=Model();app=WebApp(tmp_path,clock=lambda:NOW,fetcher=fetch,model=model)
    server=make_server(app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(path,body):
        with urlopen(Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Info-Agent-Token':app.token})) as response:return json.load(response)
    def wait(attr):
        for _ in range(500):
            if getattr(app,attr)['status']!='running':return
            time.sleep(.01)
        pytest.fail('job did not finish')
    try:
        collected=post('/api/collect',{});wait('collection_job')
        assert app.collection_job['run_id']==collected['run_id'] and model.calls==0 and len(calls)==2
        generated=post('/api/generate',{});wait('job')
        assert app.job['run_id']==generated['run_id'] and model.calls==1 and len(calls)==2
        with urlopen(base+'/api/runs/'+generated['run_id']) as response:detail=json.load(response)
        assert detail['operation']=='generate' and detail['metrics']['rss_requests']==0
        assert len(detail['picks'])==1
        post('/api/run',{'preview':True});wait('collection_job')
        assert model.calls==1 and len(calls)==4
        post('/api/run',{'preview':False});wait('job')
        assert model.calls==1 and len(calls)==4
    finally:server.shutdown();server.server_close();thread.join()


def test_inflight_source_is_pending_not_successful_empty(store):
    calls=[]
    def fetch(url):
        calls.append(url)
        if len(calls)==2:
            row=store.db.execute("SELECT data FROM runs WHERE id='collection'").fetchone()
            progress=json.loads(row['data'])
            assert [r['status'] for r in progress['source_results']]==['ok','pending']
            raise TimeoutError('PRIVATE_SENTINEL')
        return [article()]
    result=run_collect(store,profile(),fetch)
    assert [r['status'] for r in result['source_results']]==['ok','failed']


def test_known_local_diagnostic_wins_when_final_job_write_was_unavailable(tmp_path):
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    with closing(app.store()) as store:
        job=task_job('uncertain');jobs.claim(store,'generate',job,NOW)
        with store.db:store.db.execute('DELETE FROM task_leases')
        assert jobs.latest(store,'generate')['error_code']=='UNCLASSIFIED'
    app.job=dict(job,status='failed',error_code='PERSISTENCE_FAILED',error_message='数据库保存失败。')
    assert app.state()['job']['error_code']=='PERSISTENCE_FAILED'
