"""Agent core acceptance: no real RSS, model, user settings, or date dependency."""
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
import pytest
from info_agent.agent_core import AgentCore
from info_agent import agent_models as m
from info_agent.core_errors import CoreError
from info_agent.schemas import Profile,Article,Choice
from info_agent.storage import Store,dump
from info_agent.pool import upsert,snapshot,namespace
from info_agent.preferences import SHANGHAI,refresh
from info_agent.agent_migrations import observation
from info_agent.metrics import record_request

NOW=datetime(2026,10,25,12,tzinfo=SHANGHAI)
SECRET='PRIVATE_QUERY_SENTINEL_key_cookie_response'


def profile(**kw):
    data=dict(rsshub_url='http://127.0.0.1:1200',keywords=['personal'],interests='只关注烹饪',max_candidates=1,max_duration_seconds=1,
        source_plan={'keyword_search':{'enabled':True},'ups':[{'uid':'123','scope':'dynamics'}]},
        agent_query={'aliases':{'重置':['reset'],'二创':['同人','手书']}})
    data.update(kw);return Profile.model_validate(data)


def article(i,title='Codex reset 通知',body='Codex reset quota 重置说明，作者称重置时间已经变化。',published=NOW,**kw):
    return Article(id=i,title=title,body_text=body,excerpt=body[:1200],author='作者',author_uid='123',url='https://www.bilibili.com/video/av'+str(len(i)),
        duration_seconds=600,published_at=published.isoformat() if hasattr(published,'isoformat') else published,**kw).model_dump()


def ref(kind='keyword_search',key='personal'):return dict(kind=kind,key=key,label=kind+' · '+key)


def put(root,p,a,source=None,now=NOW):
    with closing(Store(root/'workspace/info_agent.sqlite3',clock=lambda:NOW)) as store:
        with store.db:
            upsert(store.db,p,source or ref(),a,now)
            observation(store.db,namespace(p),source or ref(),'ok',now,1)


class Model:
    model_name='offline'
    version='1'
    def __init__(self,response=None,block=None):self.calls=0;self.response=response;self.block=block;self.prompts=[]
    def with_structured_output(self,schema,**kw):return self
    def invoke(self,prompt):
        self.calls+=1;self.prompts.append(prompt);record_request(None)
        logging.getLogger('fake.sdk').warning(SECRET)
        if self.block:assert self.block.wait(5)
        if isinstance(self.response,Exception):raise self.response
        data=json.loads(prompt.split('\n',1)[1])
        if self.response is not None:return self.response
        return {'evidence':[{'item_ref':x['item_ref'],'reason':'对应已提供文字','evidence_ref':x['spans'][0]['evidence_ref']} for x in data['items'][:1]]}


@pytest.fixture
def env(tmp_path):
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    model=Model();core=AgentCore(tmp_path,model=model,clock=lambda:NOW,fetcher=lambda _:pytest.fail('unexpected network'))
    return tmp_path,p,model,core


def search(core,**kw):return core.search_items(dict(profile_id=core.profile_id,terms=['Codex'],**kw))
def query(core,**kw):return core.query_updates(dict(profile_id=core.profile_id,question='今天有没有 Codex 重置相关消息',terms=['Codex','重置'],time_range={'period':'today'},**kw))


def state_counts(root):
    with closing(Store(root/'workspace/info_agent.sqlite3',rebuild_preferences=False)) as store:
        return {table:store.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('runs','recommendations','feedback_events','preference_snapshots','preference_heads')}


def test_queries_ignore_personal_interest_cooldown_exploration_and_default_duration(env):
    root,p,model,core=env;put(root,p,article('cooled'))
    with closing(Store(core.db_path)) as store:
        a=article('cooled');pick=Choice(id='cooled',summary='摘要',reason='理由',action='阅读').model_dump()
        store.run('formal',p.context,NOW,'completed',{'profile':p.model_dump()},[a],[pick])
        assert snapshot(store,p,NOW)['eligible_count']==0
    before=state_counts(root)
    result=query(core)
    assert result.outcome=='found' and result.evidence[0].item.id=='cooled'
    assert model.calls==1 and result.metrics.logical_model_calls==1
    assert '烹饪' not in model.prompts[0] and 'exploration' not in model.prompts[0]
    assert state_counts(root)==before
    assert not search(core,max_duration_seconds=10).items


def test_retrieve_before_personal_budget_and_multilingual_terms(env):
    root,p,model,core=env
    for i in range(45):put(root,p,article('filler'+str(i),title='不相关',body='无关内容'))
    put(root,p,article('target'))
    result=query(core)
    assert result.evidence[0].item.id=='target'
    assert result.retrieval_terms==['codex','重置']
    english=core.query_updates(dict(profile_id=p.profile_id,question='Is there any Codex reset news today?',terms=['Codex','reset'],time_range={'period':'today'}))
    assert english.outcome=='found'
    assert english.retrieval_terms==['codex','reset']
    assert model.calls==2


def test_work_character_query_no_unverified_work_association(env):
    root,p,model,core=env
    put(root,p,article('work','某作品 希罗 同人更新','某作品中的希罗手书，作者描述了二创制作过程。'))
    put(root,p,article('other','另一作品 希罗 同人','另一作品的希罗手书。'))
    result=core.query_updates(dict(profile_id=p.profile_id,question='某作品中的希罗今天有没有二创更新',terms=['某作品','希罗','二创'],time_range={'period':'today'}))
    assert result.outcome=='found' and result.evidence[0].item.id=='work' and result.total_matches==1
    assert result.retrieval_terms==['某作品','希罗','二创']


def test_history_query_not_dynamic_ttl_and_explicit_day_bounds(env):
    root,p,model,core=env;past=NOW-timedelta(days=10)
    a=article('old',published=past,is_dynamic=True,dynamic_id='12345',content_type='text')
    put(root,p,a,ref('up_dynamics','123'))
    result=search(core,time_range={'start':'2026-10-15','end':'2026-10-16'})
    assert [x.id for x in result.items]==['old']
    assert result.coverage.start=='2026-10-15T00:00:00+08:00'
    assert not search(core,time_range={'period':'today'}).items
    with closing(Store(core.db_path)) as store:assert snapshot(store,p,NOW)['eligible_count']==0


def test_missing_publication_not_replaced_by_collection_time(env):
    root,p,model,core=env;put(root,p,article('unknown',published=None))
    result=search(core)
    assert result.items[0].published_at is None and result.items[0].time_basis=='missing'
    dated=search(core,time_range={'period':'today'})
    assert dated.outcome=='no_match' and dated.coverage.missing_publication_count==1 and dated.coverage.insufficient


@pytest.mark.parametrize('method', ['search','query','item','status','preferences','update','undo'])
def test_bound_profile_cannot_be_overridden_by_tool_input(env,method):
    root,p,model,core=env
    requests={'search':lambda:core.search_items({'profile_id':'other'}),
      'query':lambda:core.query_updates({'profile_id':'other','question':'Codex','terms':['Codex']}),
      'item':lambda:core.get_item({'profile_id':'other','item_id':'x'}),
      'status':lambda:core.get_source_status({'profile_id':'other'}),
      'preferences':lambda:core.get_preferences({'profile_id':'other'}),
      'update':lambda:core.update_preferences({'profile_id':'other','expected_version':'x','request_id':'x'}),
      'undo':lambda:core.undo_preference_change({'profile_id':'other','change_id':'x'})}
    with pytest.raises(CoreError) as error:requests[method]()
    assert error.value.code=='PROFILE_SCOPE_DENIED'


def test_filtered_get_item_and_disabled_sources_cannot_be_bypassed(env):
    root,p,model,core=env
    a=article('blocked',is_dynamic=True,dynamic_id='9',content_type='text');put(root,p,a,ref('up_dynamics','123'))
    with closing(Store(core.db_path)) as store:
        store.db.execute('UPDATE aliases SET video=video');store.db.commit()
    data=p.model_dump();data['filters']['blocked_ups']=[{'uid':'123','scope':'all'}]
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    assert not search(core).items
    with pytest.raises(CoreError) as e:core.get_item({'profile_id':p.profile_id,'item_id':'blocked'})
    assert e.value.code=='ITEM_NOT_AVAILABLE'
    data['filters']['blocked_ups']=[];data['source_plan']['ups'][0]['enabled']=False
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    assert not search(core,sources=[{'kind':'up_dynamics','key':'123'}]).items
    with pytest.raises(CoreError):core.get_item({'profile_id':p.profile_id,'item_id':'blocked'})


def test_search_length_budget_stable_pages_and_get_item_body_limit(env):
    root,p,model,core=env
    for i in range(12):put(root,p,article(str(i),body='Codex '+('正文'*1000)))
    first=search(core,limit=4)
    second=search(core,limit=4,page=2,snapshot_version=first.snapshot_version)
    assert len(first.items)==4 and first.has_more
    assert not ({x.id for x in first.items}&{x.id for x in second.items})
    assert first.snapshot_version==second.snapshot_version
    item=core.get_item({'profile_id':p.profile_id,'item_id':first.items[0].id,'max_body_chars':30})
    assert len(item.body)==30 and item.truncated and not item.images_analyzed
    put(root,p,article('new'))
    with pytest.raises(CoreError) as e:search(core,limit=4,page=2,snapshot_version=first.snapshot_version)
    assert e.value.code=='PAGINATION_CHANGED'
    assert model.calls==0


def test_no_candidate_zero_model_and_no_hidden_collection(env):
    root,p,model,core=env
    result=query(core)
    assert result.outcome=='no_match' and result.metrics.logical_model_calls==0 and model.calls==0
    assert result.coverage.insufficient and result.coverage.scope=='retained_allowed_data'


def test_temporary_collection_scoped_reusable_and_not_personal(env):
    root,p,model,_=env;calls=[]
    def fetch(url):calls.append(url);return [article('temporary')]
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=fetch)
    before=(root/'briefing_profile.json').read_bytes();counts=state_counts(root)
    result=search(core,collection_mode='temporary_keywords',temporary_keywords=['Codex'],request_id='temp-1')
    assert result.items[0].sources[0].temporary and result.items[0].sources[0].query_id=='temp-1'
    assert result.metrics.rss_requests==1 and model.calls==0
    assert (root/'briefing_profile.json').read_bytes()==before and state_counts(root)==counts
    with closing(Store(core.db_path)) as store:assert snapshot(store,p,NOW)['eligible_count']==0
    assert search(core).items[0].id=='temporary' and len(calls)==1
    other=p.model_copy(update={'profile_id':'other'})
    path=root/'other.json';path.write_text(dump(other.model_dump()),encoding='utf-8')
    foreign=AgentCore(root,profile_path=path,clock=lambda:NOW,model=model)
    assert foreign.search_items({'profile_id':'other','terms':['Codex']}).outcome=='no_match'
    with pytest.raises(CoreError):foreign.get_item({'profile_id':'other','item_id':'temporary'})


def test_temporary_keyword_bounds_failures_and_video_only(env):
    root,p,model,_=env;calls=[]
    def fail(url):calls.append(url);raise TimeoutError(SECRET)
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=fail)
    result=search(core,collection_mode='temporary_keywords',temporary_keywords=['Codex'])
    assert result.outcome=='collection_failed' and result.temporary_video_search_only
    assert result.collection_failures[0]['error_code']=='SOURCE_TIMEOUT'
    statuses=core.get_source_status({'profile_id':p.profile_id}).sources
    assert any(s.kind=='temporary_keyword' and s.availability=='error' for s in statuses)
    with pytest.raises(CoreError):search(core,collection_mode='temporary_keywords',temporary_keywords=['a','b','c','d'])
    assert len(calls)==1


def test_single_logical_selection_cache_and_invalidation(env):
    root,p,model,core=env;put(root,p,article('one'))
    first=query(core,request_id='first');second=query(core,request_id='second')
    assert first.metrics.logical_model_calls==1 and second.metrics.cache_hit and second.metrics.logical_model_calls==0 and model.calls==1
    replay=query(core,request_id='first')
    assert replay.metrics.replayed and replay.metrics.logical_model_calls==0 and model.calls==1
    put(root,p,article('two'))
    third=query(core,request_id='third');assert not third.metrics.cache_hit and model.calls==2
    with pytest.raises(CoreError) as e:query(core,request_id='first')
    assert e.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==2
    data=p.model_dump();data['agent_query']['model_version']='new-model'
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    query(core,request_id='fourth');assert model.calls==3


def test_cache_rule_change_cross_date_and_explicit_constraints(env):
    root,p,model,core=env;put(root,p,article('one'));query(core,request_id='today')
    data=p.model_dump();data['filters']['rules']=[];(root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    assert not query(core,request_id='rules').metrics.cache_hit
    tomorrow=AgentCore(root,clock=lambda:NOW+timedelta(days=1),model=model)
    result=query(tomorrow,request_id='tomorrow');assert result.outcome=='no_match' and model.calls==2
    with pytest.raises(CoreError) as e:query(tomorrow,request_id='today')
    assert e.value.code=='IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('response,code',[
    ({'evidence':[{'item_ref':'I99','reason':'r','evidence_ref':'E1'}]},'MODEL_UNKNOWN_IDS'),
    ({'evidence':[{'item_ref':'I1','reason':'r','evidence_ref':'E99'}]*2},'MODEL_QUOTE_MISMATCH'),
    ({'evidence':[{'item_ref':'I1','reason':'r','evidence_ref':'E99'}]},'MODEL_QUOTE_MISMATCH'),
    ({'evidence':[{'item_ref':'I1','reason':'r','evidence_ref':'E1','url':SECRET}]},'MODEL_FIELDS'),
])
def test_invalid_selection_no_repair_and_no_private_output(env,response,code,caplog):
    root,p,_,_=env;put(root,p,article('one'));model=Model(response)
    core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core)
    assert result.outcome=='model_failed' and result.error_code==code and model.calls==1
    assert SECRET not in result.model_dump_json() and SECRET not in caplog.text
    with sqlite3.connect(core.db_path) as db:
        assert SECRET not in ''.join(x[0] for x in db.execute('SELECT data FROM agent_requests'))
    assert not list((root/'workspace').rglob('run.json'))


def test_evidence_insufficient_and_source_failure_can_coexist_with_results(env):
    root,p,model,core=env;put(root,p,article('one'))
    with closing(Store(core.db_path)) as store:
        with store.db:observation(store.db,namespace(p),ref(),'failed',NOW+timedelta(minutes=1),0,'SOURCE_TIMEOUT')
    result=query(core);assert result.outcome=='found' and result.coverage.insufficient
    status=next(s for s in core.get_source_status({'profile_id':p.profile_id}).sources if s.kind=='keyword_search')
    assert status.last_success_at and status.last_checked_at!=status.last_success_at and status.availability=='partial'
    empty=AgentCore(root,clock=lambda:NOW,model=Model({'evidence':[]}))
    # Different version keeps this deliberate model fixture out of the previous success cache.
    empty.model.version='empty'
    result=query(empty);assert result.outcome=='insufficient_evidence'


def test_timeout_retry_does_not_pay_twice(env):
    root,p,_,_=env;put(root,p,article('one'));gate=threading.Event();model=Model(block=gate)
    core=AgentCore(root,clock=lambda:NOW,model=model)
    try:
        start=time.monotonic();result=query(core,request_id='slow',timeout_seconds=.25)
        assert result.error_code=='QUERY_TIMEOUT' and time.monotonic()-start<1.5 and model.calls==1
        again=query(core,request_id='slow',timeout_seconds=.15)
        assert again.error_code=='QUERY_TIMEOUT' and model.calls==1
    finally:gate.set()
    result=query(core,request_id='slow',timeout_seconds=2)
    assert result.outcome=='found' and model.calls==1 and result.metrics.replayed


def prefs(core):return core.get_preferences({'profile_id':core.profile_id})
def update(core,**kw):
    return core.update_preferences(dict(profile_id=core.profile_id,expected_version=prefs(core).version,request_id=kw.pop('request_id','change-'+str(time.monotonic_ns())),**kw))


def test_incremental_keywords_versions_idempotency_limit_and_promotion(env):
    root,p,model,_=env
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=lambda _:[article('temp')])
    search(core,collection_mode='temporary_keywords',temporary_keywords=['Codex'])
    before=prefs(core)
    request=dict(profile_id=p.profile_id,expected_version=before.version,request_id='add-codex',add_keywords=['Codex'])
    changed=core.update_preferences(request)
    assert changed.after.keywords==['personal','Codex'] and changed.promoted_items==1 and changed.data_ready
    assert core.update_preferences(request).change_id==changed.change_id
    with closing(Store(core.db_path)) as store:
        assert store.db.execute("SELECT count(*) FROM pool_items WHERE kind='keyword_search' AND source_key='Codex'").fetchone()[0]==1
    with pytest.raises(CoreError) as e:core.update_preferences(dict(request,add_keywords=['other']))
    assert e.value.code=='IDEMPOTENCY_CONFLICT'
    with pytest.raises(CoreError) as e:core.update_preferences(dict(profile_id=p.profile_id,expected_version=before.version,request_id='stale',add_keywords=['x']))
    assert e.value.code=='SETTINGS_CONFLICT'
    with pytest.raises(CoreError) as e:update(core,add_keywords=['1','2','3','4'])
    assert e.value.code=='SETTINGS_INVALID'
    missing=update(core,add_keywords=['NoData'])
    assert missing.needs_collection and not missing.data_ready and missing.next_action=='refresh_collection'
    removed=update(core,remove_keywords=['NoData']);assert removed.after.keywords==['personal','Codex']


def test_same_goal_switch_and_undo_keep_learning_and_unrelated_edits(env):
    root,p,model,core=env
    with closing(Store(core.db_path)) as store:
        old_version,_=store.save_preferences(p.context,NOW,{'tag:python':2},{})
    same=update(core,interests='同一目标更详细的说明',interest_mode='same_goal')
    assert same.before.learning_context==same.after.learning_context==p.context
    assert same.before.goal_id==same.after.goal_id
    another=update(core,add_keywords=['extra'])
    undo=core.undo_preference_change({'profile_id':p.profile_id,'change_id':same.change_id})
    assert undo.after.interests==p.interests and undo.after.keywords==['personal','extra']
    assert core.undo_preference_change({'profile_id':p.profile_id,'change_id':same.change_id}).replayed
    switch=update(core,interests=p.interests,interest_mode='switch_goal')
    assert switch.after.learning_context!=p.context and switch.after.goal_id!=same.after.goal_id
    with closing(Store(core.db_path)) as store:
        assert store.preferences(switch.after.learning_context)==(None,{}) and store.preferences(p.context)[0]==old_version
    restored=core.undo_preference_change({'profile_id':p.profile_id,'change_id':switch.change_id})
    assert restored.after.learning_context==p.context


def test_undo_conflicts_and_sequential_undo_not_old_file_overwrite(env):
    root,p,model,core=env
    first=update(core,add_keywords=['a']);second=update(core,add_keywords=['b'])
    with pytest.raises(CoreError) as e:core.undo_preference_change({'profile_id':p.profile_id,'change_id':first.change_id})
    assert e.value.code=='PREFERENCE_UNDO_CONFLICT'
    core.undo_preference_change({'profile_id':p.profile_id,'change_id':second.change_id})
    core.undo_preference_change({'profile_id':p.profile_id,'change_id':first.change_id})
    assert prefs(core).keywords==['personal']


def test_settings_private_fields_rejected_and_queries_do_not_change_config(env):
    root,p,model,core=env;before=(root/'briefing_profile.json').read_bytes();put(root,p,article('one'))
    search(core);query(core);core.get_item({'profile_id':p.profile_id,'item_id':'one'})
    assert (root/'briefing_profile.json').read_bytes()==before
    with pytest.raises(CoreError) as e:core.update_preferences({'profile_id':p.profile_id,'expected_version':prefs(core).version,'request_id':'bad','rsshub_url':SECRET})
    assert e.value.code=='INVALID_ARGUMENT' and SECRET not in str(e.value)


def test_durable_setting_write_failure_and_recovery(env,monkeypatch):
    import info_agent.settings_service as service
    root,p,model,core=env;original=service.atomic_write;before=prefs(core)
    def fail(*a,**kw):raise OSError(SECRET)
    monkeypatch.setattr(service,'atomic_write',fail)
    request={'profile_id':p.profile_id,'expected_version':before.version,'request_id':'durable','add_keywords':['new']}
    with pytest.raises(CoreError) as e:core.update_preferences(request)
    assert e.value.code=='SETTINGS_IO' and SECRET not in str(e.value)
    assert json.loads((root/'briefing_profile.json').read_text(encoding='utf-8'))['keywords']==['personal']
    monkeypatch.setattr(service,'atomic_write',original)
    assert prefs(core).keywords==['personal','new']
    assert core.update_preferences(request).replayed
    with closing(Store(core.db_path)) as store:assert store.db.execute('SELECT count(*) FROM setting_changes').fetchone()[0]==1


@pytest.mark.parametrize('text,expected',[
    ('淘宝闪购发布技术新闻，讨论分布式系统。',False),('转转的服务架构使用缓存。',False),
    ('美团优惠券广告分析：不要点击链接购买。',False),
    ('立即下单，点击链接领券，到手价9元 https://item.taobao.com/item.htm?id=1',True),
    ('限时促销，领券购买，商家 https://www.meituan.com/coupon',True),
    ('这个功能很好用，顺便提一下转转品牌。',False),
    ('教程：这类“立即下单领券购买 https://item.taobao.com/x”广告如何识别。',False),
])
def test_conservative_advertisement_rule(text,expected):
    from info_agent.filtering import marks
    a=article('ad',title=text,body=text)
    assert any(h['id']=='advertisement' for h in marks(a))==expected


def test_ad_rule_shared_by_personal_query_and_restore(env):
    root,p,model,core=env
    a=article('ad',body='Codex 限时促销，立即下单领券购买 https://item.taobao.com/x')
    put(root,p,a)
    assert not search(core).items
    with pytest.raises(CoreError):core.get_item({'profile_id':p.profile_id,'item_id':'ad'})
    data=p.model_dump();data['filters']['rules']=[{'id':'lottery','enabled':True}]
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    assert search(core).items[0].id=='ad'


@pytest.mark.parametrize('url',[
    'https://example.org/?merchant=taobao.com','https://taobao.com.evil.example/buy',
    'https://taobao.com@evil.example/buy','https://example.org/meituan.com/coupon'])
def test_ad_requires_real_merchant_hostname(url):
    from info_agent.filtering import marks
    assert not any(h['id']=='advertisement' for h in marks(article('x',body='立即下单领券购买 '+url)))


def test_model_candidate_and_input_budgets_after_retrieval(env):
    root,p,model,core=env
    for i in range(40):put(root,p,article('entry'+str(i),body='Codex '+('长正文'*1000)))
    result=query(core)
    sent=json.loads(model.prompts[0].split('\n',1)[1])['items']
    assert 0<len(sent)<=30 and result.total_matches==40 and result.input_truncated
    assert all(sum(len(s['text']) for s in x['spans'])<=1200 for x in sent) and len(model.prompts[0])<=p.agent_query.max_input_chars
    assert len(result.model_dump_json())<=p.agent_query.max_output_chars and model.calls==1


def test_model_output_limit_is_not_silently_truncated(env):
    root,p,_,_=env
    for i in range(2):put(root,p,article(str(i)))
    model=Model({'evidence':[{'item_ref':'I'+str(i+1),'reason':'r','evidence_ref':'E1'} for i in range(2)]})
    result=query(AgentCore(root,clock=lambda:NOW,model=model),limit=1)
    assert result.error_code=='MODEL_OUTPUT_BUDGET' and result.error_details['actual_count']==2 and model.calls==1


@pytest.mark.parametrize('parsing', [True,False])
def test_production_structured_output_usage_and_safe_parse_failure(env,monkeypatch,parsing):
    import sys,types
    from langchain_core.exceptions import OutputParserException
    root,p,_,_=env;put(root,p,article('one'))
    class Production(Model):
        def with_structured_output(self,schema,**kw):assert kw=={'include_raw':True};return self
        def invoke(self,prompt):
            self.calls+=1;record_request(None)
            return dict(parsed=None if parsing else {'evidence':[{'item_ref':'I1','reason':'r','evidence_ref':'E1'}]},
                raw=types.SimpleNamespace(usage_metadata={'input_tokens':23,'output_tokens':9,'total_tokens':32}),
                parsing_error=OutputParserException(SECRET,llm_output=SECRET) if parsing else None)
    model=Production()
    monkeypatch.setitem(sys.modules,'info_agent.llm',types.SimpleNamespace(get_model=lambda:model))
    core=AgentCore(root,clock=lambda:NOW);monkeypatch.setattr(core,'_model_identity',lambda p:['fake-production'])
    result=query(core)
    assert result.metrics.tokens['total_tokens']==32 and result.metrics.http_attempts==1 and model.calls==1
    assert result.error_code=='MODEL_PARSE' if parsing else result.outcome=='found'
    assert SECRET not in result.model_dump_json()


def test_queries_do_not_implicitly_create_interest_or_feedback(env):
    root,p,model,core=env;put(root,p,article('one'));before=state_counts(root)
    search(core);query(core);core.get_item({'profile_id':p.profile_id,'item_id':'one'})
    assert state_counts(root)==before


def test_only_matching_temporary_keyword_is_promoted(env):
    root,p,model,_=env
    def fetch(url):return [article('codex' if '/Codex/' in url else 'other')]
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=fetch)
    search(core,collection_mode='temporary_keywords',temporary_keywords=['Codex','other'])
    v=prefs(core)
    result=core.update_preferences(dict(profile_id=p.profile_id,request_id='promote-one',expected_version=v.version,add_keywords=['Codex']))
    assert result.promoted_items==1 and result.data_ready and not result.needs_collection
    with closing(Store(core.db_path)) as store:
        assert [r[0] for r in store.db.execute('SELECT video FROM pool_items')]==['codex']
        assert store.db.execute('SELECT count(*) FROM temporary_items').fetchone()[0]==2


def test_disabled_keyword_does_not_enable_on_agent_update(env):
    root,p,model,core=env;data=p.model_dump();data['source_plan']['keyword_search']['enabled']=False
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8');v=prefs(core)
    result=core.update_preferences(dict(profile_id=p.profile_id,request_id='disabled',expected_version=v.version,add_keywords=['Codex']))
    assert not result.after.keyword_search_enabled and result.next_action=='enable_keyword_search'


def test_same_goal_web_and_agent_share_identity_and_audit(env):
    from info_agent.web import WebApp
    root,p,model,core=env;app=WebApp(root=root,clock=lambda:NOW)
    before=app.profile()
    response=app.save_profile(dict(expected=before.model_dump(),changes={'interests':'烹饪技术'},interest_mode='same_goal',request_id='web-refine'))
    assert not response['changed_context'] and response['change_id']
    current=prefs(core);assert current.interests=='烹饪技术' and current.learning_context==p.context
    result=core.undo_preference_change(dict(profile_id=p.profile_id,change_id=response['change_id']))
    assert result.after.interests==p.interests and result.after.learning_context==p.context
    response=app.save_profile(dict(expected=app.profile().model_dump(),changes={'interests':'新目标'},interest_mode='switch_goal',request_id='web-switch'))
    assert response['changed_context'] and prefs(core).learning_context!=p.context


def test_settings_recovery_after_file_write_before_database_commit(env,monkeypatch):
    import info_agent.settings_service as settings
    root,p,model,core=env;original=settings._finalize;v=prefs(core)
    def interrupted(store,row):
        original(store,row)
        raise sqlite3.OperationalError(SECRET)
    monkeypatch.setattr(settings,'_finalize',interrupted)
    request=dict(profile_id=p.profile_id,expected_version=v.version,request_id='file-committed',add_keywords=['Codex'])
    with pytest.raises(CoreError) as e:core.update_preferences(request)
    assert e.value.code=='SETTINGS_IO' and SECRET not in str(e.value)
    assert 'Codex' in json.loads((root/'briefing_profile.json').read_text(encoding='utf-8'))['keywords']
    monkeypatch.setattr(settings,'_finalize',original)
    assert core.update_preferences(request).replayed
    with closing(Store(core.db_path)) as store:
        assert store.db.execute("SELECT count(*) FROM setting_changes WHERE status='committed'").fetchone()[0]==1


def test_pending_settings_do_not_overwrite_external_change(env,monkeypatch):
    import info_agent.settings_service as settings
    root,p,model,core=env;original=settings.atomic_write;v=prefs(core)
    monkeypatch.setattr(settings,'atomic_write',lambda *a:(_ for _ in ()).throw(OSError(SECRET)))
    request=dict(profile_id=p.profile_id,expected_version=v.version,request_id='external',add_keywords=['Codex'])
    with pytest.raises(CoreError):core.update_preferences(request)
    data=p.model_dump();data['keywords']=['external'];(root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    monkeypatch.setattr(settings,'atomic_write',original)
    assert prefs(core).keywords==['external']
    with pytest.raises(CoreError) as e:core.update_preferences(request)
    assert e.value.code=='SETTINGS_RECOVERY_CONFLICT'


def test_public_database_error_is_safe(env,monkeypatch):
    import info_agent.agent_core as implementation
    root,p,model,core=env
    monkeypatch.setattr(implementation,'Store',lambda *a,**kw:(_ for _ in ()).throw(sqlite3.OperationalError(SECRET)))
    with pytest.raises(CoreError) as e:search(core)
    assert e.value.code=='CORE_STORAGE_FAILED' and SECRET not in str(e.value)


def concurrent_query_worker(root,start,result):
    class SharedModel(Model):
        def invoke(self,prompt):
            with sqlite3.connect(Path(root)/'cost.sqlite3') as db:db.execute('INSERT INTO calls VALUES(1)');db.commit()
            time.sleep(.3)
            return super().invoke(prompt)
    core=AgentCore(root,clock=lambda:NOW,model=SharedModel());start.wait(10)
    try:
        value=query(core,request_id='shared-process');result.put((value.outcome,value.metrics.logical_model_calls,value.metrics.replayed))
    except CoreError as e:result.put(e.code)


def test_multi_process_idempotency_one_paid_request(env):
    root,p,model,core=env;put(root,p,article('one'))
    with sqlite3.connect(root/'cost.sqlite3') as db:db.execute('CREATE TABLE calls(n INTEGER)')
    ctx=multiprocessing.get_context('spawn');start=ctx.Event();results=ctx.Queue()
    processes=[ctx.Process(target=concurrent_query_worker,args=(str(root),start,results)) for _ in range(2)]
    try:
        for process in processes:process.start()
        start.set();values=[results.get(timeout=25) for _ in processes]
        for process in processes:process.join(10);assert process.exitcode==0
        assert all(v[0]=='found' for v in values) and sum(v[1] for v in values)==1
        with sqlite3.connect(root/'cost.sqlite3') as db:assert db.execute('SELECT count(*) FROM calls').fetchone()[0]==1
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join()


def test_dead_process_request_never_automatically_retries(env):
    from info_agent.retrieval import hash_data
    root,p,model,core=env;put(root,p,article('one'));query(core,request_id='dead')
    with sqlite3.connect(core.db_path) as db:
        db.execute("UPDATE agent_requests SET status='running',pid=-1 WHERE request_key=?",(hash_data('dead'),));db.commit()
    with pytest.raises(CoreError) as e:query(core,request_id='dead')
    assert e.value.code=='QUERY_OUTCOME_UNKNOWN' and model.calls==1


def test_v4_migration_backs_up_and_preserves_feedback_contexts(env):
    root,p,model,core=env;put(root,p,article('one'))
    with closing(Store(core.db_path)) as store:
        store.run('legacy',p.context,NOW,'completed',{'profile':p.model_dump()})
        store.run('unknown','unattributable',NOW,'failed',{'profile':p.model_dump()})
        tables=[r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0].startswith(('agent_','temporary_','source_observations','setting_','interest_'))]
        with store.db:
            for t in tables:store.db.execute('DROP TABLE '+t)
            store.db.execute('PRAGMA user_version=3')
        before={t:list(map(tuple,store.db.execute('SELECT * FROM '+t))) for t in ('runs','recommendations','feedback_events','preference_snapshots','preference_heads')}
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        assert store.migration_backup and store.migration_backup.exists()
        after={t:list(map(tuple,store.db.execute('SELECT * FROM '+t))) for t in before}
        assert before==after
        audit=list(store.db.execute('SELECT * FROM interest_attributions ORDER BY run'))
        assert audit[0]['goal_id']=='legacy-'+p.context and audit[1]['goal_id'] is None
        assert store.db.execute('PRAGMA user_version').fetchone()[0]==4
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        assert store.migration_backup is None and store.db.execute('SELECT count(*) FROM interest_attributions').fetchone()[0]==2


def test_json_cli_all_interfaces_without_web(env,capsys,tmp_path):
    from info_agent.agent_cli import main
    root,p,model,core=env;put(root,p,article('one'))
    def run(*args):
        status=main(['--root',str(root),*args],core_factory=lambda *a,**kw:core)
        captured=capsys.readouterr();assert status==0,captured.err
        return json.loads(captured.out)
    assert run('search','--term','Codex')['items'][0]['id']=='one'
    assert run('query','今天有没有 Codex 重置相关消息','--term','Codex','--term','重置','--today','--request-id','cli-query')['outcome']=='found'
    assert run('item','one','--max-body-chars','10')['truncated']
    assert run('sources')['sources']
    v=run('preferences')
    change=run('update','--expected-version',v['version'],'--request-id','cli-update','--add-keyword','Codex')
    assert change['after']['keywords']==['personal','Codex']
    assert run('undo',change['change_id'])['after']['keywords']==['personal']
    request_file=tmp_path/'request.json';request_file.write_text(dump({'profile_id':p.profile_id,'terms':['Codex']}),encoding='utf-8')
    assert run('search','--request-file',str(request_file))['items']
    assert model.calls==1


def test_interest_revision_preserves_learning_and_old_feedback_access(env):
    root,p,model,core=env
    with closing(Store(core.db_path)) as store:
        store.run('old-briefing',p.context,NOW,'completed',{'profile':p.model_dump()})
        version,_=store.save_preferences(p.context,NOW,{'tag:python':1.0},{})
    first=core.update_preferences(dict(profile_id=p.profile_id,request_id='refine',expected_version=prefs(core).version,
        interests='烹饪与食谱技术',interest_mode='same_goal'))
    current=core._profile(p.profile_id)
    with closing(Store(core.db_path)) as store:
        assert store.belongs('old-briefing',current) and store.preferences(current.context)[0]==version
    second=core.update_preferences(dict(profile_id=p.profile_id,request_id='switch',expected_version=first.after.version,
        interests='游戏二创',interest_mode='switch_goal'))
    with closing(Store(core.db_path)) as store:
        assert not store.belongs('old-briefing',core._profile(p.profile_id)) and store.preferences(second.after.learning_context)[0] is None
    core.undo_preference_change(dict(profile_id=p.profile_id,change_id=second.change_id))
    with closing(Store(core.db_path)) as store:assert store.belongs('old-briefing',core._profile(p.profile_id)) and store.preferences(p.context)[0]==version


def test_cache_invalidates_content_and_alias_changes(env):
    root,p,model,core=env;put(root,p,article('one'));query(core)
    put(root,p,article('one',body='Codex reset 重置说明已修订，作者更正了先前说法。'))
    assert not query(core).metrics.cache_hit and model.calls==2
    data=p.model_dump();data['agent_query']['aliases']['重置'].append('配额恢复')
    (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
    assert not query(core).metrics.cache_hit and model.calls==3


def test_partial_temporary_failure_still_returns_evidence(env):
    root,p,model,_=env
    def fetch(url):
        if '/bad/' in url:raise TimeoutError(SECRET)
        return [article('good')]
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=fetch)
    result=query(core,collection_mode='temporary_keywords',temporary_keywords=['Codex','bad'])
    assert result.outcome=='found' and result.coverage.insufficient and result.collection_failures[0]['error_code']=='SOURCE_TIMEOUT'
    assert result.metrics.rss_requests==2 and result.metrics.logical_model_calls==1 and SECRET not in result.model_dump_json()


def test_raw_search_does_not_initialize_model_factory(env,monkeypatch):
    root,p,model,core=env;put(root,p,article('one'))
    monkeypatch.setattr(core,'_model_identity',lambda *a:pytest.fail('search must not inspect model configuration'))
    assert search(core).outcome=='found' and model.calls==0


def test_normalized_question_and_terms_share_cache(env):
    root,p,model,core=env;put(root,p,article('one'))
    a=core.query_updates(dict(profile_id=p.profile_id,question='Codex reset',terms=['Codex'],request_id='normalized-a'))
    b=core.query_updates(dict(profile_id=p.profile_id,question='  CODEX RESET  ',terms=['codex'],request_id='normalized-b'))
    assert a.outcome=='found' and b.metrics.cache_hit and model.calls==1


@pytest.mark.parametrize('body',[
    '有用的工作经验。'*70+'立即下单领券购买 https://item.taobao.com/x',
    '个人实践方法与心得。'*30+'立即下单领券购买 https://item.taobao.com/x',
    '别买这些商品，不需要购买。立即下单领券购买 https://item.taobao.com/x',
])
def test_ad_rule_keeps_substantive_posts_and_negative_context(body):
    from info_agent.filtering import marks
    assert not any(h['id']=='advertisement' for h in marks(article('mixed',title='工作与生活记录',body=body)))


def test_retained_rss_body_has_explicit_truncation_and_no_heat_statistics():
    from info_agent.rss import parse_feed
    xml='<rss><channel><item><title>Codex</title><link>https://www.bilibili.com/video/av123</link><description><![CDATA[Play: 999 Comment: 80<br><p>'+('正文'*12000)+'</p>]]></description></item></channel></rss>'
    a=parse_feed(xml.encode('utf-8'))[0]
    assert len(a.body_text)==20000 and a.body_truncated and len(a.excerpt)==1200 and 'Play:' not in a.body_text


def test_source_access_change_during_raw_search_fails_closed(env,monkeypatch):
    root,p,model,core=env;put(root,p,article('one'));original=core._lookup
    def changed(*args,**kw):
        result=original(*args,**kw)
        data=p.model_dump();data['filters']['blocked_ups']=[{'uid':'123','scope':'all'}]
        (root/'briefing_profile.json').write_text(dump(data),encoding='utf-8')
        return result
    monkeypatch.setattr(core,'_lookup',changed)
    with pytest.raises(CoreError) as e:search(core)
    assert e.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==0
