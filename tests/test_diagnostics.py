"""Safe diagnostics end-to-end with isolated storage, transports, models and clocks."""
import io
import json
import logging
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import xml.etree.ElementTree as ET

import httpx
import openai
import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from info_agent.errors import (RunFailure, ModelConfigurationError, classify, public_diagnostic,
    source_diagnostic, safe_model_logs, safe_usage)
from info_agent.schemas import Profile, Article, Choice, Selection
from info_agent.storage import Store, dump
from info_agent.workflow import build_briefing
from info_agent.web import WebApp, make_server
from info_agent.preferences import SHANGHAI
from info_agent.rss import FeedError, fetch_feed, parse_feed
from info_agent.metrics import record_request

NOW = datetime(2026,10,24,17,tzinfo=SHANGHAI)
SECRET = 'PRIVATE_SENTINEL_cookie_api_key_response'


def profile(**changes):
    return Profile.model_validate(dict(rsshub_url='http://127.0.0.1:1200',keywords=['python'],interests='实践',**changes))


def articles(n=1):
    return [Article(id=str(i),title=f'视频{i}',url=f'https://www.bilibili.com/video/av{i+1}') for i in range(n)]


def pick(i=0,**kw):
    return dict(Choice(id=str(i),summary='摘要',reason='理由',action='阅读').model_dump(),**kw)


class Model:
    def __init__(self,response=None,exc=None,envelope=False):
        self.response=response if response is not None else {'picks':[pick()]}
        self.exc,self.calls,self.envelope=exc,0,envelope
    def with_structured_output(self,schema,**kw):
        assert kw==({'include_raw':True} if self.envelope else {})
        return self
    def invoke(self,prompt):
        self.calls+=1
        record_request(None)
        logging.getLogger('simulated.sdk').warning('private response %s',SECRET)
        if self.exc:
            try:
                raise self.exc
            except Exception:
                logging.getLogger('simulated.sdk').exception('raw exception %s',SECRET)
                raise
        return self.response


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'test.sqlite3')
    yield s
    s.close()


def invoke(store,model,**kw):
    return build_briefing(kw.pop('p',profile()).model_dump(),store=store,model=model,
        fetcher=kw.pop('fetcher',lambda _:articles()),clock=lambda:NOW,**kw).invoke({})


def data(store,run_id):
    row=store.db.execute('SELECT status,data FROM runs WHERE id=?',(run_id,)).fetchone()
    return row['status'],json.loads(row['data'])


def assert_private(store,caplog,exc=None):
    text=''.join(r[0] for r in store.db.execute('SELECT data FROM runs'))
    text+=''.join(p.read_text(encoding='utf-8') for p in store.output.rglob('*') if p.is_file())
    text+=caplog.text
    if exc:
        import traceback
        text+=''.join(traceback.format_exception(exc))
    assert SECRET not in text


def mixed_profile():
    return profile(sources={'up_uploads':{'enabled':True,'uids':['123']}})


def mixed_fetch(url):
    if '/user/video/' in url:
        raise TimeoutError(SECRET)
    return articles()


def test_partial_timeout_success(store,caplog):
    m=Model()
    r=invoke(store,m,p=mixed_profile(),fetcher=mixed_fetch)
    assert r['status']=='completed' and m.calls==1
    failed=r['source_results'][1]
    assert failed['error_code']=='SOURCE_TIMEOUT' and failed['status']=='failed' and failed['count']==0
    assert len(r['selection']['picks'])==1
    assert_private(store,caplog)


@pytest.mark.parametrize('response,code,counts',[
    ({'picks':[pick(),pick()]},'MODEL_DUPLICATE_IDS',{'duplicate_count':1,'actual_count':2,'allowed_count':1}),
    ({'picks':[pick(SECRET)]},'MODEL_UNKNOWN_IDS',{'unknown_id_count':1,'actual_count':1}),
    ({'picks':[pick(0),pick(1),pick(2)]},'MODEL_SHORTLIST_LIMIT',{'actual_count':3,'allowed_count':2}),
    ({'picks':[pick(base_score=101)]},'MODEL_FIELDS',None),
    ({'picks':[dict(pick(),**{SECRET:SECRET})]},'MODEL_FIELDS',None),
])
def test_invalid_model_details_and_partial_failure(store,caplog,response,code,counts):
    m=Model(response)
    items=articles(3) if code=='MODEL_SHORTLIST_LIMIT' else articles()
    def fetch(url):
        if '/user/video/' in url: raise TimeoutError(SECRET)
        return items
    p=mixed_profile().model_copy(update={'max_picks':1})
    with pytest.raises(RunFailure) as caught:
        invoke(store,m,p=p,fetcher=fetch)
    status,d=data(store,caught.value.run_id)
    assert status=='failed' and d['failed_stage']=='select' and d['error_code']==code
    assert d['source_results'][1]['error_code']=='SOURCE_TIMEOUT'
    assert m.calls==1 and d['metrics']['logical_model_calls']==1 and d['metrics']['http_attempts']==1
    assert store.db.execute('SELECT count(*) FROM candidates').fetchone()[0]==len(items)
    assert not store.db.execute('SELECT * FROM recommendations').fetchone()
    if counts:
        assert all(d['error_details'][k]==v for k,v in counts.items())
    else:
        assert d['error_details']['validation_errors']
        assert all(set(e)=={'path','type'} for e in d['error_details']['validation_errors'])
    assert_private(store,caplog,caught.value)


def sdk_error(kind):
    request=httpx.Request('POST',f'https://{SECRET}.invalid/model?key={SECRET}',headers={'Authorization':SECRET})
    if kind=='timeout': return openai.APITimeoutError(request=request)
    if kind=='connection': return openai.APIConnectionError(message=SECRET,request=request)
    status={'auth':401,'permission':403,'rate':429,'server':503,'http':400}[kind]
    cls={'auth':openai.AuthenticationError,'permission':openai.PermissionDeniedError,'rate':openai.RateLimitError,'server':openai.InternalServerError}.get(kind,openai.BadRequestError)
    return cls(SECRET,response=httpx.Response(status,request=request),body={'raw':SECRET})


@pytest.mark.parametrize('kind,code',[('auth','MODEL_AUTH'),('permission','MODEL_PERMISSION'),('rate','MODEL_RATE_LIMIT'),
    ('timeout','MODEL_TIMEOUT'),('connection','MODEL_CONNECTION'),('server','MODEL_SERVER'),('http','MODEL_HTTP')])
def test_real_sdk_types(store,caplog,kind,code):
    m=Model(exc=sdk_error(kind))
    with pytest.raises(RunFailure) as caught:invoke(store,m)
    _,d=data(store,caught.value.run_id)
    assert d['error_code']==code and m.calls==1
    assert_private(store,caplog,caught.value)


@pytest.mark.parametrize('parser_kind,code',[('json','MODEL_PARSE'),('unreadable','MODEL_PARSE'),('fields','MODEL_FIELDS'),('none','MODEL_PARSE')])
def test_production_parse_envelope_preserves_usage(store,monkeypatch,caplog,parser_kind,code):
    if parser_kind=='json':
        try: json.loads(SECRET)
        except json.JSONDecodeError as exc:
            parser=OutputParserException(SECRET,llm_output=SECRET)
            parser.__cause__=exc
    elif parser_kind=='fields':
        try: Selection.model_validate({'picks':[pick(base_score=SECRET)]})
        except ValidationError as exc: parser=exc
    else:parser=OutputParserException(SECRET,llm_output=SECRET) if parser_kind=='unreadable' else None
    response={'raw':SimpleNamespace(usage_metadata={'input_tokens':12,'output_tokens':4,'total_tokens':16,'raw':SECRET}),
              'parsed':None,'parsing_error':parser}
    m=Model(response,envelope=True)
    monkeypatch.setitem(sys.modules,'info_agent.llm',SimpleNamespace(get_model=lambda:m))
    with pytest.raises(RunFailure) as caught:invoke(store,None)
    _,d=data(store,caught.value.run_id)
    assert d['error_code']==code and m.calls==1
    assert d['metrics']['tokens']=={'input_tokens':12,'output_tokens':4,'total_tokens':16}
    if parser_kind=='json':assert d['error_details']['parse_reason']=='invalid_json'
    if parser_kind=='none':assert d['error_details']['parse_reason']=='missing_parsed_output'
    assert_private(store,caplog,caught.value)


def test_config_missing_zero_model_calls(store,monkeypatch):
    def missing():raise ModelConfigurationError('MODEL_CONFIG_MISSING')
    monkeypatch.setitem(sys.modules,'info_agent.llm',SimpleNamespace(get_model=missing))
    with pytest.raises(RunFailure) as caught:invoke(store,None)
    _,d=data(store,caught.value.run_id)
    assert d['error_code']=='MODEL_CONFIG_MISSING' and d['metrics']['logical_model_calls']==0


@pytest.mark.parametrize('exc,code',[(URLError(TimeoutError(SECRET)),'SOURCE_TIMEOUT'),(URLError(SECRET),'SOURCE_CONNECTION'),
    (ET.ParseError(SECRET),'SOURCE_PARSE'),(ValueError('cookie expired '+SECRET),'SOURCE_UNKNOWN'),
    (FeedError('auth_required'),'SOURCE_AUTH_REQUIRED'),(FeedError('auth_expired'),'SOURCE_AUTH_EXPIRED')])
def test_conservative_source_types(exc,code):
    assert source_diagnostic(exc)['error_code']==code
    assert SECRET not in json.dumps(source_diagnostic(exc))


def test_fetch_http_and_parse_safe(monkeypatch):
    class Opener:
        def open(self,*a,**kw):raise HTTPError('https://'+SECRET,403,SECRET,{},io.BytesIO(SECRET.encode()))
    monkeypatch.setattr('info_agent.rss.build_opener',lambda *a:Opener())
    with pytest.raises(FeedError) as caught:fetch_feed('http://localhost')
    assert caught.value.status=='failed' and caught.value.diagnosis['error_code']=='SOURCE_HTTP'
    with pytest.raises(ValueError) as wrong:parse_feed(b'<html/>')
    assert source_diagnostic(wrong.value)['error_code']=='SOURCE_NOT_RSS'


@pytest.mark.parametrize('mode,reason,status',[('failed','all_sources_failed','failed'),('empty','sources_empty','empty'),('filtered','filtered_empty','empty')])
def test_empty_outcomes_zero_calls(store,mode,reason,status):
    m=Model()
    def fetch(_):
        if mode=='failed':raise TimeoutError(SECRET)
        return [] if mode=='empty' else articles()
    p=profile(max_duration_seconds=1) if mode=='filtered' else profile()
    if mode=='failed':
        with pytest.raises(RunFailure) as caught:invoke(store,m,p=p,fetcher=fetch)
        actual,d=data(store,caught.value.run_id)
        assert d['error_code']=='ALL_SOURCES_FAILED'
    else:
        d=invoke(store,m,p=p,fetcher=fetch);actual=d['status']
    assert d['empty_reason']==reason and actual==status and m.calls==0
    invoke(store,m,preview=True)
    assert m.calls==0


def test_export_after_commit_and_recovery(store,monkeypatch,caplog):
    original=store.export
    def export(run):
        if data(store,run)[0]=='completed':raise OSError(SECRET)
        original(run)
    monkeypatch.setattr(store,'export',export)
    m=Model()
    with pytest.raises(OSError) as caught:invoke(store,m)
    status,d=data(store,caught.value.run_id)
    assert status=='completed' and d['briefing_saved'] and d['export_failed']
    assert d['error_code']=='EXPORT_FAILED' and '无需重新生成' in d['error_message']
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0]==1
    assert (store.output/caught.value.run_id/'error.json').exists()
    exported=json.loads((store.output/caught.value.run_id/'run.json').read_text(encoding='utf-8'))
    assert exported['error_code']=='EXPORT_FAILED' and exported['briefing_saved']
    monkeypatch.setattr(store,'export',original)
    store.recover();store.recover()
    _,recovered=data(store,caught.value.run_id)
    assert recovered['export_recovered'] and not recovered['export_failed']
    assert '现已' in public_diagnostic(recovered)['error_message']
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0]==1
    assert (store.output/caught.value.run_id/'briefing.md').exists() and m.calls==1
    assert_private(store,caplog,caught.value)


def test_db_failure_has_safe_outbox(store,monkeypatch,caplog):
    original=store.run
    def fail(*args,**kw):
        if args[3] in ('completed','failed'):raise sqlite3.OperationalError(SECRET)
        return original(*args,**kw)
    monkeypatch.setattr(store,'run',fail)
    with pytest.raises(RunFailure) as caught:invoke(store,Model())
    assert caught.value.diagnosis['error_code']=='PERSISTENCE_FAILED'
    assert not caught.value.diagnosis['data_saved']
    assert not store.db.execute('SELECT * FROM recommendations').fetchone()
    out=json.loads((store.output/caught.value.run_id/'error.json').read_text(encoding='utf-8'))
    assert out['error_code']=='PERSISTENCE_FAILED' and not out['briefing_saved']
    assert_private(store,caplog,caught.value)


def wait_job(app):
    for _ in range(500):
        if app.job['status']!='running':return
        time.sleep(.01)
    pytest.fail('worker did not finish')


@pytest.mark.parametrize('export_failure',[False,True])
def test_web_exact_failure_run_with_later_unrelated_row(tmp_path,export_failure):
    p=mixed_profile()
    (tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    def runner(profile,**kw):
        store=kw['store']
        if export_failure:
            original=store.export
            def export(run):
                if data(store,run)[0]=='completed':raise OSError(SECRET)
                return original(run)
            store.export=export
        class Graph:
            def invoke(self,_):
                try:
                    return build_briefing(profile,model=Model() if export_failure else Model({'picks':[pick(),pick()]}),fetcher=mixed_fetch,**kw).invoke({})
                finally:
                    store.run('unrelated-latest',p.context,NOW,'preview',{})
        return Graph()
    app=WebApp(tmp_path,runner=runner,clock=lambda:NOW)
    job=app.start_run(False);wait_job(app)
    assert app.job['run_id']==job['run_id']!='unrelated-latest'
    assert app.job['status']==('completed' if export_failure else 'failed')
    assert app.job['error_code']==('EXPORT_FAILED' if export_failure else 'MODEL_DUPLICATE_IDS')
    server=make_server(app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/api/state') as r:state=json.load(r)
        with urlopen(base+'/api/runs/'+job['run_id']) as r:detail=json.load(r)
        assert state['job']['run_id']==detail['id']
        assert detail['source_results'][1]['error_code']=='SOURCE_TIMEOUT'
        assert SECRET not in json.dumps([state,detail])
        if export_failure:
            assert len(detail['picks'])==1 and app.job['warning'] and detail['briefing_saved']
        else:assert detail['error_details']['duplicate_count']==1
    finally:server.shutdown();server.server_close();thread.join()


def test_legacy_no_invented_reason(tmp_path):
    p=profile();(tmp_path/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    app=WebApp(tmp_path,clock=lambda:NOW)
    with closing(app.store()) as s:
        s.run('legacy',p.context,NOW,'failed',dict(failed_stage='select',error_type='ValueError',error_message=SECRET,
            error_details={'raw':SECRET},source_results=[dict(kind='up_uploads',key='123',label='UP',status='failed',count=0,error_message=SECRET)]))
        s.export('legacy')
        exported=json.loads((s.output/'legacy'/'run.json').read_text(encoding='utf-8'))
        assert SECRET not in json.dumps(exported)
    d=app.detail('legacy')
    assert d['error_code']=='LEGACY_UNKNOWN' and '旧记录未保存详细原因' in d['error_message']
    assert d['failed_stage']=='select' and d['error_type']=='ValueError'
    assert d['source_results'][0]['error_code']=='LEGACY_UNKNOWN'
    assert SECRET not in json.dumps([d,app.state()])


def test_logging_factory_restored_and_unknown_is_conservative(caplog):
    before=logging.getLogRecordFactory()
    with safe_model_logs():
        with safe_model_logs():logging.warning(SECRET)
    assert logging.getLogRecordFactory() is before and SECRET not in caplog.text
    assert classify(ValueError('401 cookie expired '+SECRET),'model_request')['error_code']=='MODEL_UNKNOWN'
    assert public_diagnostic({'error_type':'ValueError','error_message':SECRET})['error_code']=='LEGACY_UNKNOWN'


def test_persistence_acknowledgement_failure_preserves_committed_history(store,monkeypatch):
    original=store.run
    def save(*args,**kw):
        original(*args,**kw)
        if args[3]=='completed':raise sqlite3.OperationalError(SECRET)
    monkeypatch.setattr(store,'run',save)
    with pytest.raises(RunFailure) as caught:invoke(store,Model())
    status,d=data(store,caught.value.run_id)
    assert status=='completed' and d['briefing_saved']
    assert caught.value.diagnosis['briefing_saved']
    assert '无需重新生成' in caught.value.diagnosis['error_message']
    assert store.db.execute('SELECT count(*) FROM recommendations').fetchone()[0]==1


def test_source_logging_and_unknown_model_errors_are_safe(store,caplog):
    def fetch(_):
        try:raise URLError(SECRET)
        except URLError:
            logging.exception('raw source %s',SECRET)
            raise
    with pytest.raises(RunFailure) as caught:invoke(store,Model(),fetcher=fetch)
    _,d=data(store,caught.value.run_id)
    assert d['source_results'][0]['error_code']=='SOURCE_CONNECTION'
    assert_private(store,caplog,caught.value)


def test_bad_production_envelope_is_parse_failure(store,monkeypatch):
    m=Model(['unexpected'],envelope=True)
    monkeypatch.setitem(sys.modules,'info_agent.llm',SimpleNamespace(get_model=lambda:m))
    with pytest.raises(RunFailure) as caught:invoke(store,None)
    assert data(store,caught.value.run_id)[1]['error_code']=='MODEL_PARSE'
    assert m.calls==1


def test_initial_export_failure_is_not_a_saved_briefing(store,monkeypatch):
    def fail(_):raise OSError(SECRET)
    monkeypatch.setattr(store,'export',fail)
    m=Model()
    with pytest.raises(RunFailure) as caught:invoke(store,m)
    status,d=data(store,caught.value.run_id)
    assert status=='failed' and not d['briefing_saved'] and d['data_saved']
    assert '运行数据已保存' in d['error_message']
    assert m.calls==0 and not store.db.execute('SELECT * FROM recommendations').fetchone()


def test_actual_partial_file_write_recovery(store,monkeypatch,caplog):
    import info_agent.storage as storage
    original=storage.atomic_write
    def write(path,text):
        if path.name=='briefing.md':raise OSError(SECRET)
        return original(path,text)
    monkeypatch.setattr(storage,'atomic_write',write)
    with pytest.raises(RunFailure) as caught:invoke(store,Model())
    run_id=caught.value.run_id
    output=json.loads((store.output/run_id/'run.json').read_text(encoding='utf-8'))
    assert output['status']=='completed' and output['error_code']=='EXPORT_FAILED'
    assert not (store.output/run_id/'briefing.md').exists()
    monkeypatch.setattr(storage,'atomic_write',original)
    store.recover()
    assert (store.output/run_id/'briefing.md').exists()
    output=json.loads((store.output/run_id/'run.json').read_text(encoding='utf-8'))
    assert output['export_recovered'] and not output['export_failed']
    assert_private(store,caplog,caught.value)


def test_recovery_failure_is_distinct_and_zero_model_calls(store,monkeypatch,caplog):
    def fail():raise OSError(SECRET)
    monkeypatch.setattr(store,'recover',fail)
    m=Model()
    with pytest.raises(RunFailure) as caught:invoke(store,m)
    _,d=data(store,caught.value.run_id)
    assert d['error_code']=='RECOVERY_FAILED' and d['failed_stage']=='collect'
    assert m.calls==0
    assert_private(store,caplog,caught.value)


def test_usage_keeps_safe_token_breakdown():
    assert safe_usage({'input_tokens':5,'output_tokens':2,'total_tokens':7,'raw':SECRET,
        'input_token_details':{'cache_read':4,'raw':SECRET},'output_token_details':{'reasoning':1,'audio':SECRET}})=={
        'input_tokens':5,'output_tokens':2,'total_tokens':7,'input_token_details':{'cache_read':4},'output_token_details':{'reasoning':1}}
    assert safe_usage(None) is None


def test_feed_error_details_are_allowlisted_before_storage(store,caplog):
    def fetch(_):raise FeedError(code='SOURCE_HTTP',details={'http_status':503,'raw':SECRET,'url':'https://'+SECRET})
    with pytest.raises(RunFailure) as caught:invoke(store,Model(),fetcher=fetch)
    _,d=data(store,caught.value.run_id)
    assert d['source_results'][0]['error_details']=={'http_status':503}
    assert_private(store,caplog,caught.value)
