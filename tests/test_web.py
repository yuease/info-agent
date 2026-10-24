"""Web integration tests use temporary databases and an injected workflow."""
import json
import threading
import time
from datetime import datetime
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from info_agent.schemas import Profile, Article, Choice
from info_agent.storage import dump
from info_agent.web import WebApp, RequestError, make_server
from info_agent.preferences import SHANGHAI

NOW = datetime(2026, 10, 24, 12, tzinfo=SHANGHAI)


@pytest.fixture
def app(tmp_path):
    profile = Profile(rsshub_url='http://127.0.0.1:1200', keywords=['Python'], interests='实践')
    (tmp_path / 'briefing_profile.json').write_text(dump(profile.model_dump()), encoding='utf-8')
    return WebApp(tmp_path, clock=lambda: NOW)


def seed(app, run_id='test-run', profile=None):
    p = profile or app.profile()
    article = Article(id='video', title='<script>untrusted</script>', url='https://www.bilibili.com/video/av1', tags=['python']).model_dump()
    choice = dict(Choice(id='video', summary='摘要', reason='相关', action='阅读').model_dump(), lane='regular')
    with closing(app.store()) as store:
        store.run(run_id, p.context, app.clock(), 'completed', dict(profile=p.model_dump(), report='# report', selection={'picks':[choice]}), [article], [choice])
    return run_id+':1'


def test_existing_data_and_feedback_undo(app):
    rec = seed(app)
    first = app.feedback(dict(recommendation=rec, value='useful', key='first'))
    retry = app.feedback(dict(recommendation=rec, value='useful', key='first'))
    assert first['event'] == retry['event']
    assert app.state()['stats']['feedback_count'] == 1
    assert app.detail('test-run')['picks'][0]['feedback']['value'] == 'useful'
    app.feedback(dict(event=first['event'], key='undo'), undo=True)
    assert app.detail('test-run')['picks'][0]['feedback'] is None
    assert app.state()['stats']['feedback_count'] == 0


def test_foreign_context_feedback_is_rejected(app):
    p = app.profile().model_copy(update={'interests':'different'})
    rec = seed(app, profile=p)
    assert app.detail('test-run')['current_context'] is False
    with pytest.raises(RequestError):
        app.feedback(dict(recommendation=rec, value='useful', key='x'))


def test_profile_preserves_private_and_advanced_settings_and_detects_conflict(app):
    before = app.profile().model_dump()
    app.save_profile(dict(expected=before, changes={'max_picks':7, 'learning_enabled':False}))
    after = app.profile()
    assert after.max_picks == 7 and not after.learning.enabled
    assert after.rsshub_url == before['rsshub_url']
    assert after.learning.half_life_days == before['learning']['half_life_days']
    with pytest.raises(RequestError, match='其他窗口'):
        app.save_profile(dict(expected=before, changes={'max_picks':8}))
    with pytest.raises(RequestError):
        app.save_profile(dict(expected=after.model_dump(), changes={'rsshub_url':'http://evil'}))


def test_invalid_profile_does_not_overwrite(app):
    before = app.profile_path.read_bytes()
    with pytest.raises(ValueError):
        app.save_profile(dict(expected=app.profile().model_dump(), changes={'keywords':[]}))
    assert app.profile_path.read_bytes() == before


def test_background_job_serializes_generation_and_settings(app):
    entered, release = threading.Event(), threading.Event()
    seen = []
    class Graph:
        def invoke(self, state):
            entered.set()
            assert release.wait(5)
            return {'run_id':'injected'}
    def runner(profile, **kwargs):
        seen.append(kwargs['preview'])
        return Graph()
    app.runner = runner
    app.start_run(True)
    assert entered.wait(2)
    try:
        with pytest.raises(RequestError):
            app.start_run(False)
        with pytest.raises(RequestError):
            app.save_profile(dict(expected=app.profile().model_dump(), changes={}))
        assert app.state()['job']['status'] == 'running'
    finally:
        release.set()
    for _ in range(100):
        if app.job['status'] != 'running':
            break
        time.sleep(.01)
    assert app.job['run_id'] == 'injected'
    assert seen == [True]


def test_worker_error_does_not_expose_secrets(app):
    def fail(*args, **kwargs):
        raise RuntimeError('secret-key-MUST-NOT-LEAK')
    app.runner = fail
    app.start_run(False)
    for _ in range(100):
        if app.job['status'] != 'running':
            break
        time.sleep(.01)
    assert app.job['status'] == 'failed'
    assert 'secret-key' not in app.job['error']


@pytest.fixture
def server(app):
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


def test_http_assets_state_and_download(app, server):
    seed(app)
    for path in ('/', '/app.js', '/style.css', '/favicon.svg'):
        with urlopen(server+path) as response:
            assert response.status == 200
            assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    with urlopen(server+'/api/state') as response:
        state = json.load(response)
    assert state['token'] == app.token
    with urlopen(server+'/api/runs/test-run/download') as response:
        assert response.read() == b'# report'
        assert 'attachment' in response.headers['Content-Disposition']


@pytest.mark.parametrize('headers', [{}, {'Origin':'https://other.example'}, {'Host':'evil.example'}])
def test_http_write_requires_token_and_local_origin(app, server, headers):
    h = {'Content-Type':'application/json'}
    if headers:
        h['X-Info-Agent-Token'] = app.token
    h.update(headers)
    request = Request(server+'/api/profile', data=b'{}', headers=h)
    with pytest.raises(HTTPError) as exc:
        urlopen(request)
    assert exc.value.code == 403


def test_http_feedback_roundtrip_and_not_found(app, server):
    rec = seed(app)
    request = Request(server+'/api/feedback', data=json.dumps(dict(recommendation=rec,value='later',key='http')).encode(),
                      headers={'Content-Type':'application/json','X-Info-Agent-Token':app.token})
    with urlopen(request) as response:
        assert json.load(response)['event']
    assert app.detail('test-run')['picks'][0]['feedback']['value'] == 'later'
    for path in ('/api/runs/missing', '/../.env', '/api/runs/test-run/../../.env'):
        with pytest.raises(HTTPError) as exc:
            urlopen(server+path)
        assert exc.value.code == 404


def test_filtered_empty_preview_does_not_reintroduce_raw_candidates(app):
    article = Article(id='cooled', title='Already recommended', url='https://example.org/video').model_dump()
    with closing(app.store()) as store:
        store.run('filtered', app.profile().context, app.clock(), 'preview',
                  dict(articles=[], metrics={'candidate_count': 0, 'history_filtered': 1}), [article])
    detail = app.detail('filtered')
    assert detail['candidates'] == []
    assert detail['metrics']['candidate_count'] == 0


@pytest.mark.parametrize('path,body', [('/api/feedback', dict(recommendation=[],value='useful',key='invalid')),
                                      ('/api/undo',dict(event={},key='invalid'))])
def test_bad_feedback_identifiers_are_client_errors(app,server,path,body):
    request = Request(server+path, data=json.dumps(body).encode(),
                      headers={'Content-Type':'application/json','X-Info-Agent-Token':app.token})
    with pytest.raises(HTTPError) as exc:
        urlopen(request)
    assert exc.value.code == 400


def test_injected_clock_used_for_feedback_and_job(app):
    rec = seed(app)
    response = app.feedback(dict(recommendation=rec,value='useful',key='fixed-clock'))
    with closing(app.store()) as store:
        created = store.db.execute('SELECT created FROM feedback_events WHERE id=?',(response['event'],)).fetchone()[0]
    assert created == NOW.isoformat()
