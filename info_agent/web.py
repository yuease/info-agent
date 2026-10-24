"""Loopback-only web UI over the existing workflow. No extra dependencies."""
from __future__ import annotations

import argparse
import json
import secrets
import threading
import uuid
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from info_agent.preferences import refresh_safe
from info_agent.schemas import Profile
from info_agent.storage import Store, atomic_write, dump
from info_agent.workflow import ROOT, build_briefing, local_now

STATIC = Path(__file__).with_name('web_assets')


class RequestError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


class WebApp:
    def __init__(self, root=ROOT, runner=build_briefing, clock=local_now):
        self.root = Path(root)
        self.profile_path = self.root / 'briefing_profile.json'
        self.db_path = self.root / 'workspace' / 'info_agent.sqlite3'
        self.runner = runner
        self.clock = clock
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.job = None

    def profile(self):
        return Profile.model_validate(json.loads(self.profile_path.read_text(encoding='utf-8-sig')))

    def store(self):
        # Each request/worker owns its SQLite connection.
        return Store(self.db_path)

    def state(self):
        with self.lock:
            p = self.profile()
            job = dict(self.job) if self.job else None
        with closing(self.store()) as s:
            runs = []
            for r in s.db.execute('SELECT * FROM runs ORDER BY created DESC,rowid DESC LIMIT 100'):
                d = json.loads(r['data'])
                runs.append(dict(id=r['id'], created=r['created'], status=r['status'],
                    current_context=r['context'] == p.context,
                    count=s.db.execute('SELECT count(*) FROM recommendations WHERE run=?', (r['id'],)).fetchone()[0],
                    candidates=d.get('metrics', {}).get('candidate_count', len(d.get('articles', []))),
                    preview=d.get('preview', False), keywords=d.get('profile', {}).get('keywords', [])))
            version, weights = s.preferences(p.context)
            stats = s.feedback_stats(p.context)
        return dict(profile=p.model_dump(), runs=runs, preferences=dict(version=version, weights=weights),
                    stats=stats, job=job, token=self.token)

    def detail(self, run_id):
        p = self.profile()
        with closing(self.store()) as s:
            r = s.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if not r:
                raise RequestError('找不到这份简报。', 404)
            d = json.loads(r['data'])
            feedback = {f['recommendation']: dict(id=f['id'], value=f['value'], reason=f['reason'])
                        for f in s.effective_feedback(r['context'])}
            picks = []
            for row in s.db.execute('SELECT * FROM recommendations WHERE run=? ORDER BY position', (run_id,)):
                item = json.loads(row['data'])
                item.update(recommendation_id=row['id'], position=row['position'], feedback=feedback.get(row['id']))
                picks.append(item)
            candidates = d.get('articles', [])
            if 'articles' not in d:
                candidates = [json.loads(x[0]) for x in s.db.execute('SELECT data FROM candidates WHERE run=? ORDER BY rowid', (run_id,))]
            return dict(id=run_id, created=r['created'], status=r['status'], picks=picks, candidates=candidates,
                        current_context=r['context'] == p.context, metrics=d.get('metrics', {}),
                        failures=d.get('failures', []), report=d.get('report', ''),
                        failed_stage=d.get('failed_stage'), error_type=d.get('error_type'),
                        preference_update_error=d.get('preference_update_error'),
                        keywords=d.get('profile', {}).get('keywords', []))

    def save_profile(self, body):
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise RequestError('正在生成简报，请完成后再保存设置。', 409)
            current = self.profile()
            if body.get('expected') != current.model_dump():
                raise RequestError('设置已在其他窗口修改，请刷新后重试。', 409)
            allowed = {'keywords', 'interests', 'max_picks', 'max_duration_seconds',
                       'exploration_ratio', 'history_cooldown_days', 'snooze_days'}
            patch = body.get('changes', {})
            if not isinstance(patch, dict) or set(patch) - (allowed | {'learning_enabled'}):
                raise RequestError('设置字段无效。')
            data = current.model_dump()
            data.update({k: v for k, v in patch.items() if k in allowed})
            if 'learning_enabled' in patch:
                data['learning']['enabled'] = patch['learning_enabled']
            updated = Profile.model_validate(data)
            atomic_write(self.profile_path, dump(updated.model_dump()) + '\n')
            return dict(changed_context=current.context != updated.context)

    def start_run(self, preview):
        if not isinstance(preview, bool):
            raise RequestError('运行类型无效。')
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise RequestError('已有任务正在运行，请稍候。', 409)
            p = self.profile()
            self.job = dict(id=uuid.uuid4().hex, status='running', preview=preview,
                            started=self.clock().isoformat(), run_id=None, error=None)
            job = dict(self.job)
            threading.Thread(target=self._run, args=(p, preview), daemon=True).start()
            return job

    def _run(self, p, preview):
        try:
            with closing(self.store()) as s:
                result = self.runner(p.model_dump(), preview=preview, store=s, clock=self.clock).invoke({})
            update = dict(status='completed', run_id=result['run_id'])
        except Exception as exc:
            # Never serialize provider exceptions, URLs or credentials to the UI.
            update = dict(status='failed', error=f'运行未完成（{type(exc).__name__}）。请检查 RSSHub 连接和模型配置，并查看历史记录。')
        with self.lock:
            self.job.update(update, finished=self.clock().isoformat())

    def feedback(self, body, undo=False):
        with self.lock:
            p = self.profile()
            with closing(self.store()) as s:
                key = body.get('key')
                if not isinstance(key, str) or not 1 <= len(key) <= 100:
                    raise RequestError('缺少有效的请求编号。')
                if undo:
                    event = body.get('event')
                    if not isinstance(event, str) or not event:
                        raise RequestError('缺少有效的反馈事件编号。')
                    row = s.db.execute('''SELECT f.id FROM feedback_events f
                        JOIN recommendations r ON r.id=f.recommendation JOIN runs u ON u.id=r.run
                        WHERE f.id=? AND f.value!='undo' AND u.context=?''', (event, p.context)).fetchone()
                    if not row:
                        raise RequestError('反馈不属于当前兴趣设置或已不可用。', 404)
                    result = s.feedback(None, 'undo', '', self.clock(), key, event)
                else:
                    rid, value, reason = body.get('recommendation'), body.get('value'), body.get('reason', '')
                    if not isinstance(rid, str) or not rid:
                        raise RequestError('缺少有效的推荐编号。')
                    if value not in ('useful', 'not_useful', 'later') or not isinstance(reason, str) or len(reason) > 300:
                        raise RequestError('反馈格式无效，原因最多 300 字。')
                    row = s.db.execute('''SELECT r.id FROM recommendations r JOIN runs u ON u.id=r.run
                        WHERE r.id=? AND u.context=? AND u.status='completed' ''', (rid, p.context)).fetchone()
                    if not row:
                        raise RequestError('该条目不属于当前兴趣设置的正式推荐。', 404)
                    result = s.feedback(rid, value, reason.strip(), self.clock(), key)
                _, _, error = refresh_safe(s, p, self.clock())
        return dict(event=result, warning='反馈已保存；偏好将在下次运行时重试更新。' if error else None)


def make_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, content, content_type='application/json; charset=utf-8', extra=None):
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(content)

        def dispatch(self, write=False):
            try:
                host = f'127.0.0.1:{self.server.server_port}'
                if self.headers.get('Host') not in (host, f'localhost:{self.server.server_port}'):
                    raise RequestError('只允许本机访问。', 403)
                origin = self.headers.get('Origin')
                if origin and origin not in ('http://' + host, f'http://localhost:{self.server.server_port}'):
                    raise RequestError('不允许跨站请求。', 403)
                path = unquote(urlsplit(self.path).path)
                if write:
                    if not secrets.compare_digest(self.headers.get('X-Info-Agent-Token', ''), app.token):
                        raise RequestError('页面会话已失效，请刷新后重试。', 403)
                    if self.headers.get_content_type() != 'application/json':
                        raise RequestError('需要 JSON 请求。', 415)
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 32768:
                        raise RequestError('请求内容过大或为空。', 413)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise RequestError('请求格式无效。')
                    if path == '/api/run':
                        return self.send(202, app.start_run(body.get('preview', False)))
                    if path == '/api/profile':
                        return self.send(200, app.save_profile(body))
                    if path == '/api/feedback':
                        return self.send(200, app.feedback(body))
                    if path == '/api/undo':
                        return self.send(200, app.feedback(body, undo=True))
                else:
                    assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                              '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
                    if path in assets:
                        filename, content_type = assets[path]
                        return self.send(200, (STATIC / filename).read_bytes(), content_type)
                    if path == '/api/state':
                        return self.send(200, app.state())
                    if path.startswith('/api/runs/'):
                        run_id = path[len('/api/runs/'):]
                        if run_id.endswith('/download'):
                            detail = app.detail(run_id[:-9])
                            if not detail['report'] or detail['status'] not in ('completed', 'empty'):
                                raise RequestError('此运行没有正式简报。', 404)
                            return self.send(200, detail['report'].encode(), 'text/markdown; charset=utf-8',
                                             {'Content-Disposition': 'attachment; filename="briefing.md"'})
                        return self.send(200, app.detail(run_id))
                raise RequestError('页面或接口不存在。', 404)
            except RequestError as exc:
                self.send(exc.status, {'error': str(exc)})
            except ValidationError:
                self.send(400, {'error': '配置不合法：关键词 1–5 个，推荐数 1–10，时长须为正数，探索比例 0–100%。'})
            except (ValueError, TypeError):
                self.send(400, {'error': '输入内容无效，请检查后重试。'})
            except Exception:
                self.send(500, {'error': '本地数据暂时不可用，请检查配置文件或稍后重试。'})

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(write=True)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='信息筛选助手本地界面')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = WebApp()
    app.profile()  # Fail early if profile is invalid.
    try:
        server = make_server(app, args.port)
    except OSError:
        parser.exit(1, '端口不可用，请关闭旧界面服务，或使用 --port 8766。\n')
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'信息筛选助手已启动：{url}\n保持此进程运行；按 Ctrl+C 关闭。', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
