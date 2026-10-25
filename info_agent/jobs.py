"""SQLite leases fence duplicate work across local service processes."""
import json
import os
import threading
import uuid
from contextlib import closing
from datetime import timedelta
from info_agent.storage import Store,process_alive,dump
from info_agent.errors import DiagnosedError,diagnostic,public_diagnostic,source_public


LEASE_SECONDS=90


def active(db,kind):
    row=db.execute('SELECT * FROM task_leases WHERE kind=?',(kind,)).fetchone()
    # A live owner is never displaced mid-model-call, even after a delayed heartbeat.
    if row and process_alive(row['pid']):
        job=db.execute('SELECT data FROM task_jobs WHERE id=?',(row['job'],)).fetchone()
        if job and json.loads(job[0]).get('status')=='running':return row
    return None


def claim(store,kind,job,now,*,scheduled=False,interval=60):
    db=store.db;db.execute('BEGIN IMMEDIATE')
    try:
        if active(db,kind) or db.execute("SELECT 1 FROM setting_changes WHERE status='prepared' LIMIT 1").fetchone():db.rollback();return None
        if scheduled:
            row=db.execute('SELECT * FROM collection_schedule WHERE id=1').fetchone()
            if not row or not row['enabled'] or row['interval']!=interval:
                db.execute('INSERT OR REPLACE INTO collection_schedule VALUES(1,1,?,?)',(now.timestamp()+interval*60,interval))
                db.commit();return None
            if now.timestamp()<row['next_due']:db.rollback();return None
            db.execute('UPDATE collection_schedule SET next_due=? WHERE id=1',(now.timestamp()+interval*60,))
        owner=uuid.uuid4().hex
        job['process_id']=os.getpid()
        db.execute('INSERT OR REPLACE INTO task_leases VALUES(?,?,?,?,?)',(kind,owner,os.getpid(),now.timestamp()+LEASE_SECONDS,job['id']))
        db.execute('INSERT INTO task_jobs VALUES(?,?,?,?)',(job['id'],kind,now.isoformat(),dump(job)))
        db.commit();return owner
    except Exception:db.rollback();raise


def disable_schedule(store):
    with store.db:store.db.execute('UPDATE collection_schedule SET enabled=0 WHERE id=1')


def latest(store,kind):
    row=active(store.db,kind)
    if row:
        found=store.db.execute('SELECT data FROM task_jobs WHERE id=?',(row['job'],)).fetchone()
        return json.loads(found[0]) if found else None
    found=store.db.execute('SELECT data FROM task_jobs WHERE kind=? ORDER BY rowid DESC LIMIT 1',(kind,)).fetchone()
    if not found:return None
    job=json.loads(found[0])
    if job['status']=='running':
        lease=store.db.execute('SELECT pid FROM task_leases WHERE kind=? AND job=?',(kind,job['id'])).fetchone()
        # Dead process, not an inferred provider failure. Keep the exact assigned run ID.
        run=store.db.execute('SELECT status,data FROM runs WHERE id=?',(job['run_id'],)).fetchone()
        if run:
            data=json.loads(run['data'])
            job.update(record_available=True,source_results=[source_public(r) for r in data.get('source_results',[])],**public_diagnostic(data))
            if run['status'] in ('completed','empty','preview','collection'):
                job['status']='completed'
                if job.get('error_message'):job['warning']=job['error_message']
            else:
                pid=lease['pid'] if lease else data.get('process_id') or job.get('process_id')
                code='PROCESS_INTERRUPTED' if pid and not process_alive(pid) else 'UNCLASSIFIED'
                job.update(status='failed',**(public_diagnostic(data) or diagnostic(code)))
        else:
            pid=lease['pid'] if lease else job.get('process_id')
            code='PROCESS_INTERRUPTED' if pid and not process_alive(pid) else 'UNCLASSIFIED'
            job.update(status='failed',record_available=False,**diagnostic(code))
    return job


def recover_dead(store,now):
    """At service startup, persist only confirmed dead owners; never infer provider causes."""
    db=store.db;db.execute('BEGIN IMMEDIATE')
    try:
        for row in db.execute('SELECT * FROM task_jobs').fetchall():
            job=json.loads(row['data'])
            if job.get('status')!='running':continue
            lease=db.execute('SELECT pid FROM task_leases WHERE job=?',(job['id'],)).fetchone()
            run=db.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone()
            data=json.loads(run['data']) if run else {}
            pid=lease['pid'] if lease else data.get('process_id') or job.get('process_id')
            if not pid or process_alive(pid):continue
            if run and run['status'] in ('completed','empty','preview','collection'):
                job.update(status='completed',record_available=True,**public_diagnostic(data))
                if job.get('error_message'):job['warning']=job['error_message']
            else:
                error=diagnostic('PROCESS_INTERRUPTED')
                job.update(status='failed',record_available=bool(run),finished=now.isoformat(),**error)
                if run:
                    data.update(status='failed',failed_stage='interrupted',error_type='ProcessInterrupted',**error)
                    if data.get('operation')=='collect':data.setdefault('metrics',{})['rss_requests']=None
                    if data.get('metrics',{}).get('logical_model_calls'):
                        data['metrics'].update(http_attempts=None,network_retries=None)
                    db.execute("UPDATE runs SET status='failed',data=? WHERE id=?",(dump(data),run['id']))
            db.execute('UPDATE task_jobs SET data=? WHERE id=?',(dump(job),job['id']))
            db.execute('DELETE FROM task_leases WHERE job=?',(job['id'],))
        db.commit()
    except Exception:db.rollback();raise


class Lease:
    def __init__(self,path,kind,owner):
        self.path,self.kind,self.owner=path,kind,owner
        self.stop=threading.Event();self.thread=None
    def __enter__(self):
        def renew():
            import sqlite3,time
            while not self.stop.wait(LEASE_SECONDS/3):
                try:
                    with sqlite3.connect(self.path,timeout=5) as db:
                        db.execute('UPDATE task_leases SET expires=? WHERE kind=? AND owner=?',(time.time()+LEASE_SECONDS,self.kind,self.owner))
                except sqlite3.Error:pass
        self.thread=threading.Thread(target=renew,daemon=True);self.thread.start();return self
    def finish(self,store,job):
        with store.db:
            store.db.execute('UPDATE task_jobs SET data=? WHERE id=?',(dump(job),job['id']))
    def __exit__(self,*args):
        self.stop.set()
        if self.thread:self.thread.join(2)
        import sqlite3
        try:
            with sqlite3.connect(self.path,timeout=5) as db:
                db.execute('DELETE FROM task_leases WHERE kind=? AND owner=?',(self.kind,self.owner))
        except sqlite3.Error:pass


class Scheduler:
    def __init__(self,app):self.app=app;self.stop=threading.Event();self.thread=None
    def tick(self):
        p=self.app.profile()
        if p.collection.enabled:return self.app.start_collect(scheduled=True)
        with closing(self.app.store()) as s:disable_schedule(s)
    def start(self):
        def loop():
            while not self.stop.is_set():
                try:self.tick()
                except Exception:pass  # No raw config/provider exception enters logs; next tick can retry.
                self.stop.wait(5)
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(6)
