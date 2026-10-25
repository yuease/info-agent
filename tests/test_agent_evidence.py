"""Isolated acceptance for late text and per-source evidence (no RSS/model network)."""
import json
from datetime import timedelta
from contextlib import closing
import pytest
from test_agent_core import env,article,put,ref,Model,NOW,search,query,state_counts
from info_agent.agent_core import AgentCore
from info_agent.storage import Store
from info_agent.core_errors import CoreError
from info_agent.metrics import record_request
from info_agent.text_evidence import normalize,normalized_offsets,hits

TARGET='Codex 重置时间发生变化'


class EvidenceModel(Model):
    def invoke(self,prompt):
        self.calls+=1;self.prompts.append(prompt)
        record_request(None)
        for item in json.loads(prompt.split('\n',1)[1])['items']:
            parts=item['spans']
            for part in parts:
                if TARGET in part['text']:
                    choice={'item_ref':item['item_ref'],'reason':'作者原文提到时间变化','evidence_ref':part['evidence_ref']}
                    return {'evidence':[choice]}
        return {'evidence':[]}


def test_late_evidence_reaches_model_under_existing_budget(env):
    root,p,_,_=env
    put(root,p,article('late',title='更新公告',body='无关背景。'*300+TARGET+'，作者提醒查看原文。'+'后续说明。'*200))
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    assert core.search_items({'profile_id':p.profile_id,'terms':['Codex','重置']}).total_matches==1
    result=query(core)
    assert result.outcome=='found'
    assert TARGET in result.evidence[0].quote and result.input_truncated
    assert model.calls==1


def test_short_source_remains_searchable_after_longer_source_arrives(env):
    root,p,_,core=env
    put(root,p,article('merged',title='更新公告',body=TARGET))
    assert search(core).total_matches==1
    put(root,p,article('merged',title='更新公告',body='较长但无关的说明。'*60,is_dynamic=True,dynamic_id='999'),ref('up_dynamics','123'))
    assert [x.id for x in search(core).items]==['merged']


def payload(model):return json.loads(model.prompts[-1].split('\n',1)[1])


def two_sources(root,p,first=None,second=None):
    a=first or article('merged',title='公告',body=TARGET)
    b=second or article('merged',title='无关公告',body='不相关的较长背景。'*120,published=NOW-timedelta(hours=1),is_dynamic=True,dynamic_id='999')
    a['url']='https://www.bilibili.com/video/BV1example'
    b['url']='https://t.bilibili.com/999'
    b['author']='第二来源作者'
    put(root,p,a);put(root,p,b,ref('up_dynamics','123'))
    return a,b


def test_multiple_hits_aliases_and_overall_prompt_budget(env):
    root,p,_,_=env
    # Both meaningful positions are beyond 1200; unrelated repeated early hits
    # must not crowd the distinct alias group out of the window budget.
    body='Codex 的背景。'*160+'第一处上下文 Codex 额度消息。'+'占位文字。'*250+'第二处上下文 reset 时间说明。'+'结尾。'*100
    for i in range(16):put(root,p,article(str(i),title='长公告',body=body))
    model=Model();core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core)
    assert result.outcome=='found' and result.input_truncated and result.total_matches==16
    data=payload(model)
    assert len(model.prompts[-1])<=p.agent_query.max_input_chars
    assert any('reset 时间说明' in s['text'] for s in data['items'][0]['spans'])
    for item in data['items']:
        assert sum(len(s['text']) for s in item['spans'])<=p.agent_query.max_item_input_chars
        # The stronger v3 prompt counts toward the same global budget. A final
        # small remainder may omit context, and must be marked as truncated.
        if not any('reset 时间说明' in s['text'] for s in item['spans']):assert item['text_truncated']
        for s in item['spans']:
            original=body if s['field']=='body_text' else '长公告'
            assert s['text'] in original
    assert result.metrics.logical_model_calls==1


@pytest.mark.parametrize('prefix,raw,term',[
    ('ﬃ'*850,'ＣＯＤＥＸ','codex'),
    ('ß'*850,'Straße','strasse'),
    ('背景'*850,'e\u0301','é'),
    ('背景'*850,'\u1100\u1161','가'),
])
def test_normalization_expansion_and_composition_keep_original_offsets(prefix,raw,term):
    text=prefix+' '+raw+' 正文'
    folded,mapping=normalized_offsets(text)
    assert folded==normalize(text) and len(mapping)==len(folded)
    matches=hits(text,[[term]])
    assert matches and text[matches[-1][0]:matches[-1][1]]==raw


def test_normalized_late_model_quote_maps_to_original(env):
    root,p,_,_=env;raw='ＣＯＤＥＸ ＲＥＳＥＴ 时间变化'
    body='ﬃ'*1600+' 前置上下文 '+raw+' 后置上下文'
    put(root,p,article('normalized',title='普通公告',body=body))
    class NormalizedModel(Model):
        def invoke(self,prompt):
            self.calls+=1;self.prompts.append(prompt)
            item=json.loads(prompt.split('\n',1)[1])['items'][0]
            part=next(s for s in item['spans'] if raw in s['text'])
            return {'evidence':[{'item_ref':item['item_ref'],'evidence_ref':part['evidence_ref'],'reason':'对应原文'}]}
    model=NormalizedModel()
    core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core)
    assert result.outcome=='found'
    origin=result.evidence[0].quote_evidence
    assert body[origin.start:origin.end]==result.evidence[0].quote and raw in result.evidence[0].quote and origin.field=='body_text'
    assert any('前置上下文' in s['text'] for s in payload(model)['items'][0]['spans'])
    found=search(core).items[0]
    assert raw in found.snippet and found.snippet_evidence.source.key=='personal'


def test_search_model_and_detail_keep_source_link_and_time_together(env):
    root,p,_,_=env;a,b=two_sources(root,p)
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core);assert result.outcome=='found' and result.total_matches==1
    evidence=result.evidence[0];origin=evidence.quote_evidence
    assert origin.source.kind=='keyword_search' and origin.source.url==a['url']
    assert evidence.item.url==a['url'] and evidence.item.published_at==a['published_at']
    assert evidence.item.author==a['author'] and evidence.item.matched_published_at==a['published_at']
    assert len(evidence.item.sources)==2
    assert a['body_text'][origin.start:origin.end]==evidence.quote
    found=search(core).items[0]
    assert found.snippet_evidence.source.url==a['url'] and found.url==a['url']
    detail=core.get_item({'profile_id':p.profile_id,'item_id':'merged','max_body_chars':4000})
    assert TARGET in detail.body and '不相关的较长背景' in detail.body
    assert len(detail.body)<=4000
    by_url={a['url']:a,b['url']:b}
    for part in detail.body_spans:
        text=by_url[part.source.url][part.field]
        assert detail.body[part.body_start:part.body_end]==text[part.start:part.end]


def test_missing_time_not_borrowed_from_other_source(env):
    root,p,_,_=env
    a=article('merged',title='公告',body=TARGET,published=None)
    two_sources(root,p,first=a)
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    result=core.query_updates({'profile_id':p.profile_id,'question':'Codex 重置消息','terms':['Codex','重置'],'request_id':'missing-time'})
    assert result.outcome=='found'
    evidence=result.evidence[0]
    assert evidence.item.published_at is None and evidence.item.matched_published_at is None and evidence.item.time_basis=='missing'
    assert evidence.quote_evidence.source.published_at is None


@pytest.mark.parametrize('excluded',['source','time','type','rule','disabled'])
def test_excluded_contribution_cannot_supply_search_text_model_quote_or_detail(env,excluded):
    root,p,_,_=env
    a=article('merged',title='公告',body=TARGET)
    b=article('merged',title='Codex reset 讨论',body='这里只讨论 Codex reset 的背景。'*80,
        is_dynamic=True,dynamic_id='999',content_type='text')
    options={}
    if excluded=='time':a['published_at']=(NOW-timedelta(days=1)).isoformat();options['time_range']={'period':'today'}
    if excluded=='type':options['content_types']=['text']
    if excluded=='source':options['sources']=[{'kind':'up_dynamics','key':'123'}]
    if excluded=='rule':a['body_text']+=' 立即下单领券购买 https://item.taobao.com/x'
    if excluded=='disabled':
        data=p.model_dump();data['source_plan']['keyword_search']['enabled']=False
        (root/'briefing_profile.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    two_sources(root,p,a,b)
    model=Model({'evidence':[{'item_ref':'I1','reason':'试图借用已排除来源','evidence_ref':'E99'}]})
    core=AgentCore(root,clock=lambda:NOW,model=model)
    request={'profile_id':p.profile_id,'terms':['Codex','重置'],**options}
    found=core.search_items(request)
    assert found.total_matches==1 and TARGET not in found.items[0].snippet
    assert all(s.kind=='up_dynamics' for s in found.items[0].sources)
    result=core.query_updates({**request,'question':'Codex 重置消息'})
    assert result.outcome=='model_failed' and result.error_code=='MODEL_QUOTE_MISMATCH'
    assert all(TARGET not in s['text'] for item in payload(model)['items'] for s in item['spans'])
    exact=core.search_items({**request,'terms':['时间发生变化']})
    assert exact.total_matches==0
    empty=core.query_updates({**request,'terms':['时间发生变化'],'question':'时间发生变化'})
    assert empty.outcome=='no_match' and model.calls==1
    detail=core.get_item({'profile_id':p.profile_id,'item_id':'merged','sources':[{'kind':'up_dynamics','key':'123'}]})
    assert TARGET not in detail.body


@pytest.mark.parametrize('bad',['cross_span','wrong_span','fabricated','not_provided'])
def test_old_freeform_quote_protocol_is_rejected_without_retry(env,bad):
    root,p,_,_=env
    body='Codex 首段文字。'+'甲'*800+'reset 另一段文字。'+'乙'*1000+'这句真实存在但没有交给模型。'
    put(root,p,article('quotes',title='普通公告',body=body))
    class InvalidModel(Model):
        def invoke(self,prompt):
            self.calls+=1;self.prompts.append(prompt)
            spans=json.loads(prompt.split('\n',1)[1])['items'][0]['spans']
            assert len(spans)>1
            if bad=='cross_span':quote='Codex 首段文字。'+'reset 另一段文字。';sid=None
            elif bad=='wrong_span':quote='reset 另一段文字。';sid=next(s['evidence_ref'] for s in spans if 'reset' not in s['text'])
            elif bad=='not_provided':quote='这句真实存在但没有交给模型。';sid=None
            else:quote='模型编造的原文';sid=None
            return {'evidence':[{'id':'quotes','reason':'引用测试','quote':quote,'span_id':sid}]}
    model=InvalidModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core)
    assert result.outcome=='model_failed' and result.error_code=='MODEL_FIELDS' and model.calls==1


def test_new_cache_replay_rehydrates_origin_and_detects_short_source_edits(env):
    root,p,_,_=env;a,b=two_sources(root,p)
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    first=query(core,request_id='origin-first');cached=query(core,request_id='origin-cached')
    assert first.outcome=='found' and cached.metrics.cache_hit and model.calls==1
    put(root,p,a,now=NOW+timedelta(minutes=2))
    replay=query(core,request_id='origin-first')
    assert replay.metrics.replayed and model.calls==1
    assert replay.evidence[0].quote_evidence.source.collected_at==(NOW+timedelta(minutes=2)).isoformat()
    assert replay.evidence[0].quote_evidence.source.url==a['url']
    a['body_text']=TARGET+'，较短来源修订了说明。'
    put(root,p,a)
    updated=query(core,request_id='origin-updated')
    assert not updated.metrics.cache_hit and model.calls==2 and TARGET in updated.evidence[0].quote
    with pytest.raises(CoreError) as error:query(core,request_id='origin-first')
    assert error.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==2


def test_previous_strategy_cache_and_request_are_not_silently_reused(env,monkeypatch):
    import info_agent.agent_core as implementation
    root,p,_,_=env;put(root,p,article('late',title='公告',body='背景。'*700+TARGET))
    class ToggleModel(EvidenceModel):
        legacy=True
        def invoke(self,prompt):
            if self.legacy:self.calls+=1;self.prompts.append(prompt);return {'evidence':[]}
            return super().invoke(prompt)
    model=ToggleModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    current=implementation.PROMPT_VERSION
    monkeypatch.setattr(implementation,'PROMPT_VERSION','agent-evidence-v1')
    assert query(core,request_id='previous').outcome=='insufficient_evidence'
    monkeypatch.setattr(implementation,'PROMPT_VERSION',current);model.legacy=False
    result=query(core,request_id='current')
    assert result.outcome=='found' and not result.metrics.cache_hit and model.calls==2
    with pytest.raises(CoreError) as error:query(core,request_id='previous')
    assert error.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==2
    # Real v1 persisted requests have no strategy_version field at all.
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        with store.db:
            rows=store.db.execute('SELECT namespace,request_key,data FROM agent_requests').fetchall()
            for row in rows:
                data=json.loads(row['data']);data.pop('strategy_version',None)
                store.db.execute('UPDATE agent_requests SET data=? WHERE namespace=? AND request_key=?',(json.dumps(data),row['namespace'],row['request_key']))
    with pytest.raises(CoreError) as error:query(core,request_id='current')
    assert error.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==2


def test_query_and_detail_leave_settings_history_and_learning_unchanged(env):
    root,p,_,_=env;two_sources(root,p)
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:store.save_preferences(p.context,NOW,{'tag:python':2},{})
    before=(root/'briefing_profile.json').read_bytes();counts=state_counts(root)
    def originals():
        with closing(Store(core.db_path,rebuild_preferences=False)) as store:
            return {t:list(map(tuple,store.db.execute('SELECT * FROM '+t))) for t in ('runs','recommendations','feedback_events','preference_snapshots','preference_heads')}
    data=originals()
    search(core);query(core,request_id='safe');query(core,request_id='safe');query(core,request_id='cache')
    core.get_item({'profile_id':p.profile_id,'item_id':'merged'})
    assert before==(root/'briefing_profile.json').read_bytes() and counts==state_counts(root) and data==originals()


def test_small_global_budget_reports_error_without_halving_base_content(env):
    root,p,_,_=env
    body='Codex 第一处。'+'背景。'*600+TARGET+'，有用上下文。'+'后文。'*500
    put(root,p,article('small-budget',title='公告',body=body))
    model=EvidenceModel();core=AgentCore(root,clock=lambda:NOW,model=model,
        policy={'max_input_chars':2000,'max_item_input_chars':1200,'aliases':{'重置':['reset']}})
    with pytest.raises(CoreError) as error:query(core)
    assert error.value.code=='OUTPUT_BUDGET' and error.value.details['phase']=='input_pack'
    assert error.value.details['actual_chars']>2000 and model.calls==0


def test_temporary_refresh_reuses_cache_but_rehydrates_query_ownership(env):
    root,p,_,_=env;model=EvidenceModel()
    core=AgentCore(root,clock=lambda:NOW,model=model,fetcher=lambda _: [article('temp',title='公告',body=TARGET)])
    request=dict(profile_id=p.profile_id,terms=['Codex'],temporary_keywords=['Codex'],collection_mode='temporary_keywords')
    core.search_items({**request,'request_id':'a'})
    first=query(core,request_id='temp-evidence')
    long_id='fresh-'+('x'*90)
    core.search_items({**request,'request_id':long_id})
    cached=query(core,request_id='temp-evidence-cached')
    replay=query(core,request_id='temp-evidence')
    assert first.outcome=='found' and cached.metrics.cache_hit and replay.metrics.replayed and model.calls==1
    assert cached.evidence[0].quote_evidence.source.query_id==long_id
    assert replay.evidence[0].quote_evidence.source.query_id==long_id
    assert all('query_id' not in s['source'] and 'collected_at' not in s['source'] for i in payload(model)['items'] for s in i['spans'])


def test_nearby_hits_with_overlapping_context_are_not_dropped(env):
    root,p,_,_=env
    body='前文。'*500+'Codex 更新原因。'+'间隔'*50+'reset 时间说明。'+'后文。'*100
    put(root,p,article('overlap',title='公告',body=body))
    model=Model();core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core);assert result.outcome=='found'
    spans=payload(model)['items'][0]['spans']
    assert any('Codex' in s['text'] for s in spans) and any('reset' in s['text'] for s in spans)
    assert sum(len(s['text']) for s in spans)<=p.agent_query.max_item_input_chars
    for s in spans:assert s['text'] in (body if s['field']=='body_text' else '公告')
