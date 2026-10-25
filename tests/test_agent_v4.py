"""V4 content/ref/budget acceptance. All state isolated, all model outputs constructed.

Prompt assertions and injected selections do NOT establish real semantic accuracy.
"""
import copy
import json
from contextlib import closing

import pytest
from test_agent_core import env,article,put,ref,Model,NOW,query,SECRET
from info_agent.agent_core import AgentCore,selection_prompt,model_json,PROMPT_VERSION
from info_agent.agent_models import QueryUpdatesRequest,QueryResult,Coverage,EvidenceSelection,Usage,Item
from info_agent.core_errors import CoreError
from info_agent.retrieval import retrieve,resolve_range,terms_for
from info_agent.storage import Store
from info_agent.text_evidence import split_interval
from examples.agent_acceptance_v4 import run_acceptance,load_fixture,seed,request_for,FIXTURE


def inspect(core,p,request):
    p=core._profile(p.profile_id)
    r=QueryUpdatesRequest.model_validate(request)
    absolute=resolve_range(r.time_range,r.question,core.clock());_,groups=terms_for(r.question,r.terms,p.agent_query.aliases)
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:entries,_,_=retrieve(store,p,r,absolute,groups)
    return r,entries,core._model_input(p,r,entries,absolute)


def request(core,**kw):
    value=dict(profile_id=core.profile_id,question='原问题',terms=['Codex']);value.update(kw);return value


def choice(i='I1',e='E1',reason='明确构造的测试选择'):
    return dict(item_ref=i,evidence_ref=e,reason=reason)


class AllModel(Model):
    def invoke(self,prompt):
        self.calls+=1;self.prompts.append(prompt)
        items=json.loads(prompt.split('\n',1)[1])['items']
        return dict(evidence=[choice(i['item_ref'],i['spans'][-1]['evidence_ref']) for i in items[:8]])


def test_twelve_frozen_inputs_without_network_and_exact_questions():
    report=run_acceptance();_,questions=load_fixture()
    assert report['mode']=='retrieval_input_only' and len(report['cases'])==12
    for record,original in zip(report['cases'],questions['cases']):
        assert record['reference']==original and record['request']['question']==original['q']
        assert record['request']['terms']==original['terms']
        assert record['search']['metrics']['logical_model_calls']==record['search']['metrics']['rss_requests']==0
        assert 'query' not in record and 'input_error' not in record
        assert record['input_chars']<=18000
        for item in record['input_items']:
            assert sum(len(s['text']) for s in item['spans'])<=1200
    q1=report['cases'][0]
    assert q1['search']['outcome']=='no_match' and q1['search']['coverage']['start']=='2026-10-26T00:00:00+08:00'


@pytest.mark.parametrize('number,required',[(8,['NatsumiSan','ChyetYi']),(12,['流光照影','My Little Garden'])])
def test_real_short_credits_reach_model_from_title_only_hits(number,required):
    case=run_acceptance(numbers=[number])['cases'][0]
    body=[s for i in case['input_items'] for s in i['spans'] if s['field']=='body_text']
    assert body and all(any(word in s['text'] for s in body) for word in required)
    assert all(s['start']==0 and s['end']==len(s['text']) for s in body)
    baseline=json.loads((FIXTURE/'v3-input-baseline.json').read_text(encoding='utf-8'))
    old=next(c for c in baseline['cases'] if c['number']==number)
    assert not any(word in s['text'] for i in old['input_items'] for s in i['spans'] for word in required)


def test_author_only_hits_now_include_two_citable_titles():
    case=run_acceptance(numbers=[11])['cases'][0]
    assert len(case['input_items'])==2
    assert all(any(s['field']=='title' and s['text']==i['title'] for s in i['spans']) for i in case['input_items'])
    assert all(s['field']!='author' and not s['field'].startswith('tags[') for i in case['input_items'] for s in i['spans'])


def test_real_duration_constraints_and_public_fields():
    a=run_acceptance(numbers=[4,9])['cases']
    assert a[0]['input_items'][0]['duration_seconds']==224
    assert '1分58秒' in a[0]['input_items'][0]['title']
    assert {i['duration_seconds'] for i in a[1]['input_items']}=={33,43}
    assert all(i['duration_seconds']<=60 for i in a[1]['search']['items'])
    assert not set(a[1]['reference']['forbidden']) & set(a[1]['input_real_ids'])


def test_actual_model_input_only_short_refs_context_and_server_backfill(env):
    root,p,_,_=env;a=article('real-long-identity-12345');put(root,p,a)
    model=Model({'evidence':[choice(e='E2')]});core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core,max_duration_seconds=700,content_types=['video'])
    data=json.loads(model.prompts[0].split('\n',1)[1]);item=data['items'][0]
    assert 'id' not in item and 'text' not in item and item['item_ref']=='I1'
    assert all(set(s)=={'evidence_ref','field','text','source'} for s in item['spans'])
    assert data['applied_conditions']==dict(max_duration_seconds=700,content_types=['video'],
        absolute_time_range=dict(start='2026-10-25T00:00:00+08:00',end='2026-10-26T00:00:00+08:00',timezone='Asia/Shanghai'))
    assert result.evidence[0].item.id==a['id'] and result.evidence[0].quote==a['body_text']
    assert result.evidence[0].quote_evidence.start==0 and result.evidence[0].quote_evidence.end==len(a['body_text'])
    assert result.evidence[0].item.duration_seconds==core.get_item(dict(profile_id=p.profile_id,item_id=a['id'])).duration_seconds==600


def test_full_300_title_and_600_body_fit_with_split_overlap(env):
    root,p,_,core=env;title='Codex'+('题'*295);body='文'*600
    put(root,p,article('full',title=title,body=body))
    _,_,(items,_)=inspect(core,p,request(core));spans=items[0]['spans']
    assert spans[0]['field']=='title' and spans[0]['text']==title
    parts=[s for s in spans if s['field']=='body_text']
    assert parts[0]['start']==0 and parts[-1]['end']==600
    assert all(len(s['text'])<=400 for s in spans)
    assert 0<=parts[0]['end']-parts[1]['start']<=40
    assert sum(len(s['text']) for s in spans)<=1200
    assert all(body[s['start']:s['end']]==s['text'] for s in parts)


def test_small_item_budget_keeps_true_title_prefix_and_flags(env):
    root,p,_,_=env;put(root,p,article('small',title='Codex'+'题'*400,body='正文'))
    core=AgentCore(root,clock=lambda:NOW,model=Model(),policy={'max_item_input_chars':100})
    _,_,(items,truncated)=inspect(core,p,request(core))
    assert truncated and items[0]['title_truncated'] and items[0]['text_truncated']
    assert len(items[0]['spans'])==1 and items[0]['spans'][0]['text']=='Codex'+'题'*95
    assert items[0]['spans'][0]['end']==100


@pytest.mark.parametrize('body',[None,''])
def test_excerpt_fallback_not_author_or_tag_evidence(env,body):
    root,p,_,core=env;a=article('fallback');a.update(body_text=body,excerpt='这是回退正文',tags=['Codex']);put(root,p,a)
    _,_,(items,_)=inspect(core,p,request(core));item=items[0]
    assert not item['body_missing'] and any(s['field']=='excerpt' and s['text']=='这是回退正文' for s in item['spans'])
    assert not any(s['field']=='author' or s['field'].startswith('tags[') for s in item['spans'])


def test_title_only_body_missing_and_bounded_tag_metadata(env):
    root,p,_,core=env;a=article('empty',body='');a['tags']=['长'*120]*15;put(root,p,a)
    _,_,(items,_)=inspect(core,p,request(core));item=items[0]
    assert item['body_missing'] and item['tags_truncated'] and len(item['tags'])==12
    assert all(len(t)==100 for t in item['tags']) and [s['field'] for s in item['spans']]==['title']


def test_primary_coverage_and_supplement_uncovered_groups_keep_and_semantics(env):
    root,p,_,core=env
    # Stable keyword source loses priority to the dynamic source covering two groups.
    put(root,p,article('merged',title='gamma',body='短来源正文'))
    b=article('merged',title='alpha beta',body='长来源正文',is_dynamic=True,dynamic_id='999');b['author']='动态作者'
    put(root,p,b,ref('up_dynamics','123'))
    _,entries,(items,_)=inspect(core,p,request(core,terms=['alpha','beta','gamma']))
    assert len(entries)==1 and items[0]['author']=='动态作者' and items[0]['spans'][0]['text']=='alpha beta'
    assert any(s['source']['kind']=='keyword_search' and s['text']=='gamma' for s in items[0]['spans'])
    # Fetch timestamps and insertion ordering cannot choose a different primary.
    put(root,p,article('merged',title='gamma',body='短来源正文'),now=NOW.replace(hour=13))
    _,_,(again,_)=inspect(core,p,request(core,terms=['alpha','beta','gamma']))
    assert model_json('q',items)==model_json('q',again)


def test_global_budget_stops_before_next_complete_package(env):
    root,p,_,_=env
    for n in range(4):put(root,p,article(str(n),body='文'*600))
    large=AgentCore(root,clock=lambda:NOW,model=Model())
    r,_,(all_items,_)=inspect(large,p,request(large))
    context=large._request_context(r,resolve_range(r.time_range,r.question,NOW))
    first_size=len(selection_prompt(r.question,all_items[:1],r.limit,context))
    small=AgentCore(root,clock=lambda:NOW,model=Model(),policy={'max_input_chars':first_size+1})
    _,_,(one,truncated)=inspect(small,p,request(small))
    assert len(one)==1 and truncated and one[0]['spans']==all_items[0]['spans']


@pytest.mark.parametrize('rows,error,counts',[
    ([choice('I99')],'MODEL_UNKNOWN_IDS',{'unknown_item_ref_count':1}),
    ([choice(e='E99')],'MODEL_QUOTE_MISMATCH',{'unknown_evidence_ref_count':1,'cross_item_ref_count':0}),
    ([choice('I1','E2')],'MODEL_QUOTE_MISMATCH',{'unknown_evidence_ref_count':0,'cross_item_ref_count':1}),
    ([choice(),choice(e='E99')],'MODEL_QUOTE_MISMATCH',{'unknown_evidence_ref_count':1}),
])
def test_bad_refs_fail_all_before_dedup_and_safe_counts(env,rows,error,counts):
    root,p,_,_=env;put(root,p,article('a',body=''));put(root,p,article('b'))
    model=Model({'evidence':rows});core=AgentCore(root,clock=lambda:NOW,model=model)
    result=core.query_updates(request(core))
    assert result.outcome=='model_failed' and result.error_code==error and not result.evidence and model.calls==1
    for k,v in counts.items():assert result.error_details[k]==v


def test_server_rejects_tampered_offsets_and_excluded_source_mapping(env):
    root,p,_,core=env;put(root,p,article('a'))
    r,entries,(items,_)=inspect(core,p,request(core))
    choices=EvidenceSelection(evidence=[choice()]);allowed={'I1':items[0]}
    for mutation in ('text','offset','source'):
        bad=copy.deepcopy(allowed);s=bad['I1']['spans'][0]
        if mutation=='text':s['text']='编造'
        elif mutation=='offset':s['end']+=1
        else:s['source']['key']='excluded'
        with pytest.raises(CoreError) as error:core._validate_choices(choices,bad,r,p,entries)
        assert error.value.code=='MODEL_QUOTE_MISMATCH'


def test_selection_char_phase_precedes_ref_validation(env):
    root,p,_,_=env;put(root,p,article('one'))
    model=Model({'evidence':[choice('I99',reason='理'*200)]*45})
    result=AgentCore(root,clock=lambda:NOW,model=model).query_updates(request(env[3]))
    assert result.error_code=='MODEL_OUTPUT_BUDGET' and result.error_details['phase']=='selection_chars'
    assert result.error_details['actual_chars']>result.error_details['allowed_chars']


def test_preflight_impossible_response_does_not_call_model_and_replay_keeps_phase(env):
    root,p,_,_=env;put(root,p,article('one',title='Codex'+'题'*1500,body='正文'))
    model=Model();core=AgentCore(root,clock=lambda:NOW,model=model,policy={'max_output_chars':1000})
    for _ in range(2):
        with pytest.raises(CoreError) as error:core.query_updates(request(core,request_id='preflight'))
        assert error.value.details['phase']=='response_pack'
        assert error.value.details['actual_chars']>error.value.details['allowed_chars']==1000
    assert model.calls==0


def test_response_packing_partial_complete_provenance_cache_and_replay(env):
    root,p,_,_=env
    for n in range(8):put(root,p,article(str(n),body='证据正文。'*90))
    class DuplicateAllModel(AllModel):
        def invoke(self,prompt):
            value=super().invoke(prompt);value['evidence'].append(dict(value['evidence'][0],reason='合法重复，保留第一条'))
            return value
    model=DuplicateAllModel();core=AgentCore(root,clock=lambda:NOW,model=model)
    results=[core.query_updates(request(core,request_id=rid)) for rid in ('first','cached','first')]
    assert model.calls==1 and results[1].metrics.cache_hit and results[2].metrics.replayed
    for result in results:
        assert result.outcome=='found' and result.output_truncated
        assert result.duplicates_removed==1
        assert 0<len(result.evidence)<8 and result.evidence_omitted_count==8-len(result.evidence)
        assert len(result.model_dump_json())<=10000
        for e in result.evidence:
            assert e.quote==e.quote_evidence.text and e.quote_evidence.end-e.quote_evidence.start==len(e.quote)
    assert len({(len(r.evidence),r.evidence_omitted_count,r.duplicates_removed) for r in results})==1
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        cached=json.loads(store.db.execute('SELECT data FROM agent_cache').fetchone()[0])
        saved=json.loads(store.db.execute('SELECT data FROM agent_requests ORDER BY rowid').fetchone()[0])
    assert len(cached['choices']['evidence'])==len(saved['choices']['evidence'])==8


def test_output_compression_order_keeps_quote_source_and_offsets(env):
    root,p,_,_=env
    a=article('merge',body='Codex reset 真实正文',is_dynamic=True,dynamic_id='777')
    a['dynamic_contexts']=[dict(id=str(i),url='https://example.com/'+('x'*250),author='转发者',text='上下文'*100) for i in range(4)]
    put(root,p,a);put(root,p,dict(a,title='独立来源标题'),ref('up_dynamics','123'))
    model=Model({'evidence':[choice()]})
    # Find a fixed envelope where removal of contexts is enough, then a lower
    # envelope requiring sources to shrink as well. No production policy changes.
    found=set()
    for cap in range(2000,6001,250):
        core=AgentCore(root,clock=lambda:NOW,model=model,policy={'max_output_chars':cap,'cache_ttl_seconds':0})
        try:result=core.query_updates(request(core))
        except CoreError:continue
        if result.outcome!='found':continue
        e=result.evidence[0]
        if e.item.contexts_truncated and not e.item.dynamic_contexts:
            found.add('contexts')
            if e.item.sources_truncated:
                found.add('sources');assert e.item.sources==[e.quote_evidence.source]
            else:assert len(e.item.sources)==2
        assert e.quote==a['title'] and e.quote_evidence.start==0 and e.quote_evidence.end==len(a['title'])
    assert found=={'contexts','sources'}


def test_oversized_first_selected_row_fails_after_valid_precheck(env):
    root,p,_,_=env
    put(root,p,article('a',title='Codex'+'题'*3000,body='短正文'))
    put(root,p,article('b',body='短正文'))
    model=Model({'evidence':[choice()]});core=AgentCore(root,clock=lambda:NOW,model=model,policy={'max_output_chars':4000})
    results=[core.query_updates(request(core,request_id=rid)) for rid in ('oversized','oversized-cached','oversized')]
    assert model.calls==1
    assert all(result.outcome=='model_failed' and result.error_details['phase']=='response_pack' for result in results)
    assert results[1].metrics.cache_hit and results[2].metrics.replayed


def test_limit_overflow_stays_failure_not_response_truncation(env):
    root,p,_,_=env
    for n in range(2):put(root,p,article(str(n)))
    model=Model({'evidence':[choice('I1'),choice('I2')]});core=AgentCore(root,clock=lambda:NOW,model=model)
    result=core.query_updates(request(core,limit=1))
    assert result.error_code=='MODEL_OUTPUT_BUDGET' and result.error_details['phase']=='selection_count'
    assert result.error_details['actual_count']==2 and not result.output_truncated


def test_unknown_duration_behavior_unchanged(env):
    root,p,_,core=env;a=article('unknown');a['duration_seconds']=None;put(root,p,a)
    assert core.search_items(dict(profile_id=p.profile_id)).items[0].duration_seconds is None
    assert core.search_items(dict(profile_id=p.profile_id,max_duration_seconds=60)).total_matches==0


def test_selected_supplement_source_owns_public_metadata(env):
    root,p,_,_=env;put(root,p,article('same',title='Codex 主来源',body='主正文'))
    a=article('same',title='次来源',body='独立补充正文',is_dynamic=True,dynamic_id='888');a.update(author='补充作者',url='https://t.bilibili.com/888',duration_seconds=33)
    put(root,p,a,ref('up_dynamics','123'))
    class SupplementModel(Model):
        def invoke(self,prompt):
            self.calls+=1;item=json.loads(prompt.split('\n',1)[1])['items'][0]
            s=next(s for s in item['spans'] if s['text']=='独立补充正文')
            return {'evidence':[choice(item['item_ref'],s['evidence_ref'])]}
    result=AgentCore(root,clock=lambda:NOW,model=SupplementModel()).query_updates(request(env[3]))
    e=result.evidence[0]
    assert e.item.title==a['title'] and e.item.author==a['author'] and e.item.url==a['url']
    assert e.quote_evidence.source.kind=='up_dynamics' and e.item.duration_seconds==33


def test_new_fields_have_compatible_defaults_and_prompt_is_only_contract():
    old=QueryResult(request_id='old',outcome='no_match',coverage=Coverage(as_of=NOW.isoformat())).model_dump()
    for k in ('output_truncated','evidence_omitted_count'):old.pop(k)
    result=QueryResult.model_validate(old)
    assert not result.output_truncated and result.evidence_omitted_count==0
    assert Item(id='old',title='标题',snippet='片段',author='作者',url='https://example.com',content_type='video',sources=[]).duration_seconds is None
    prompt=selection_prompt('问题',[],8)
    for clause in ('篮球希罗','影视剪辑或其他游戏','标签是元数据','duration_seconds','实战用时不等于视频时长',
                   '不要无故否认已满足的条件','未提供的视频内容','最多200字符 reason','只输出最终入选列表'):
        assert clause in prompt
    assert PROMPT_VERSION=='agent-evidence-v4-content-pack-refs'


def test_frozen_harness_online_injection_selection_one_call_per_selected_question():
    model=Model({'evidence':[]});report=run_acceptance(online=True,numbers=[8,12],model=model)
    assert model.calls==2 and len(report['cases'])==2 and report['mode']=='injected_model'
    assert report['semantic_verdict']=='requires_human_review'


def test_response_budget_boundary_deterministic_across_request_ids_and_usage(env):
    root,p,_,core=env
    for n in range(3):put(root,p,article(str(n),body='完整正文。'*75))
    r,entries,(inputs,_)=inspect(core,p,request(core,request_id='x'))
    choices=EvidenceSelection(evidence=[choice(i['item_ref'],i['spans'][-1]['evidence_ref']) for i in inputs])
    coverage=Coverage(as_of=NOW.isoformat())
    original=Usage(logical_model_calls=1,http_attempts=1,actual_requests=1,elapsed_seconds=.12,
        tokens={'input_tokens':1234,'output_tokens':50,'total_tokens':1284})
    origin=original.model_dump(exclude={'origin_metrics'})
    roomy=p.model_copy(update={'agent_query':p.agent_query.model_copy(update={'max_output_chars':20000})})
    whole=core._evidence_result(r,roomy,entries,coverage,['codex'],original,[],choices,packing_metrics=origin)
    cap=core._packing_size(whole,origin)-1
    limited=p.model_copy(update={'agent_query':p.agent_query.model_copy(update={'max_output_chars':cap})})
    variants=[original,Usage(cache_hit=True,origin_metrics=origin,elapsed_seconds=.003),
              Usage(replayed=True,origin_metrics=origin,elapsed_seconds=.04)]
    results=[core._evidence_result(r.model_copy(update={'request_id':'x'*(1+49*n)}),limited,entries,coverage,
        ['codex'],usage,[],choices,packing_metrics=origin) for n,usage in enumerate(variants)]
    assert all(x.output_truncated and x.evidence_omitted_count==1 and len(x.evidence)==2 for x in results)
    assert all(len(x.model_dump_json())<=cap for x in results)


def test_long_body_head_overlapping_windows_merge_before_split(env):
    root,p,_,core=env;body='Codex reset '+('上下文。'*170)
    put(root,p,article('merge-head',title='原标题',body=body))
    _,_,(items,_)=inspect(core,p,request(core,terms=['Codex','reset']))
    spans=[s for s in items[0]['spans'] if s['field']=='body_text']
    assert spans[0]['start']==0
    # Prefix and its hits form a single union, not duplicate independent windows.
    assert len(spans)==1 and spans[0]['end']==200
    assert spans[0]['text']==body[:200]


def test_sentence_splitting_and_all_offsets_are_real_contiguous_text():
    text=('第一句话。\n第二句话！第三句话？第四句话；'*40)
    doc=dict(field='body_text',text=text,source=dict(kind='keyword_search',key='x',label='x'))
    pieces=split_interval(doc,0,len(text),80)
    assert pieces[0]['start']==0 and pieces[-1]['end']==len(text)
    for index,s in enumerate(pieces):
        assert s['text']==text[s['start']:s['end']] and len(s['text'])<=80
        if index:
            assert 0<=pieces[index-1]['end']-s['start']<=40
        if index<len(pieces)-1:assert s['text'][-1] in '\n。！？；.!?;'


def test_supplement_prioritizes_new_group_over_more_already_covered_groups(env):
    root,p,_,core=env
    p=p.model_copy(update={'keywords':['personal','other']})
    (root/'briefing_profile.json').write_text(p.model_dump_json(),encoding='utf-8')
    put(root,p,article('multi',title='alpha beta delta',body='正文'*250))
    put(root,p,article('multi',title='alpha beta',body='已有词组正文'),ref('keyword_search','other'))
    put(root,p,article('multi',title='gamma',body='新词组的短来源',is_dynamic=True,dynamic_id='333'),ref('up_dynamics','123'))
    _,_,(items,_)=inspect(core,p,request(core,terms=['alpha','beta','delta','gamma']))
    spans=items[0]['spans']
    assert spans[0]['text']=='alpha beta delta'
    supplements=[s for s in spans if s['source']['key']!='personal']
    assert supplements[0]['text']=='gamma' and supplements[0]['source']['kind']=='up_dynamics'


def test_dynamic_short_context_keeps_source_field_offsets_without_joining(env):
    root,p,_,core=env
    a=article('context',title='Codex 转发',body='转发者正文',is_dynamic=True,dynamic_id='333')
    a['dynamic_contexts']=[dict(text='原作者的独立短上下文',author='原作者',author_uid='456')]
    put(root,p,a,ref('up_dynamics','123'))
    _,_,(items,_)=inspect(core,p,request(core))
    s=items[0]['spans'][-1]
    assert s['field']=='dynamic_contexts[0].text' and s['text']=='原作者的独立短上下文'
    assert s['start']==0 and s['end']==len(s['text']) and s['source']['dynamic_id']=='333'
    assert [s['field'] for s in items[0]['spans'][:2]]==['title','body_text']


def test_invalid_typed_selection_is_not_persisted_as_verified_choices(env):
    root,p,_,_=env;put(root,p,article('one'))
    model=Model({'evidence':[choice('I99',reason=SECRET)]});core=AgentCore(root,clock=lambda:NOW,model=model)
    result=core.query_updates(request(core))
    assert result.error_code=='MODEL_UNKNOWN_IDS' and SECRET not in result.model_dump_json()
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        saved=store.db.execute('SELECT data FROM agent_requests').fetchone()[0]
        assert SECRET not in saved and 'choices' not in json.loads(saved)
        assert not store.db.execute('SELECT * FROM agent_cache').fetchall()


def test_small_snippet_limit_preserves_whole_title_and_short_body_within_default_budget(env):
    root,p,_,_=env;title='Codex'+'题'*295;body='文'*600
    put(root,p,article('many-pieces',title=title,body=body))
    core=AgentCore(root,clock=lambda:NOW,model=Model(),policy={'max_snippet_chars':50})
    _,_,(items,_)=inspect(core,p,request(core));item=items[0]
    assert not item['title_truncated'] and item['title']==title
    assert sum(len(s['text']) for s in item['spans'])<=1200
    assert all(len(s['text'])<=50 for s in item['spans'])
    assert max(s['end'] for s in item['spans'] if s['field']=='body_text')==600
