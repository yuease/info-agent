"""Terminal generation, feedback, preference inspection and recovery."""
import argparse
import json
import sys
from pathlib import Path
from info_agent.schemas import Profile
from info_agent.storage import Store
from info_agent.preferences import refresh_safe
from info_agent.workflow import ROOT, build_briefing, local_now
from info_agent.errors import RunFailure, classify, public_diagnostic


def task_error(job,exc):
    diagnosis=exc.diagnosis if isinstance(exc,RunFailure) else classify(exc,'unknown')
    job.update(public_diagnostic(diagnosis))
    job.update(status='completed' if diagnosis.get('status') in ('completed','empty','preview','collection') else 'failed',
               record_available=bool(diagnosis.get('data_saved')))
    if job['status']=='completed':job['warning']=job.get('error_message')


def main(argv=None):
    parser = argparse.ArgumentParser(description='本机 RSS 信息筛选助手')
    parser.add_argument('--profile', type=Path, default=ROOT / 'briefing_profile.json')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--show', action='store_true')
    sub = parser.add_subparsers(dest='command')
    feedback = sub.add_parser('feedback', help='查看最近推荐并评价；无参数进入交互')
    feedback.add_argument('--run')
    feedback.add_argument('--item', type=int)
    feedback.add_argument('--value', choices=['useful','not_useful','later'])
    feedback.add_argument('--reason', default='')
    feedback.add_argument('--undo')
    feedback.add_argument('--key', help='可选重试幂等键')
    preferences = sub.add_parser('preferences')
    preferences.add_argument('--restore', type=int)
    preferences.add_argument('--reset', action='store_true')
    sub.add_parser('recover')
    sub.add_parser('collect',help='仅刷新来源并写入持久候选池，不调用模型')
    backup = sub.add_parser('backup')
    backup.add_argument('destination', type=Path)
    legacy = sub.add_parser('import-legacy')
    legacy.add_argument('directory', type=Path)
    legacy.add_argument('--published', action='store_true', help='明确确认旧简报已正式生成，才导入推荐历史')
    args = parser.parse_args(argv)
    db = None
    try:
        profile = Profile.model_validate(json.loads(args.profile.read_text(encoding='utf-8-sig')))
        db = Store(ROOT / 'workspace' / 'info_agent.sqlite3')
        profile = Profile.model_validate(json.loads(args.profile.read_text(encoding='utf-8-sig')))
        if args.command == 'backup':
            db.backup(args.destination)
            print(f'备份已保存：{args.destination}')
        elif args.command == 'import-legacy':
            from info_agent.maintenance import import_legacy
            print('导入运行：' + import_legacy(db, args.directory, profile, local_now(), args.published))
        elif args.command == 'recover':
            db.recover()
            print('已从数据库恢复已提交产物。')
        elif args.command == 'collect' or args.preview and args.command is None:
            from info_agent.collection import collect
            from info_agent.workflow import new_run_id
            from info_agent.jobs import claim,Lease
            now=local_now();run_id=new_run_id(now)
            job=dict(id=run_id,run_id=run_id,status='running',started=now.isoformat(),preview=True,record_available=False)
            owner=claim(db,'collect',job,now)
            if owner is None:
                from info_agent.errors import DiagnosedError
                raise DiagnosedError('TASK_BUSY')
            with Lease(ROOT/'workspace'/'info_agent.sqlite3','collect',owner) as lease:
                try:
                    result=collect(profile.model_dump(),store=db,clock=local_now,run_id=run_id)
                    job.update(status='completed',record_available=True)
                except Exception as exc:
                    task_error(job,exc)
                    raise
                finally:
                    job['finished']=local_now().isoformat()
                    lease.finish(db,job)
            print(f"采集完成，{len(result['articles'])} 条条目已保存到候选池；未调用模型。运行编号：{run_id}")
        elif args.command == 'preferences':
            if args.reset:
                version, _ = db.save_preferences(profile.context, local_now(), {}, {})
                db.restore_preferences(profile.context, version)
            if args.restore is not None:
                db.restore_preferences(profile.context, args.restore)
            print(json.dumps(dict(zip(('version','weights'), db.preferences(profile.context))), ensure_ascii=False, indent=2))
            print(json.dumps(db.feedback_stats(profile.context), ensure_ascii=False, indent=2))
        elif args.command == 'feedback':
            now = local_now()
            if args.undo:
                # Prevent cross-profile accidental undo.
                if not db.db.execute('''SELECT 1 FROM feedback_events f JOIN recommendations r ON r.id=f.recommendation
                    JOIN runs u ON u.id=r.run WHERE f.id=? AND u.context=?''', (args.undo,profile.context)).fetchone():
                    raise ValueError('反馈不属于当前兴趣上下文')
                event = db.feedback(None, 'undo', '', now, args.key, args.undo)
            else:
                run = args.run
                if not run:
                    row = db.db.execute("SELECT id FROM runs WHERE context=? AND status='completed' ORDER BY created DESC,rowid DESC LIMIT 1", (profile.context,)).fetchone()
                    if not row:
                        raise ValueError('当前兴趣还没有正式推荐')
                    run = row[0]
                rows = db.db.execute('''SELECT r.* FROM recommendations r JOIN runs u ON u.id=r.run
                    WHERE r.run=? AND u.context=? ORDER BY position''', (run,profile.context)).fetchall()
                if not rows:
                    raise ValueError('运行没有可反馈的正式推荐')
                for r in rows:
                    print(f"{r['position']}. {json.loads(r['data'])['article']['title']}")
                print(f'简报：{run}')
                item = args.item or int(input('条目编号：'))
                value = args.value or input('评价 useful=有用 / not_useful=没用 / later=暂时不想看：').strip()
                reason = args.reason if args.value else input('原因（可直接回车）：').strip()
                row = next((r for r in rows if r['position'] == item), None)
                if row is None:
                    raise ValueError('invalid item')
                event = db.feedback(row['id'], value, reason, now, args.key)
            _, _, error = refresh_safe(db, profile, now)
            print(f'反馈已保存；事件 ID：{event}')
            if error:
                print('偏好暂未更新，已保留旧版本；下次运行会自动重算。')
        else:
            from info_agent.workflow import new_run_id
            from info_agent.jobs import claim,Lease
            from info_agent.errors import DiagnosedError
            now=local_now();run_id=new_run_id(now)
            job=dict(id=run_id,run_id=run_id,status='running',started=now.isoformat(),preview=False,record_available=False)
            owner=claim(db,'generate',job,now)
            if owner is None:raise DiagnosedError('TASK_BUSY')
            with Lease(ROOT/'workspace'/'info_agent.sqlite3','generate',owner) as lease:
                try:
                    result=build_briefing(profile.model_dump(),store=db,run_id=run_id,from_pool=True).invoke({})
                    job.update(status='completed',record_available=True)
                except Exception as exc:
                    task_error(job,exc)
                    raise
                finally:
                    job['finished']=local_now().isoformat()
                    lease.finish(db,job)
            print(f"取得 {len(result['articles'])} 条候选；{len(result['failures'])} 个来源失败。")
            print('仅预览，未调用模型。' if args.preview else f"推荐 {len(result['selection']['picks'])} 条。")
            print(f"保存位置：{db.output / result['run_id']}")
            if args.show and not args.preview:
                print(result['report'])
        return 0
    except Exception as exc:
        diagnosis = exc.diagnosis if isinstance(exc,RunFailure) else classify(exc,'recover' if args.command=='recover' else 'unknown')
        print(f"{diagnosis['error_message']} [{diagnosis['error_code']}]", file=sys.stderr)
        if isinstance(exc,RunFailure):
            print(f'运行编号：{exc.run_id}', file=sys.stderr)
        return 1
    finally:
        if db:
            db.close()
