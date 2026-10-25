"""V3 contract, strict-before-dedup, prompt, and frozen public-content rules."""
import json
from contextlib import closing
from datetime import timedelta
import pytest
from test_agent_core import env,article,put,Model,NOW,state_counts
from info_agent.agent import AgentCore,CoreError
from info_agent.agent_models import QueryResult,EvidenceSelection
from info_agent.storage import Store
from info_agent.filtering import allows,marks,RULES
from info_agent.schemas import Filters,Article
from info_agent.agent_core import PROMPT_VERSION,selection_prompt
from examples.agent_acceptance import load_fixture,seed,run_acceptance


def query(core,**kw):
    request=dict(profile_id=core.profile_id,question='今天有没有 Codex 重置消息',terms=['Codex','重置'])
    request.update(kw);return core.query_updates(request)


@pytest.mark.parametrize('terms',[None,[],['','  ','\t','\u3000']])
@pytest.mark.parametrize('mode',['existing_only','temporary_keywords'])
def test_query_terms_required_before_collection_model_and_request_lease(env,terms,mode):
    root,p,model,core=env
    request=dict(profile_id=p.profile_id,question='今天 Codex 重置',collection_mode=mode,temporary_keywords=['Codex'] if mode=='temporary_keywords' else [])
    if terms is not None:request['terms']=terms
    before=(root/'briefing_profile.json').read_bytes()
    with pytest.raises(CoreError) as error:core.query_updates(request)
    assert error.value.code=='QUERY_TERMS_REQUIRED' and model.calls==0
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        for table in ('agent_requests','temporary_items','temporary_origins'):assert store.db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert (root/'briefing_profile.json').read_bytes()==before


def test_search_without_terms_still_browses_and_does_not_derive_from_question(env):
    root,p,model,core=env;put(root,p,article('browse',title='完全不同内容',body='可浏览文字'))
    result=core.search_items({'profile_id':p.profile_id})
    assert result.total_matches==1 and result.retrieval_terms==[] and model.calls==0


def test_shared_keyword_cleaning_deduplicates_before_limit_and_keeps_order(env):
    root,p,model,core=env;put(root,p,article('one'))
    terms=['  Ｃｏｄｅｘ  ','','CODEX','\u3000','重置',' 重置 ','CoDeX',' reset ','RESET','codex']
    a=core.search_items({'profile_id':p.profile_id,'terms':terms})
    b=query(core,terms=terms)
    assert a.retrieval_terms==b.retrieval_terms==['codex','重置','reset']
    assert b.outcome=='found'


def test_exact_keyword_count_and_length_boundaries_remain_valid(env):
    root,p,model,core=env
    result=core.search_items({'profile_id':p.profile_id,'terms':['a'*100]+[str(i) for i in range(7)]})
    assert len(result.retrieval_terms)==8 and len(result.retrieval_terms[0])==100 and model.calls==0


@pytest.mark.parametrize('terms',[[str(i) for i in range(9)],['x'*101],['ﬃ'*34]])
@pytest.mark.parametrize('method',['search_items','query_updates'])
def test_cleaned_keyword_limits_remain_parameter_errors(env,terms,method):
    root,p,model,core=env
    request={'profile_id':p.profile_id,'terms':terms}
    if method=='query_updates':request['question']='完整原问题'
    with pytest.raises(CoreError) as error:getattr(core,method)(request)
    assert error.value.code=='INVALID_ARGUMENT' and model.calls==0


def test_question_today_and_source_names_never_infer_filters(env):
    root,p,model,core=env;put(root,p,article('past',published=NOW-timedelta(days=8)))
    a=query(core,question='今天 today 只看不存在作者999的图文，Codex 重置？')
    assert a.outcome=='found' and a.coverage.start is None and a.coverage.end is None
    b=query(core,time_range={'period':'today','timezone':'Asia/Shanghai'})
    assert b.outcome=='no_match' and b.coverage.start=='2026-10-25T00:00:00+08:00' and model.calls==1
    assert b.coverage.end=='2026-10-26T00:00:00+08:00'


def test_model_question_is_preserved_while_retrieval_uses_only_supplied_terms(env):
    root,p,model,core=env;put(root,p,article('one'))
    question='找一下，订阅里有没有 Codex 重置的确切原文？'
    result=query(core,question=question)
    sent=json.loads(model.prompts[0].split('\n',1)[1])
    assert sent['question']==question and result.retrieval_terms==['codex','重置']


@pytest.mark.parametrize('different',[False,True])
def test_valid_duplicates_keep_first_and_survive_cache_and_replay(env,different):
    root,p,_,_=env;put(root,p,article('one'))
    first={'item_ref':'I1','reason':'第一条理由','evidence_ref':'E1'}
    second=dict(first,reason='第二条理由',evidence_ref='E2' if different else 'E1')
    model=Model({'evidence':[first,second]});core=AgentCore(root,clock=lambda:NOW,model=model)
    original=query(core,limit=1,request_id='dedup')
    cached=query(core,limit=1,request_id='dedup-cache')
    replay=query(core,limit=1,request_id='dedup')
    assert model.calls==1 and cached.metrics.cache_hit and replay.metrics.replayed
    for result in (original,cached,replay):
        assert result.outcome=='found' and result.duplicates_removed==1 and len(result.evidence)==1
        assert result.evidence[0].quote=='Codex reset 通知' and result.evidence[0].reason=='第一条理由'
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        saved=json.loads(store.db.execute('SELECT data FROM agent_cache').fetchone()[0])
        assert saved['duplicates_removed']==1 and len(saved['choices']['evidence'])==1


def test_raw_more_than_eight_rows_are_counted_by_unique_ids(env):
    root,p,_,_=env;put(root,p,article('one'))
    model=Model({'evidence':[{'item_ref':'I1','reason':'有效','evidence_ref':'E1'} for _ in range(9)]})
    result=query(AgentCore(root,clock=lambda:NOW,model=model),limit=1)
    assert result.outcome=='found' and result.duplicates_removed==8 and model.calls==1


def test_duplicate_first_span_id_is_preserved_in_all_paths(env):
    root,p,_,_=env;put(root,p,article('one'))
    class Choose(Model):
        def invoke(self,prompt):
            self.calls+=1;parts=json.loads(prompt.split('\n',1)[1])['items'][0]['spans'];s=parts[0];self.first=s['text']
            return {'evidence':[{'item_ref':'I1','reason':'first','evidence_ref':s['evidence_ref']},
                {'item_ref':'I1','reason':'second','evidence_ref':s['evidence_ref']}]}
    model=Choose();core=AgentCore(root,clock=lambda:NOW,model=model)
    for key in ('first','cache','first'):
        result=query(core,request_id=key,limit=1)
        assert result.duplicates_removed==1 and result.evidence[0].quote==model.first
        assert result.evidence[0].reason=='first'
    assert model.calls==1


@pytest.mark.parametrize('bad',[{'item_ref':'I1','reason':'bad','evidence_ref':'E99'},
    {'item_ref':'I99','reason':'bad','evidence_ref':'E1'},
    {'item_ref':'I1','reason':'bad','evidence_ref':'E98'}])
def test_duplicate_validation_cannot_hide_invalid_rows(env,bad):
    root,p,_,_=env;put(root,p,article('one'))
    model=Model({'evidence':[{'item_ref':'I1','reason':'first','evidence_ref':'E1'},bad]})
    result=query(AgentCore(root,clock=lambda:NOW,model=model),limit=1)
    assert result.outcome=='model_failed' and result.error_code in ('MODEL_UNKNOWN_IDS','MODEL_QUOTE_MISMATCH')
    assert result.duplicates_removed==0 and model.calls==1


def test_raw_text_budget_is_checked_before_deduplication(env):
    root,p,_,_=env;put(root,p,article('one',body='Codex '+('a'*300)))
    rows=[{'item_ref':'I1','reason':'r'*200,'evidence_ref':'E1'} for _ in range(45)]
    model=Model({'evidence':rows});result=query(AgentCore(root,clock=lambda:NOW,model=model),limit=1)
    assert result.error_code=='MODEL_OUTPUT_BUDGET' and model.calls==1


def test_unique_limit_checked_after_raw_quotes_and_in_original_order(env):
    root,p,_,_=env
    for i in ('a','b'):put(root,p,article(i))
    rows=[{'item_ref':i,'reason':i,'evidence_ref':'E1'} for i in ('I2','I1','I2')]
    model=Model({'evidence':rows});core=AgentCore(root,clock=lambda:NOW,model=model)
    result=query(core,limit=1);assert result.error_code=='MODEL_OUTPUT_BUDGET'
    result=query(core,limit=2);assert [e.item.id for e in result.evidence]==['b','a'] and result.duplicates_removed==1


def test_duplicate_row_field_validation_is_not_relaxed(env):
    root,p,_,_=env;put(root,p,article('one'))
    rows=[{'item_ref':'I1','reason':'first','evidence_ref':'E1'},{'item_ref':'I1','reason':'x'*201,'evidence_ref':'E1'}]
    result=query(AgentCore(root,clock=lambda:NOW,model=Model({'evidence':rows})))
    assert result.error_code=='MODEL_FIELDS'


def test_prompt_contract_only_not_a_real_relevance_accuracy_claim():
    prompt=selection_prompt('完整原问题',[],5)
    for clause in ('候选检索命中不等于应当推荐','只共享品牌名、工具名或泛化主题不足以入选','reason 必须说明',
        '没有涉及问题核心','少于名额或返回空列表','相同条目最多返回一次','evidence_ref','最多200字符 reason',
        '不将预告当成已发布','不将条件性承诺当成无条件结论','不将标题中的作者主张转述为已核实事实',
        '手机上能创建和接管 Codex','购买 Plus 会员即可使用 Codex','不等于付费会员'):
        assert clause in prompt
    assert PROMPT_VERSION=='agent-evidence-v4-content-pack-refs'


def frozen_articles():
    data=load_fixture()
    for case in data['rule_cases']:
        r=next(x for x in data['items'] if x['id']==case['id'])
        yield case['rule'],Article(id=r['id'],title=r['title'],url=r['url'],author=r['author'],published_at=r['published'],
            body_text=r['body'],excerpt=r['body'][:1200],is_dynamic=True,content_type=r['type']).model_dump()


@pytest.mark.parametrize('rule,a',list(frozen_articles()),ids=lambda x:x if isinstance(x,str) else x['id'])
def test_four_frozen_missed_samples_blocked_only_when_own_rule_enabled(rule,a):
    hit=next(h for h in marks(a) if h['id']==rule)
    assert hit['version']==1 and hit['reason'] and RULES[rule].version==1
    assert not allows(a,Filters(rules=[{'id':rule,'version':1,'enabled':True}]))
    assert allows(a,Filters(rules=[{'id':rule,'version':1,'enabled':False}]))


@pytest.mark.parametrize('title,body',[
    ('淘宝闪购新闻','淘宝闪购和美团新增搜888444领券活动。'),
    ('优惠骗局提醒','不要在美団嗖332211领券，这是骗局。'),
    ('抽奖教程','关注并转发本条动态即可参与，随机抽取：1、会员：2位。此为教程示例。'),
    ('抽奖结果公告','关注并转发本条动态即可参与，随机抽取：1、会员：2位。开奖结果如下。'),
    ('品牌讨论','美团与淘宝闪购的普通优惠讨论，没有参与指令。'),
    ('数字记录','888444 是编号，不是购买要求。'),
    ('普通下单说明','copy本段话打开手淘下单，这是普通讨论，没有口令和价格。'),
])
def test_rule_counterexamples_remain_visible(title,body):
    assert not marks(article('negative',title=title,body=body,is_dynamic=True))


def test_long_posts_and_lottery_video_scope_and_body_fallback():
    rule,a=next((r,a) for r,a in frozen_articles() if r=='lottery')
    assert not any(h['id']=='lottery' for h in marks(dict(a,is_dynamic=False)))
    assert any(h['id']=='lottery' for h in marks(dict(a,body_text=None)))
    # Saved body after 1200 chars must be checked, not just the old excerpt.
    assert any(h['id']=='lottery' for h in marks(dict(a,body_text='普通前言。'*300+a['body_text'],excerpt='普通前言。'*240)))
    _,ad=next((r,a) for r,a in frozen_articles() if r=='advertisement')
    assert not any(h['id']=='advertisement' for h in marks(dict(ad,body_text='正文。'*200+ad['body_text'])))


def test_v3_duplicate_default_is_compatible_with_old_json():
    from info_agent.agent_models import Coverage
    result=QueryResult(request_id='old',outcome='no_match',coverage=Coverage(as_of=NOW.isoformat()))
    old=result.model_dump();old.pop('duplicates_removed')
    assert QueryResult.model_validate(old).duplicates_removed==0


def test_frozen_acceptance_default_is_only_retrieval():
    report=run_acceptance()
    assert report['mode']=='retrieval_only' and len(report['cases'])==3
    assert all(c['reference_found'] and 'query' not in c and c['search']['metrics']['logical_model_calls']==0 for c in report['cases'])


def test_q4_probe_exposes_true_negative_without_inventing_snapshot_text(tmp_path):
    data=load_fixture();p,now=seed(tmp_path,data)
    class Recorder(Model):
        def invoke(self,prompt):
            self.calls+=1;self.prompts.append(prompt);return {'evidence':[]}
    model=Recorder();core=AgentCore(tmp_path,clock=lambda:now,model=model)
    case=data['cases'][0];result=core.query_updates(case['model_request'])
    sent=json.loads(model.prompts[0].split('\n',1)[1])
    titles={x['title'] for x in sent['items']}
    assert {x['title'][:300] for x in data['items'] if x['id'] in case['expected_ids']+case['forbidden_ids']}<=titles
    assert result.outcome=='insufficient_evidence'  # semantic success deliberately not simulated
    assert sent['question']==case['request']['question'] and model.calls==1


def test_frozen_harness_injected_calls_are_reported_as_needing_human_review():
    class EmptyModel(Model):
        def invoke(self,prompt):self.calls+=1;return {'evidence':[]}
    model=EmptyModel();report=run_acceptance(online=True,model=model)
    assert report['mode']=='injected_model' and model.calls==3
    assert all(c['manual_review_required'] and c['semantic_verdict']=='requires_human_review' for c in report['cases'])


def test_four_frozen_samples_use_same_query_rules_and_restore_without_deleting(tmp_path):
    data=load_fixture();p,now=seed(tmp_path,data)
    core=AgentCore(tmp_path,clock=lambda:now,model=Model())
    ids={x['id'] for x in data['rule_cases']}
    for identity in ids:
        with pytest.raises(CoreError) as error:core.get_item({'profile_id':p.profile_id,'item_id':identity})
        assert error.value.code=='ITEM_NOT_AVAILABLE'
    profile=p.model_dump()
    for rule in profile['filters']['rules']:rule['enabled']=False
    (tmp_path/'briefing_profile.json').write_text(json.dumps(profile,ensure_ascii=False),encoding='utf-8')
    for identity in ids:assert core.get_item({'profile_id':p.profile_id,'item_id':identity}).id==identity


def test_no_generic_homophone_or_partial_rule_signals():
    cases=['美团搜888444：今天记录网络情况。','外卖红包很优惠，数字888444只是编号。',
        '美團搜888444领券，只有未经配置的同音字。','淘宝闪购可领券，没有搜索密令指令。',
        '复制本段话打开手淘下单，价格16元，但没有口令串。',
        'copy本段话打开手淘下单(abcdef123)，未给出价格或促销信息。',
        '关注并转发动态即可参与，随机抽取：没有列出人数和奖品。']
    for text in cases:assert not marks(article('counter',title='普通记录',body=text,is_dynamic=True)),text


@pytest.mark.parametrize('title,body,rule',[
    ('美团红包','嗖【123456】领券。','advertisement'),
    ('16元优惠','复制本段话打开手淘下单￥abcdef123￥。','advertisement'),
    ('回馈活动','关注并转发动态即可参与；随机抽取\n1、3名：定制会员权益。','lottery'),
    ('回馈活动','随机抽取：\n1、会员福利：3名。\n关注并转发动态即可参与。','lottery'),
])
def test_only_requested_rule_combinations(title,body,rule):
    assert any(h['id']==rule for h in marks(article('combination',title=title,body=body,is_dynamic=True)))


def test_pre_v3_completed_request_is_not_replayed_or_paid_again(env,monkeypatch):
    import info_agent.agent_core as implementation
    root,p,model,core=env;put(root,p,article('one'));version=implementation.PROMPT_VERSION
    monkeypatch.setattr(implementation,'PROMPT_VERSION','agent-evidence-v2-source-spans')
    query(core,request_id='old-version')
    # An actual pre-v3 hash may include uncleaned case/whitespace. It still must
    # be rejected as an old strategy, not charged again or mislabeled as replay.
    with closing(Store(core.db_path,rebuild_preferences=False)) as store:
        with store.db:store.db.execute("UPDATE agent_requests SET payload_hash='legacy-uncleaned-terms'")
    monkeypatch.setattr(implementation,'PROMPT_VERSION',version)
    with pytest.raises(CoreError) as error:query(core,request_id='old-version')
    assert error.value.code=='QUERY_CONTEXT_CHANGED' and model.calls==1
    fresh=query(core,request_id='new-version')
    assert not fresh.metrics.cache_hit and model.calls==2


def test_cli_missing_terms_has_explicit_safe_error_and_help(env,capsys):
    from info_agent.agent_cli import main
    root,p,model,core=env
    result=main(['--root',str(root),'query','今天有没有 Codex？'],core_factory=lambda *a,**kw:core)
    captured=capsys.readouterr();assert result==2 and json.loads(captured.err)['error_code']=='QUERY_TERMS_REQUIRED'
    assert model.calls==0
    with pytest.raises(SystemExit):main(['query','--help'])
    help_text=capsys.readouterr().out;assert 'QUERY_TERMS_REQUIRED' in help_text and '--today' in help_text


def test_query_no_setting_history_feedback_or_learning_changes(env):
    root,p,_,_=env;put(root,p,article('one'));before=(root/'briefing_profile.json').read_bytes();counts=state_counts(root)
    model=Model({'evidence':[{'item_ref':'I1','evidence_ref':'E1','reason':'r'}]*2});core=AgentCore(root,clock=lambda:NOW,model=model)
    query(core,request_id='safe');query(core,request_id='safe');query(core,request_id='cached')
    assert model.calls==1 and counts==state_counts(root) and before==(root/'briefing_profile.json').read_bytes()
