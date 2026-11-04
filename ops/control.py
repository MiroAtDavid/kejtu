"""Local development lifecycle. Never controls the older Kejtu or shared services."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.state/runtime'
OPERATIONS=ROOT/'.state/operations'
OWNER=OPERATIONS/'desktop-owner.json'


def process(pid):
    try:
        p=Path('/proc')/str(pid)
        stat=(p/'stat').read_text().rsplit(')',1)[1].split()
        return dict(pid=int(pid),ppid=int(stat[1]),start=stat[19],state=stat[0])
    except (OSError,ValueError):return None


def same(row):
    current=process(row['pid'])
    return current is not None and current['start']==row['start'] and current['state']!='Z'


def owned():
    if not OWNER.exists():return []
    owner=json.loads(OWNER.read_text())
    if owner['root']!=str(ROOT):raise RuntimeError('Owner record belongs to a different checkout.')
    if not same(owner):return []
    rows=[r for p in Path('/proc').iterdir() if p.name.isdigit() and (r:=process(p.name))]
    ids={owner['pid']}
    while True:
        more={r['pid'] for r in rows if r['ppid'] in ids}
        if more<=ids:break
        ids|=more
    return [r for r in rows if r['pid'] in ids and r['state']!='Z']


def archive():
    return sqlite3.connect((STATE/'core.sqlite3').as_uri()+'?mode=ro',uri=True)


def idle():
    if (STATE/'pending-intent.json').exists():raise RuntimeError('An unresolved native operation must finish first.')
    with closing(archive()) as db:
        for sql in ("SELECT 1 FROM turns WHERE status='running'",
                    "SELECT 1 FROM background_runs WHERE status IN ('running','cancel_requested')",
                    "SELECT 1 FROM conversation_titles WHERE status='running'"):
            if db.execute(sql).fetchone():raise RuntimeError('Kejtu is busy; finish the reply/background work first.')


def backup():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    dest=ROOT/'.state/backups'/stamp;dest.mkdir(parents=True,mode=0o700)
    with closing(archive()) as source,closing(sqlite3.connect(dest/'core.sqlite3')) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('Backup integrity check failed.')
    for rel in ('runtime/model-settings.json','runtime/mcp-settings.json','runtime/preference-settings.json','runtime/access-settings.json','runtime/onboarding.json','runtime.identity.json'):
        path=ROOT/'.state'/rel
        if path.is_file():
            copy=dest/rel;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,copy);copy.chmod(0o600)
    if (ROOT/'.state/launch').exists():shutil.copytree(ROOT/'.state/launch',dest/'launch')
    (dest/'core.sqlite3').chmod(0o600)
    receipt=dict(root=str(ROOT),created_at=stamp,database_sha256=hashlib.sha256((dest/'core.sqlite3').read_bytes()).hexdigest())
    (dest/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return str(dest)


def start(*, initialize=False):
    if owned():raise RuntimeError('This checkout is already running.')
    if initialize:
        if (STATE/'core.sqlite3').exists() or (ROOT/'.state/runtime.identity.json').exists():
            raise RuntimeError('An archive already exists. Use ./kejtu start to reopen it.')
    elif not (STATE/'core.sqlite3').is_file():
        raise RuntimeError('No archive: use ./kejtu initialize for a new installation.')
    # Another app may still own the development origin; never steal its port.
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1',1431))==0:raise RuntimeError('Port 1431 is in use. Close the other development instance first.')
    launch=ROOT/'.state/launch';profile=launch/'profile.json';standing=launch/'standing-profile.md'
    if initialize and not launch.exists():
        # Keep provider defaults and prompt preparation in their existing owner.
        # Dependency setup belongs to the installer, not desktop launch.
        sys.path.insert(0,str(ROOT))
        from tools.prepare_first_run import prepare
        prepare(ROOT)
    if not profile.is_file() or not standing.is_file():raise RuntimeError('The preserved launch profile/instructions are missing.')
    OPERATIONS.mkdir(parents=True,exist_ok=True,mode=0o700)
    env=os.environ.copy();saved=launch/'desktop-environment.json'
    if saved.exists():
        for key,value in json.loads(saved.read_text()).items():
            if key in ('DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','GDK_BACKEND','WINIT_UNIX_BACKEND','WEBKIT_DISABLE_DMABUF_RENDERER'):
                # The saved desktop backend must not change with the terminal
                # launching us. Switching X11/Wayland loses position support and
                # can overwrite the same window-state file with different bounds.
                if key in ('GDK_BACKEND','WINIT_UNIX_BACKEND','WEBKIT_DISABLE_DMABUF_RENDERER'):
                    env[key]=value
                else:
                    env.setdefault(key,value)
    env.pop('WAYLAND_DISPLAY',None)
    log=OPERATIONS/'desktop.log'
    with log.open('ab') as output:
        mode='initialize-provider' if initialize else 'reopen-provider'
        child=subprocess.Popen([str(ROOT/'run-development.sh'),mode,str(profile),
            '--notes-write','--standing-profile',str(standing)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    log.chmod(0o600)
    row=process(child.pid)
    if row is None:raise RuntimeError('Launcher exited; inspect .state/operations/desktop.log.')
    OWNER.write_text(json.dumps(dict(row,root=str(ROOT)),indent=2)+'\n');OWNER.chmod(0o600)
    return dict(started=True,pid=child.pid,log=str(log))


def stop(composer_empty):
    rows=owned()
    if not rows:return dict(stopped=True,already_stopped=True)
    if not composer_empty:raise RuntimeError('Verify all message drafts are saved or empty, then use stop --composer-empty. The archive cannot prove unsent UI draft state.')
    idle();saved=backup();idle()
    # The backend gets its normal joined shutdown first. Every subsequent signal
    # is guarded by the captured process start identity, not merely a reused PID.
    backend=[]
    for row in rows:
        try:
            if b'\x00kejtu_next\x00native-server\x00' in (Path('/proc')/str(row['pid'])/'cmdline').read_bytes():backend.append(row)
        except OSError:pass
    for group in (backend,[r for r in rows if r not in backend]):
        for row in group:
            if same(row):
                try:os.kill(row['pid'],signal.SIGTERM)
                except ProcessLookupError:pass
        until=time.monotonic()+20
        while any(same(r) for r in group):
            if time.monotonic()>=until:raise RuntimeError('Owned process did not stop; no forced kill was attempted.')
            time.sleep(.1)
    return dict(stopped=True,backup=saved)


def status():
    rows=owned();value=dict(root=str(ROOT),running=bool(rows),owned_processes=len(rows),archive=str(STATE/'core.sqlite3'))
    if (STATE/'core.sqlite3').exists():
        with closing(archive()) as db:
            value.update(schema=db.execute('PRAGMA user_version').fetchone()[0],
                running_replies=db.execute("SELECT count(*) FROM turns WHERE status='running'").fetchone()[0],
                running_jobs=db.execute("SELECT count(*) FROM background_runs WHERE status IN ('running','cancel_requested')").fetchone()[0])
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('initialize','start','stop','status','backup','setup-web','setup-files'))
    parser.add_argument('--composer-empty',action='store_true',help='Operator has verified no unsent drafts would be lost.')
    args=parser.parse_args()
    if args.action in ('setup-web','setup-files'):
        script='setup_web.py' if args.action=='setup-web' else 'setup_files.py'
        raise SystemExit(subprocess.call([str(ROOT/'.venv/bin/python'),str(ROOT/'tools'/script)]))
    try:
        OPERATIONS.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (OPERATIONS/'control.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=start(initialize=args.action=='initialize') if args.action in ('start','initialize') else stop(args.composer_empty) if args.action=='stop' else status() if args.action=='status' else dict(backup=backup())
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
