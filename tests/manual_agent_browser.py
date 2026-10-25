"""Disposable browser acceptance server. No real config, RSS, model or history is used.

Run from project root: python tests/manual_agent_browser.py
Close with Ctrl+C after browser verification. The printed root is a temporary directory.
"""
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from info_agent.schemas import Profile
from info_agent.storage import Store,dump
from info_agent.web import WebApp,make_server
from info_agent.preferences import SHANGHAI

if __name__=='__main__':
    root=Path(tempfile.mkdtemp(prefix='info-agent-agent-ui-'))
    p=Profile(rsshub_url='http://127.0.0.1:1',keywords=['Codex'],interests='编程工具更新',profile_id='browser-test')
    (root/'briefing_profile.json').write_text(dump(p.model_dump()),encoding='utf-8')
    def no_network(*a,**kw):raise AssertionError('offline browser test cannot use network')
    app=WebApp(root=root,clock=lambda:datetime(2026,10,25,12,tzinfo=SHANGHAI),fetcher=no_network)
    server=make_server(app,0)
    print(dump({'root':str(root),'url':f'http://127.0.0.1:{server.server_port}'}),flush=True)
    try:server.serve_forever()
    finally:server.server_close()
