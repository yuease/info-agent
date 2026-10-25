"""New contracts: all RSS/model/time are injected unless explicitly marked live outside pytest."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from info_agent.schemas import Profile, Article, Choice, Selection
from info_agent.storage import Store, dump
from info_agent.preferences import SHANGHAI, refresh
from info_agent.workflow import build_briefing, prepare
from info_agent.sources import subscriptions
from info_agent.rss import parse_feed, FeedError, classify_error
from info_agent.covers import cover_url
from info_agent.web import WebApp

NOW = datetime(2026,10,24,12,tzinfo=SHANGHAI)


def p(**changes):
    data = dict(rsshub_url='http://localhost:1200',keywords=['a'],interests='Python practical projects')
    data.update(changes)
    return Profile.model_validate(data)


def source_config(search=False, ups=None, following=''):
    return dict(keyword_search=dict(enabled=search),up_uploads=dict(enabled=bool(ups),uids=ups or []),
                following_videos=dict(enabled=bool(following),uid=following))


def a(i,**kw):
    return Article(id=str(i),title='Python project '+str(i),url='https://www.bilibili.com/video/av'+str(i),tags=['python'],**kw).model_dump()


def pick(i):
    return Choice(id=str(i),summary='作者介绍 Python 项目。',reason='与明确兴趣相关',action='查看原来源').model_dump()


@pytest.fixture
def store(tmp_path):
    with closing(Store(tmp_path/'data.sqlite3')) as s:
        yield s


def run(s,profile,fetch,preview=True,model=None):
    return build_briefing(profile.model_dump(),store=s,fetcher=fetch,model=model,preview=preview,clock=lambda:NOW).invoke({})


def test_legacy_profile_and_source_uids():
    assert p().sources.keyword_search.enabled
    assert len(subscriptions(p())) == 1
    config=source_config(ups=[' 123 ','123','987654321098765432109876543210'])
    profile=p(keywords=[],sources=config)
    assert profile.sources.up_uploads.uids == ['123','987654321098765432109876543210']
    assert all('/user/video/' in x['path'] for x in subscriptions(profile))


@pytest.mark.parametrize('sources,keywords',[(source_config(),[]),(source_config(search=True),[]),
    (source_config(ups=['x']),[]),(source_config(ups=['１２３']),[]),(source_config(ups=[123]),[]),
    (source_config(ups=[str(i) for i in range(11)]),[]),(source_config(following='1'*31),[])])
def test_invalid_source_configuration(sources,keywords):
    with pytest.raises(ValueError):
        p(sources=sources,keywords=keywords)


@pytest.mark.parametrize('sources,paths',[
    (source_config(search=True),['/vsearch/']),
    (source_config(ups=['123']),['/user/video/']),
    (source_config(following='456'),['/followings/video/']),
    (source_config(True,['123'],'456'),['/vsearch/','/user/video/','/followings/video/'])])
def test_each_source_and_mixed(store,sources,paths):
    calls=[]
    def fetch(url):
        calls.append(url)
        return [a(1)]
    result=run(store,p(sources=sources,keywords=['a'] if sources['keyword_search']['enabled'] else []),fetch)
    assert len(calls)==len(paths)
    assert all(any(path in url for url in calls) for path in paths)
    assert len(result['articles'])==1
    assert len(result['articles'][0]['sources'])==len(paths)
    assert result['metrics']['logical_model_calls']==0
    assert len(result['source_results'])==len(paths)
    assert result['status']=='preview'


def test_field_merge_and_no_keyword_dependency(store):
    cfg=p(sources=source_config(True,['123'],'456'),max_candidates=1)
    def fetch(url):
        if '/vsearch/' in url:
            return [a(1,author='未知')]
        if '/user/' in url:
            return [dict(a(1,author='UP author',duration_seconds=100),tags=['python','ai'],
                         cover_url='https://i0.hdslb.com/bfs/archive/pic.jpg',excerpt='实际投稿简介')]
        return [a(1)]
    result=run(store,cfg,fetch)
    item=result['articles'][0]
    assert item['author']=='UP author' and item['duration_seconds']==100 and item['cover_url']
    assert item['tags']==['python','ai'] and item['matched_keywords']==['a']
    assert {x['kind'] for x in item['sources']}=={'keyword_search','up_uploads','following_videos'}
    assert item['source_ids']==[]  # provenance is separate from video aliases
    saved=json.loads((store.output/result['run_id']/'candidates.json').read_text(encoding='utf-8'))
    assert saved[0]['cover_url']==item['cover_url']


def test_global_budget_kind_fairness(store):
    cfg=p(keywords=['a','b','c','d','e'],sources=source_config(True,[str(i) for i in range(10)],'456'),max_candidates=6)
    calls=[]
    def fetch(url):
        calls.append(url)
        base=len(calls)*100
        return [a(base+i) for i in range(15)]
    result=run(store,cfg,fetch)
    assert len(calls)==16 and result['metrics']['rss_retries']==0
    assert len(result['articles'])==6
    kinds=[x['sources'][0]['kind'] for x in result['articles']]
    assert kinds.count('keyword_search')==kinds.count('up_uploads')==kinds.count('following_videos')==2


@pytest.mark.parametrize('status,expected', [('auth_required','missing_credentials'),('auth_expired','auth_expired'),('failed','error')])
def test_auth_status_is_result_based_and_cached(store,status,expected):
    cfg=p(keywords=[],sources=source_config(ups=['123'],following='456'))
    assert store.source_status(cfg)[0]['status']=='unknown'
    calls=[]
    def fetch(url):
        calls.append(url)
        if '/followings/' in url: raise FeedError(status)
        return [a(1)]
    result=run(store,cfg,fetch)
    assert len(result['articles'])==1 and len(result['failures'])==1
    assert store.source_status(cfg)[0]['status']==expected
    assert store.source_status(cfg)[0]['status']==expected and len(calls)==2
    changed=cfg.model_dump()
    changed['source_plan']['following']['uid']='789'
    assert store.source_status(Profile.model_validate(changed))[0]['status']=='unknown'


def test_success_empty_all_failed_filtered_empty_are_distinct(store):
    cfg=p(keywords=[],sources=source_config(following='456'))
    result=run(store,cfg,lambda _ : [])
    assert result['empty_reason']=='sources_empty'
    assert result['source_results'][0]['status']=='empty'
    assert store.source_status(cfg)[0]['status']=='ready'  # success, not just a supplied UID
    with pytest.raises(RuntimeError):
        run(store,cfg,lambda _: (_ for _ in ()).throw(FeedError('auth_required')))
    data=json.loads(store.db.execute("SELECT data FROM runs WHERE status='failed'").fetchone()[0])
    assert data['empty_reason']=='all_sources_failed'
    result=run(store,cfg.model_copy(update={'max_duration_seconds':100}),lambda _:[a(1)])
    assert result['empty_reason']=='filtered_empty' and result['metrics']['logical_model_calls']==0


@pytest.mark.parametrize('text,status', [('缺少对应 uid 的 Bilibili 用户登录后的 Cookie 值','auth_required'),
    ('对应 uid 的 Bilibili 用户的 Cookie 已过期','auth_expired'),('connection timed out','failed'),('403 forbidden','failed')])
def test_upstream_error_classification(text,status):
    assert classify_error(text.encode())==status


@pytest.mark.parametrize('url', ['https://evil.example/a.jpg','data:image/png,x','javascript:x',
    'https://i0.hdslb.com.evil.com/bfs/archive/x.jpg','https://evil@i0.hdslb.com/a.jpg',
    'https://i0.hdslb.com:444/a.jpg','file:///x','https://i0.hdslb.com/\\x'])
def test_illegal_covers(url):
    assert cover_url(url) is None
    assert Article.model_validate(dict(a(1),cover_url=url)).cover_url is None


def test_rss_artwork_before_cleaning_and_route_differences():
    raw='''<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <image><url>https://i0.hdslb.com/bfs/face/avatar.jpg</url></image>
      <item><title>UP video</title><link>https://www.bilibili.com/video/BVabc</link>
      <description><![CDATA[<img src="https://i0.hdslb.com/bfs/face/avatar.jpg"><img src="https://i0.hdslb.com/bfs/emote/emoji.png"><img src="http://i1.hdslb.com/bfs/archive/poster.jpg">正文]]></description></item>
      <item><title>Following video</title><link>https://www.bilibili.com/video/av2</link><media:thumbnail url="https://i2.biliimg.com/bfs/archive/2.jpg"/></item>
      <item><title>Unknown</title><link>https://www.bilibili.com/video/av3</link><description><![CDATA[<img src="https://other.org/image.jpg">]]></description></item>
      </channel></rss>'''
    items=parse_feed(raw.encode())
    assert items[0].cover_url=='https://i1.hdslb.com/bfs/archive/poster.jpg'
    assert items[0].excerpt=='正文' and items[0].duration_seconds is None and items[0].tags==[]
    assert items[1].cover_url.endswith('/2.jpg') and items[2].cover_url is None


def test_cover_not_sent_to_model_but_saved_in_recommendation(store):
    class Model:
        calls=0
        def with_structured_output(self,_): return self
        def invoke(self,prompt):
            self.calls+=1
            assert 'cover_url' not in prompt and 'poster.jpg' not in prompt
            assert '作者的主张' in prompt
            return Selection(picks=[Choice.model_validate(pick(1))])
    model=Model()
    result=run(store,p(sources=source_config(ups=['123']),keywords=[]),lambda _:[a(1,cover_url='https://i0.hdslb.com/bfs/archive/poster.jpg')],False,model)
    saved=json.loads(store.db.execute('SELECT data FROM recommendations').fetchone()[0])
    assert saved['article']['cover_url'] and saved['article']['sources'][0]['kind']=='up_uploads'
    assert model.calls==1 and result['metrics']['logical_model_calls']==1


def old_database(path,profiles):
    # Materialize exact v1 tables then simulate pre-upgrade contexts. Original snapshots remain verbatim.
    s=Store(path)
    for i,profile in enumerate(profiles):
        now=NOW-timedelta(days=i)
        data={'profile':profile.model_dump(),'original_marker':i}
        s.run('old'+str(i),profile.legacy_context,now,'completed',data,[a(i)],[pick(i)])
        s.feedback('old'+str(i)+':1','useful','',now)
    s.db.execute('DROP TABLE context_migrations')
    s.db.execute('DROP TABLE source_checks')
    s.db.execute('PRAGMA user_version=1')
    s.db.commit()
    snapshots={r['id']:r['data'] for r in s.db.execute('SELECT id,data FROM runs')}
    s.close()
    return snapshots


def test_stable_migration_backup_rebuild_idempotent_undo_decay(tmp_path):
    path=tmp_path/'old.sqlite3'
    profiles=[p(keywords=[str(i)],max_duration_seconds=100+i) for i in range(3)]
    snapshots=old_database(path,profiles)
    s=Store(path,clock=lambda:NOW)
    assert s.migration_backup.is_file()
    backup=sqlite3.connect(s.migration_backup)
    assert backup.execute('PRAGMA user_version').fetchone()[0]==1
    assert backup.execute('SELECT context FROM runs LIMIT 1').fetchone()[0]==profiles[0].legacy_context
    backup.close()
    assert {r['id']:r['data'] for r in s.db.execute('SELECT id,data FROM runs')}==snapshots
    assert len({r[0] for r in s.db.execute('SELECT context FROM runs')})==1
    assert refresh(s,p(),NOW)[1]['tag:python']>0
    assert s.feedback_stats(p().context)['feedback_count']==3
    event=s.db.execute("SELECT id FROM feedback_events WHERE recommendation='old0:1'").fetchone()[0]
    s.feedback(None,'undo','',NOW,undo=event)
    assert refresh(s,p(),NOW)[1]=={}
    assert refresh(s,p(),NOW+timedelta(days=365))[1]=={}
    s.close()
    s=Store(path,clock=lambda:NOW)
    assert s.migration_backup is None
    assert s.db.execute('SELECT count(*) FROM context_migrations').fetchone()[0]==3
    assert s.db.execute('SELECT count(*) FROM feedback_events').fetchone()[0]==4
    s.close()


def test_migration_different_goals_policy_and_repeat_video(tmp_path):
    path=tmp_path/'old.sqlite3'
    configs=[p(),p(keywords=['b']),p(interests='different goal'),p(learning={'duration_buckets':[100,200]})]
    old_database(path,configs)
    s=Store(path,clock=lambda:NOW)
    assert s.belongs('old0',configs[1]) and s.belongs('old1',configs[0])
    assert not s.belongs('old2',configs[0]) and not s.belongs('old3',configs[0])
    # Same video across source/keyword runs never creates the third independent video.
    s.run('again',p().context,NOW,'completed',{'profile':p().model_dump()},[a(0)],[pick(0)])
    s.feedback('again:1','useful','',NOW)
    assert refresh(s,p(),NOW)[1]=={}
    assert len(s.effective_feedback(p().context))==3
    s.close()


def test_migration_failure_rolls_back(tmp_path,monkeypatch):
    path=tmp_path/'old.sqlite3'
    old_database(path,[p()])
    conn=sqlite3.connect(path)
    conn.execute("CREATE TRIGGER reject_migration BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT,'test'); END")
    conn.commit(); conn.close()
    with pytest.raises(sqlite3.IntegrityError): Store(path)
    conn=sqlite3.connect(path)
    assert conn.execute('SELECT context FROM runs').fetchone()[0]==p().legacy_context
    assert conn.execute('PRAGMA user_version').fetchone()[0]==1
    conn.close()
    assert list(tmp_path.glob('*.pre-stable-*.sqlite3'))


def test_web_contract_and_source_save_feedback_permissions(tmp_path):
    config=p()
    (tmp_path/'briefing_profile.json').write_text(dump(config.model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    with closing(app.store()) as store:
        store.run('before',config.context,NOW,'completed',{'profile':config.model_dump()},[a(1)],[pick(1)])
    before=app.state()
    assert all(before['capabilities'].values()) and before['source_status'][0]['status']=='disabled'
    app.save_profile(dict(expected=before['profile'],changes=dict(keywords=[],sources=source_config(ups=['123']))))
    assert app.profile().context==config.context
    assert app.detail('before')['current_context']
    event=app.feedback(dict(recommendation='before:1',value='useful',key='after-source-switch'))
    assert event['event'] and app.state()['stats']['feedback_count']==1
    app.feedback(dict(event=event['event'],key='undo-after-switch'),undo=True)
    assert app.state()['stats']['feedback_count']==0


def test_unattributed_old_records_are_not_silently_mixed(tmp_path):
    path=tmp_path/'old.sqlite3'
    old_database(path,[p()])
    c=sqlite3.connect(path)
    c.execute("UPDATE runs SET context='unrecognized-context'")
    c.commit(); c.close()
    with closing(Store(path,clock=lambda:NOW)) as s:
        assert s.db.execute('SELECT context FROM runs').fetchone()[0]=='unrecognized-context'
        audit=s.db.execute('SELECT stable_context,reason FROM context_migrations').fetchone()
        assert audit[0] is None and audit[1].startswith('unattributed')
        assert s.feedback_stats(p().context)['feedback_count']==0


def test_old_weights_not_added_and_replacements_survive(tmp_path):
    path=tmp_path/'old.sqlite3'
    configs=[p(keywords=[str(i)]) for i in range(3)]
    old_database(path,configs)
    c=sqlite3.connect(path)
    c.execute('INSERT INTO preference_snapshots(context,created,data) VALUES (?,?,?)',(configs[0].legacy_context,NOW.isoformat(),'{"tag:python":9999}'))
    c.commit(); c.close()
    with closing(Store(path,clock=lambda:NOW)) as s:
        weight=s.preferences(p().context)[1]['tag:python']
        assert 1.5 <= weight <= 3
        event=s.feedback('old0:1','not_useful','',NOW)
        assert refresh(s,p(),NOW)[1]=={}
        s.feedback(None,'undo','',NOW,undo=event)
        assert refresh(s,p(),NOW)[1]['tag:python']>0


def test_source_switch_keeps_history_and_unrelated_tags_not_adjusted(store):
    from info_agent.ranking import bounded_order
    config=p()
    store.run('old',config.context,NOW,'completed',{'profile':config.model_dump()},[a(1)],[pick(1)])
    changed=p(keywords=[],sources=source_config(ups=['123']))
    result=run(store,changed,lambda _:[a(1)])
    assert result['articles']==[] and result['metrics']['history_filtered']==1
    item=dict(a(2),tags=['unrelated'])
    result=bounded_order([pick(2)],[item],{'tag:python':5},changed)
    assert result[0]['adjustment']==0


def test_http_error_diagnostics_sanitized_and_timeout_bounded(monkeypatch):
    import io
    import info_agent.rss as rss
    from urllib.error import HTTPError
    timeouts=[]
    class Opener:
        def open(self,request,timeout):
            timeouts.append(timeout)
            raise HTTPError(request.full_url,503,'error',{},io.BytesIO('缺少对应 uid 的 Bilibili 用户登录后的 Cookie 值 private-MUST-NOT-LEAK'.encode()))
    monkeypatch.setattr(rss,'build_opener',lambda *_:Opener())
    with pytest.raises(FeedError) as error:
        rss.fetch_feed('http://localhost/following',timeout=7)
    assert error.value.status=='auth_required' and 'private' not in str(error.value)
    assert timeouts==[7]


def test_later_on_another_recommendation_does_not_erase_interest(store):
    for i in range(3):
        now=NOW-timedelta(days=i)
        store.run(str(i),p().context,now,'completed',{'profile':p().model_dump()},[a(i)],[pick(i)])
        store.feedback(str(i)+':1','useful','',now)
    _,before=refresh(store,p(),NOW)
    store.run('later',p().context,NOW,'completed',{'profile':p().model_dump()},[a(0)],[pick(0)])
    store.feedback('later:1','later','',NOW)
    assert refresh(store,p(),NOW)[1]==before


def test_old_candidate_bad_cover_sanitized_in_detail(tmp_path):
    (tmp_path/'briefing_profile.json').write_text(dump(p().model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    bad=dict(a(1),cover_url='https://untrusted.example/a.jpg')
    with closing(app.store()) as s:
        s.run('bad-cover',p().context,NOW,'preview',{'articles':[bad]},[bad])
    assert app.detail('bad-cover')['candidates'][0]['cover_url'] is None


def test_new_http_contract_and_csp(tmp_path):
    import threading
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from info_agent.web import make_server
    (tmp_path/'briefing_profile.json').write_text(dump(p().model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    http=make_server(app,0)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{http.server_port}'
    try:
        with urlopen(base+'/api/state') as response:
            state=json.load(response)
            csp=response.headers['Content-Security-Policy']
        assert all(state['capabilities'][k] for k in ('sources_v1','cover_images_v1','stable_preferences_v1'))
        assert 'https://*.hdslb.com' in csp and 'https://*.biliimg.com' in csp and "default-src 'self'" in csp
        headers={'Content-Type':'application/json','X-Info-Agent-Token':state['token']}
        # Old client snapshot, before optional fields existed.
        legacy_expected={k:state['profile'][k] for k in ('rsshub_url','keywords','interests','max_picks')}
        request=Request(base+'/api/profile',data=json.dumps(dict(expected=legacy_expected,changes={'keywords':['new']})).encode(),headers=headers)
        with urlopen(request) as response: assert not json.load(response)['changed_context']
        with urlopen(base+'/api/state') as response: state=json.load(response)
        body=dict(expected=state['profile'],changes=dict(keywords=[],sources=source_config(ups=['123'])))
        with urlopen(Request(base+'/api/profile',data=json.dumps(body).encode(),headers=headers)) as response:
            assert response.status==200
        with closing(app.store()) as s:
            result=run(s,app.profile(),lambda _:[a(1,cover_url='https://i0.hdslb.com/bfs/archive/image.jpg')])
        with urlopen(base+'/api/runs/'+result['run_id']) as response: detail=json.load(response)
        assert detail['source_results'][0]['kind']=='up_uploads'
        assert detail['candidates'][0]['cover_url']
        with urlopen(base+'/api/state') as response: state=json.load(response)
        assert state['runs'][0]['source_labels']==['UP 投稿 · 123']
        body=dict(expected=state['profile'],changes={'cookie':'not-accepted'})
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+'/api/profile',data=json.dumps(body).encode(),headers=headers))
        assert error.value.code==400
    finally:
        http.shutdown(); http.server_close(); thread.join()
