import json
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from info_agent.schemas import Article, Choice, Profile, Selection
from info_agent.rss import parse_feed, description_fields
from info_agent.storage import Store
from info_agent.workflow import build_briefing, prepare
from info_agent.preferences import refresh, refresh_safe, SHANGHAI
from info_agent.ranking import finalize, bounded_order, exploration_slots

NOW = datetime(2026, 10, 24, 12, tzinfo=SHANGHAI)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / 'test.db')
    yield db
    db.close()


def profile(**kw):
    return Profile.model_validate(dict(rsshub_url='http://localhost:1200', keywords=['a','b'], interests='practical', **kw))


def article(i, **kw):
    return Article(id=str(i), title=f'Video {i}', url=f'https://example.org/{i}', tags=['python'], **kw).model_dump()


def choice(i, **kw):
    return Choice(id=str(i), summary='摘要', reason='理由', action='阅读', **kw)


class Model:
    def __init__(self, response=None):
        self.calls = 0
        self.response = response

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.calls += 1
        self.prompt = prompt
        if isinstance(self.response, Exception):
            raise self.response
        return self.response or Selection(picks=[choice(1)])


def run(store, model=None, items=None, now=NOW, preview=False, p=None):
    return build_briefing((p or profile()).model_dump(), store=store, clock=lambda: now,
                          fetcher=lambda _: items if items is not None else [Article.model_validate(article(1))],
                          model=model, preview=preview).invoke({})


def seed(store, i, now=NOW, value='useful', reason='', p=None, video=None):
    p = p or profile()
    a = article(video if video is not None else i)
    store.run(f'run{i}', p.context, now, 'completed', {'report':'x'}, [a], [choice(a['id']).model_dump()])
    return store.feedback(f'run{i}:1', value, reason, now)


def test_real_sample():
    items = parse_feed((Path(__file__).parents[1] / 'docs/rsshub-deploy/cursor-test.xml').read_bytes())
    assert len(items) == 20
    assert all(a.duration_seconds and a.tags for a in items)
    assert items[0].duration_seconds == 245
    assert all('Play:' not in a.excerpt and 'Match By:' not in a.excerpt and 'Favorite:' not in a.excerpt for a in items)
    assert all(len(a.source_ids) >= 2 for a in items)


@pytest.mark.parametrize('value,expected', [('04:05',245), ('252:15',15135), ('1:02:03',3723), ('1:99',None), ('x',None), ('00:00',None)])
def test_duration_formats(value, expected):
    duration, body, warnings = description_fields(f'Length: {value}<br>Play: 12 Favorite: 1<br>Danmaku: 0 Comment: 2<br><br>正文评论和收藏很有用')
    assert duration == expected
    assert body == '正文评论和收藏很有用'
    assert bool(warnings) == (expected is None)


def test_missing_and_ordinary_prose():
    a = parse_feed(b'<rss><channel><item><title>a</title><link>https://x.org/a</link></item></channel></rss>')[0]
    assert a.duration_seconds is None and a.tags == [] and a.excerpt == ''
    assert description_fields('Play: is a verb<br>Comment: this is prose')[1] == 'Play: is a verb Comment: this is prose'


def test_fair_unique_and_duration():
    p = profile(max_candidates=4)
    groups = [('a', [article(i) for i in range(10)]), ('b',[article(i) for i in [0,10,11,12]])]
    selected, raw, counts = prepare(groups, p, set())
    assert [a['id'] for a in selected] == ['0','10','1','11']
    assert selected[0]['matched_keywords'] == ['a','b']
    assert counts['duplicates'] == 1
    assert prepare(groups, profile(max_duration_seconds=300), set())[0] == []


@pytest.mark.parametrize('changes', [{'keywords':['']}, {'exploration_ratio':2}, {'max_picks':0}, {'rsshub_url':'file:///x'}, {'learning':{'max_score_adjustment':6}}])
def test_validation_shared(changes):
    data = profile().model_dump()
    data.update(changes)
    with pytest.raises(ValueError):
        build_briefing(data)


def test_model_call_history_preview_and_empty(store):
    m = Model()
    run(store, m, preview=True)
    assert m.calls == 0
    first = run(store, m)
    assert m.calls == 1 and first['metrics']['logical_model_calls'] == 1
    assert '适合初学者' not in m.prompt
    second = run(store, m)
    assert not second['articles'] and m.calls == 1
    run(store, m, items=[])
    assert m.calls == 1
    run(store, m, now=NOW+timedelta(days=15))
    assert m.calls == 2
    assert '观看状态' not in first['report']


def test_failure_preserves_candidates_and_does_not_cooldown(store):
    m = Model(ValueError('bad response'))
    with pytest.raises(ValueError):
        run(store,m)
    assert m.calls == 1
    row = store.db.execute("SELECT * FROM runs WHERE status='failed'").fetchone()
    assert json.loads(row['data'])['failed_stage'] == 'select'
    assert len(json.loads((store.output / row['id'] / 'candidates.json').read_text(encoding='utf-8'))) == 1
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0
    run(store, Model())


@pytest.mark.parametrize('bad', [dict(picks=[dict(id='unknown',summary='s',reason='r',action='a')]),
    dict(picks=[choice(1).model_dump(), choice(1).model_dump()]),
    dict(picks=[dict(id='1',summary='s',reason='r',action='a',base_score=101)]),
    dict(picks=[dict(id='1',summary='s',reason='r',action='a',title='invented')])])
def test_invalid_model_no_repair(store,bad):
    m = Model(bad)
    with pytest.raises(ValueError):
        run(store,m)
    assert m.calls == 1
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0


@pytest.mark.parametrize('k,slots', [(1,0),(4,0),(5,1),(10,2)])
def test_quotas_and_exploration_no_learning(k,slots):
    p = profile(max_picks=k)
    articles = [article(i) for i in range(15)]
    draft = Selection(picks=[choice(i, base_score=90-i, eligible_explore=True, exploration_reason='另一实践方向') for i in range(15)])
    picks = finalize(draft, articles, {'tag:python':-100}, p)
    assert len(picks) == k and len({x['id'] for x in picks}) == k
    assert sum(x['lane']=='explore' for x in picks) == slots
    assert all(x['adjustment']==0 for x in picks if x['lane']=='explore')
    assert exploration_slots(p) == slots
    assert len(finalize(Selection(picks=[choice(0)]),articles,{},p)) == 1


def test_no_explore_fills_regular_and_ineligible_not_forced():
    picks = finalize(Selection(picks=[choice(i) for i in range(8)]),[article(i) for i in range(8)],{},profile())
    assert len(picks) == 5 and all(p['lane']=='regular' for p in picks)
    assert not finalize(Selection(picks=[choice(0,eligible_regular=False)]),[article(0)],{},profile())


def test_adjustment_and_position_limits():
    articles = [dict(article(i), tags=[str(i),'python','ai']) for i in range(12)]
    drafts = [choice(i, base_score=60-i*.1).model_dump() for i in range(12)]
    weights = {'tag:'+str(i): (-100 if i%2==0 else 100) for i in range(12)}
    for bias in (-100,100):
        result = bounded_order(drafts,articles,dict(weights,**{'tag:python':bias,'tag:ai':bias}),profile())
        assert all(abs(p['adjustment']) <= 5 for p in result)
        assert all(abs(p['adjusted_position'] - p['base_position']) <= 1 for p in result)
    result = bounded_order(drafts,articles,weights,profile())
    assert any(p['base_position'] != p['adjusted_position'] for p in result)


def test_same_day_burst_no_preference(store):
    for i in range(50):
        seed(store,i)
    assert refresh(store,profile(),NOW)[1] == {}


def test_activation_decay_opposition_and_undo(store):
    events = [seed(store,i,NOW-timedelta(days=i)) for i in range(3)]
    assert refresh(store,profile(),NOW)[1]['tag:python'] > 0
    assert refresh(store,profile(),NOW+timedelta(days=365))[1] == {}
    undo = store.feedback(None,'undo','',NOW,undo=events[0])
    assert refresh(store,profile(),NOW)[1] == {}
    seed(store,4,NOW+timedelta(days=1),value='not_useful')
    seed(store,5,NOW+timedelta(days=2),value='not_useful')
    assert refresh(store,profile(),NOW+timedelta(days=2))[1] == {}
    assert undo


def test_repeat_video_and_no_feedback_unknown(store):
    for i in range(4):
        seed(store,i,NOW-timedelta(days=i),video='same')
    assert refresh(store,profile(),NOW)[1] == {}
    store.run('no-feedback',profile().context,NOW,'completed',{},[article('unknown')],[choice('unknown').model_dump()])
    assert refresh(store,profile(),NOW)[1] == {}


def test_later_only_cooldown_and_undo(store):
    p = profile(history_cooldown_days=0)
    event = seed(store,1,value='later',p=p)
    assert refresh(store,p,NOW)[1] == {}
    assert '1' in store.blocked(p.context,NOW,p)
    store.feedback(None,'undo','',NOW,undo=event)
    assert '1' not in store.blocked(p.context,NOW,p)


def test_duration_requires_reason_and_no_author_rules(store):
    for i in range(3):
        a = article(i,duration_seconds=1800)
        day = NOW-timedelta(days=i)
        store.run(str(i),profile().context,day,'completed',{},[a],[choice(i).model_dump()])
        store.feedback(str(i)+':1','not_useful','',day)
    assert set(refresh(store,profile(),NOW)[1]) == {'tag:python'}
    for i in range(3):
        store.feedback(str(i)+':1','not_useful','太长',NOW-timedelta(days=i))
    assert set(refresh(store,profile(),NOW)[1]) == {'duration:2'}


def test_idempotency_replacement_and_revert(store):
    seed(store,1)
    event = store.feedback('run1:1','not_useful','',NOW,key='request1')
    assert event == store.feedback('run1:1','not_useful','',NOW,key='request1')
    assert len(store.effective_feedback(profile().context)) == 1
    assert store.effective_feedback(profile().context)[0]['value'] == 'not_useful'
    with pytest.raises(ValueError):
        store.feedback('run1:1','useful','',NOW,key='request1')
    store.feedback(None,'undo','',NOW,undo=event)
    assert store.effective_feedback(profile().context)[0]['value'] == 'useful'


def test_learning_update_failure_rollback_restore(store, monkeypatch):
    for i in range(3):
        seed(store,i,NOW-timedelta(days=i))
    version, weights = refresh(store,profile(),NOW)
    store.db.execute("CREATE TRIGGER fail_head BEFORE INSERT ON preference_heads BEGIN SELECT RAISE(ABORT,'test'); END")
    v, w, error = refresh_safe(store,profile(),NOW)
    assert v == version and w == weights and error
    assert store.db.execute('SELECT count(*) FROM preference_snapshots').fetchone()[0] == 1
    store.db.execute('DROP TRIGGER fail_head')
    v2,_ = refresh(store,profile(),NOW+timedelta(days=365))
    store.restore_preferences(profile().context,version)
    assert store.preferences(profile().context) == (version,weights)
    assert v2 != version


def test_file_failure_recover_idempotently(store,monkeypatch):
    original = store.export
    def export(run_id):
        status = store.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]
        if status == 'completed':
            raise OSError('interrupted')
        return original(run_id)
    monkeypatch.setattr(store,'export',export)
    with pytest.raises(OSError):
        run(store,Model())
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 1
    monkeypatch.setattr(store,'export',original)
    store.recover()
    store.recover()
    row = store.db.execute("SELECT id FROM runs WHERE status='completed'").fetchone()
    assert (store.output / row[0] / 'briefing.md').exists()
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 1


def test_transaction_failure_no_partial_recommendations(store):
    store.db.execute("CREATE TRIGGER fail_rec BEFORE INSERT ON recommendations BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(Exception):
        run(store,Model())
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM runs WHERE status='completed'").fetchone()[0] == 0


def test_context_and_legacy_profile():
    p = profile()
    assert profile(max_picks=2).context == p.context
    assert profile(learning={'enabled':False}).context == p.context
    assert Profile.model_validate(dict(rsshub_url='http://localhost',keywords=['x'],interests='',max_picks=5))
    assert profile(max_duration_seconds=500).context != p.context


def test_cli_and_feedback(store,monkeypatch,tmp_path,capsys):
    import info_agent.cli as cli
    seed(store,1)
    monkeypatch.setattr(cli,'Store',lambda _:store)
    monkeypatch.setattr(store,'close',lambda:None)
    config = tmp_path/'profile.json'
    config.write_text(json.dumps(profile().model_dump()),encoding='utf-8')
    assert cli.main(['--profile',str(config),'feedback','--run','run1','--item','1','--value','later','--key','cli']) == 0
    event = store.db.execute("SELECT id FROM feedback_events WHERE request_key='cli'").fetchone()[0]
    assert cli.main(['--profile',str(config),'feedback','--undo',event]) == 0
    assert cli.main(['--profile',str(config),'preferences']) == 0
    monkeypatch.setattr(cli,'build_briefing',lambda *a,**kw: build_briefing(*a,**dict(kw,fetcher=lambda _:[],clock=lambda:NOW)))
    assert cli.main(['--profile',str(config),'--preview','--show']) == 0


def test_restore_used_until_next_feedback(store):
    for i in range(3):
        seed(store,i,NOW-timedelta(days=i))
    v,w = refresh(store,profile(),NOW)
    refresh(store,profile(),NOW+timedelta(days=365))
    store.restore_preferences(profile().context,v)
    assert refresh_safe(store,profile(),NOW+timedelta(days=365))[:2] == (v,w)
    store.feedback('run0:1','not_useful','',NOW)
    assert refresh_safe(store,profile(),NOW+timedelta(days=365))[1] == {}


def test_feedback_storage_failure_is_not_success(store):
    seed(store,1)
    store.db.execute("CREATE TRIGGER fail_feedback BEFORE INSERT ON feedback_events BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(Exception):
        store.feedback('run1:1','later','',NOW)
    assert store.effective_feedback(profile().context)[0]['value'] == 'useful'


def test_interrupted_collection_recovery(store):
    store.run('interrupted',profile().context,NOW,'collected',{'process_id':-1},[article(1)])
    store.recover()
    store.recover()
    assert store.db.execute("SELECT status FROM runs WHERE id='interrupted'").fetchone()[0] == 'failed'
    assert not store.blocked(profile().context,NOW,profile())


def test_aliases_merge_without_extra_request(store):
    a = dict(article(1),source_ids=['bilibili:av1','bilibili:BVabc'])
    b = dict(article(2),source_ids=['bilibili:BVabc'])
    assert store.canonicalize(a)['id'] == store.canonicalize(b)['id']


def test_backup_and_legacy_import(store,tmp_path):
    from info_agent.maintenance import import_legacy
    directory = tmp_path/'20261023-120000-old'
    directory.mkdir()
    (directory/'run.json').write_text(json.dumps({'profile':profile().model_dump(),'selection':Selection(picks=[choice(1)]).model_dump()}),encoding='utf-8')
    (directory/'candidates.json').write_text(json.dumps([article(1)]),encoding='utf-8')
    (directory/'briefing.md').write_text('old',encoding='utf-8')
    run_id = import_legacy(store,directory,profile(),NOW,True)
    assert import_legacy(store,directory,profile(),NOW,True) == run_id
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 1
    store.backup(tmp_path/'backup.db')
    backup = Store(tmp_path/'backup.db')
    assert backup.db.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 1
    backup.close()
    with pytest.raises(ValueError):
        store.backup(tmp_path/'backup.db')


def test_retry_metrics_count_transport_attempts(store):
    from info_agent.metrics import record_request
    class RetryModel(Model):
        def invoke(self,prompt):
            record_request(None)
            record_request(None)
            return super().invoke(prompt)
    result = run(store,RetryModel())
    assert result['metrics']['network_retries'] == 1
    assert result['metrics']['http_attempts'] == 2
    assert result['metrics']['logical_model_calls'] == 1


def test_existing_entry_points(monkeypatch):
    import runpy
    import info_agent.workflow as workflow
    calls=[]
    monkeypatch.setattr(workflow,'main',lambda argv=None: calls.append(argv) or 0)
    for path in [Path(__file__).parents[1]/'main.py',Path(__file__).parents[1]/'info_agent/__main__.py']:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(path),run_name='__main__')
        assert exc.value.code == 0
    assert calls == [['--show'],None]


def test_empty_feedback_stats_unknown(store):
    assert store.feedback_stats(profile().context)['useful_rate'] is None
    seed(store,1,value='later')
    stats=store.feedback_stats(profile().context)
    assert stats['feedback_count'] == 1 and stats['evaluated'] == 0 and stats['useful_rate'] is None
