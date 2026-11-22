"""Lifecycle of this Kejtu: a source checkout, or the installed Linux package.

Never controls the older Kejtu, another checkout or shared services. Installed,
the program folder is read-only and every path below lives in the private data
folder (layout.state_folder); a checkout keeps its .state folder.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from kejtu_next.adapters.mcp.layout import installed as _installed,installed_data,state_folder

INSTALLED=_installed(ROOT)
STATE=state_folder(ROOT)
# Backups, launch inputs and operations sit beside the archive's folder.
OPERATIONS=STATE.parent/'operations'
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


def installed_processes(program=None,uid=None):
    """The installed app however it was started (menu, terminal): its window
    process, found by program file and user, and everything it started. A file
    dpkg replaced under a running app shows as deleted in /proc. Another user's
    app is read only by the package step, as root."""
    program=str(program or ROOT/'bin/kejtu')
    uid=os.getuid() if uid is None else uid
    rows=[r for p in Path('/proc').iterdir() if p.name.isdigit() and (r:=process(p.name))]
    ids=set()
    for row in rows:
        folder=Path('/proc')/str(row['pid'])
        try:
            if folder.stat().st_uid!=uid:continue
            if os.readlink(folder/'exe') in (program,program+' (deleted)'):ids.add(row['pid']);row['window']=True
        except OSError:pass
    while True:
        more={r['pid'] for r in rows if r['ppid'] in ids}
        if more<=ids:break
        ids|=more
    return [r for r in rows if r['pid'] in ids and r['state']!='Z']


def backend(args):
    return any(args[i:i+2]==[b'kejtu_next',b'native-server'] for i in range(len(args)-1))


def archive_of(row):
    """The archive folder a running installed process works on: the backend's own
    --state-dir, or for the window the native data rule applied to the window's own
    environment. None for other processes and for one that has exited."""
    folder=Path('/proc')/str(row['pid'])
    try:
        args=(folder/'cmdline').read_bytes().split(b'\0')
        if backend(args):
            if b'--state-dir' not in args[:-1]:raise RuntimeError('A running Kejtu backend names no archive folder; nothing was changed.')
            return Path(os.fsdecode(args[args.index(b'--state-dir')+1])).resolve()
        if not row.get('window'):return None
        environ=dict(item.split(b'=',1) for item in (folder/'environ').read_bytes().split(b'\0') if b'=' in item)
    except (FileNotFoundError,ProcessLookupError):return None
    except PermissionError:raise RuntimeError('Cannot confirm which data folder the running Kejtu uses; nothing was changed.') from None
    return (installed_data({os.fsdecode(k):os.fsdecode(v) for k,v in environ.items()})/'runtime').resolve()


def owned():
    if INSTALLED:
        # One user can run the installed app on another data folder (XDG_DATA_HOME).
        # Commands act only on the app whose archive is this command's archive.
        rows=installed_processes()
        other=sorted({str(state) for row in rows if (state:=archive_of(row)) is not None and state!=STATE.resolve()})
        if other:
            raise RuntimeError(f'Kejtu is running with another data folder ({other[0]}). Run this command with the '
                               'same XDG_DATA_HOME as the running app; nothing was changed.')
        return rows
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


def connection_settings(dest):
    """The backup's copy of the saved connections: no key value, and what has to be supplied again.

    The connections owner decides what a key is and writes the copy's text. The saved file is never
    copied as it is: if the owner cannot read it safely, it is left out and the receipt says so.
    Returns the receipt's entry, or None when no connections are saved.
    """
    try:
        import sys
        source=str(Path(__file__).resolve().parents[1]/'src')
        if source not in sys.path:sys.path.insert(0,source)
        from kejtu_next.adapters.mcp.host import backup_settings
        from kejtu_next.adapters.linux_sandbox import browser_identity
        # A restored, exact pre-upgrade browser still owns its fixed PYTHONPATH.
        saved=backup_settings(STATE,ROOT,lambda root,row: browser_identity(root,row,allow_previous=True))
    except Exception:
        return dict(included=False,reason='The saved connections could not be read safely, so they are not in this backup.')
    if saved is None:return None
    text,left_out,keyring=saved
    copy=dest/'runtime/mcp-settings.json';copy.parent.mkdir(parents=True,exist_ok=True)
    copy.touch(mode=0o600);copy.write_text(text)
    return dict(included=True,values_left_out=left_out,values_in_keyring=keyring)


def package_data(dest,stamp):
    """The backup's copy of each installed package's own data, and what it says about it.

    Which packages keep data is read from the archive; the copying and the person's choices belong to the
    package owner. A package's data is taken unless the person chose to leave it out. If a tool of a package
    is running, or its data cannot be copied whole, the backup is refused: nothing is left out silently.
    Returns the receipt's entry, or None when no installed package keeps data.
    """
    try:
        with closing(archive()) as db:
            packages=[dict(id=i,identity=n,version=v) for i,n,v in db.execute(
                """SELECT i.id,i.identity,i.version FROM shelf_installations i WHERE i.state!='removed' AND EXISTS(
                    SELECT 1 FROM shelf_members m JOIN shelf_components c ON c.id=m.component_id
                    WHERE m.installation_id=i.id AND c.kind='plugin_data') ORDER BY i.identity,i.id""")]
    except sqlite3.OperationalError:return None  # an archive from before packages existed
    if not packages:return None
    import sys
    source=str(Path(__file__).resolve().parents[1]/'src')
    if source not in sys.path:sys.path.insert(0,source)
    from kejtu_next.adapters.mcp.backups import backup_packages
    from kejtu_next.core.records import Failure
    def running(folder):
        # A program of a package is started with its data folder on its command line.
        name=os.fsencode(str(folder))
        for row in owned():
            try:
                if name in (Path('/proc')/str(row['pid'])/'cmdline').read_bytes():return True
            except OSError:pass
        return False
    try:return backup_packages(STATE,dest,packages,stamp,running)
    except Failure as error:raise RuntimeError(error.detail) from None


def backup():
    """A complete snapshot requires exclusive archive ownership for its whole lifetime."""
    source=str(Path(__file__).resolve().parents[1]/'src')
    if source not in sys.path:sys.path.insert(0,source)
    from kejtu_next.adapters.storage.locking import acquire_owner_lock
    from kejtu_next.core.records import Failure
    if owned():raise RuntimeError('Close Kejtu before backing up. Managed stop takes a backup after shutdown.')
    try:
        lease=acquire_owner_lock(STATE/'owner.lock')
    except Failure as error:
        raise RuntimeError('Backup requires the archive to be closed: '+error.detail) from None
    with lease:
        try:return _backup()
        except Failure as error:raise RuntimeError(error.detail) from None


def _backup():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    dest=STATE.parent/'backups'/stamp;dest.mkdir(parents=True,mode=0o700)
    try:
        with closing(archive()) as source,closing(sqlite3.connect(dest/'core.sqlite3')) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('Backup integrity check failed.')
        from kejtu_next.adapters.mcp.backups import PackageBackups
        PackageBackups(STATE).choices()  # validate before preserving the user's policy
        for rel in ('runtime/model-settings.json','runtime/preference-settings.json','runtime/access-settings.json','runtime/onboarding.json','runtime.identity.json','runtime/shelf-backup.json'):
            path=STATE.parent/rel
            if path.is_file():
                copy=dest/rel;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,copy);copy.chmod(0o600)
        connections=connection_settings(dest)
        packages=package_data(dest,stamp)
        if (STATE.parent/'launch').exists():shutil.copytree(STATE.parent/'launch',dest/'launch')
        (dest/'core.sqlite3').chmod(0o600)
        with closing(sqlite3.connect((dest/'core.sqlite3').as_uri()+'?mode=ro',uri=True)) as copied:
            state_id=copied.execute('SELECT state_id FROM state_metadata WHERE singleton=1').fetchone()[0]
        receipt=dict(root=str(ROOT),created_at=stamp,state_id=state_id,database_sha256=hashlib.sha256((dest/'core.sqlite3').read_bytes()).hexdigest())
        # Only when connections are saved or a package keeps data, so a backup without them is what it always was.
        if connections is not None:receipt['connections']=connections
        if packages is not None:receipt['packages']=packages
        (dest/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    except BaseException:
        # A backup that did not finish is not a backup. This call made the folder, so it removes it.
        shutil.rmtree(dest,ignore_errors=True)
        raise
    return str(dest)


def backup_result(dest):
    """What `./kejtu backup` prints: where the backup is, and each package's data with its size."""
    packages=json.loads((Path(dest)/'receipt.json').read_text()).get('packages')
    if packages is None:return dict(backup=dest)
    return dict(backup=dest,package_data_bytes=packages['data_bytes'],package_data_over_limit=packages['over_limit'],
        packages=[dict(package=i['identity'],data=i['data'],bytes=i['bytes']) for i in packages['items']])


def start_installed():
    """Open the installed app in the background; its own startup owns the data."""
    if owned():raise RuntimeError('Kejtu is already running.')
    OPERATIONS.mkdir(parents=True,exist_ok=True,mode=0o700)
    log=OPERATIONS/'desktop.log'
    with log.open('ab') as output:
        child=subprocess.Popen([str(ROOT/'bin/kejtu')],cwd=Path.home(),stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    log.chmod(0o600)
    return dict(started=True,pid=child.pid,log=str(log))


def start(*, initialize=False):
    if INSTALLED:
        if initialize:raise RuntimeError('The installed app sets itself up at its first start. Use kejtu start, or open Kejtu from the menu.')
        return start_installed()
    if owned():raise RuntimeError('This checkout is already running.')
    if initialize:
        if (STATE/'core.sqlite3').exists() or (STATE.parent/'runtime.identity.json').exists():
            raise RuntimeError('An archive already exists. Use ./kejtu start to reopen it.')
    elif not (STATE/'core.sqlite3').is_file():
        raise RuntimeError('No archive: use ./kejtu initialize for a new installation.')
    # Another app may still own the development origin; never steal its port.
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1',1431))==0:raise RuntimeError('Port 1431 is in use. Close the other development instance first.')
    launch=STATE.parent/'launch';profile=launch/'profile.json';standing=launch/'standing-profile.md'
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
    idle()
    # The backend gets its normal joined shutdown first. Every subsequent signal
    # is guarded by the captured process start identity, not merely a reused PID.
    backends=[]
    for row in rows:
        try:
            if backend((Path('/proc')/str(row['pid'])/'cmdline').read_bytes().split(b'\0')):backends.append(row)
        except OSError:pass
    for group in (backends,[r for r in rows if r not in backends]):
        for row in group:
            if same(row):
                try:os.kill(row['pid'],signal.SIGTERM)
                except ProcessLookupError:pass
        until=time.monotonic()+20
        while any(same(r) for r in group):
            if time.monotonic()>=until:raise RuntimeError('Owned process did not stop; no forced kill was attempted.')
            time.sleep(.1)
    # Joined shutdown releases the archive lease and all owned tool writers. Hold
    # that same lease while taking the archive/settings/data snapshot; a new app
    # owner cannot start halfway through it.
    try:saved=backup()
    except Exception as error:
        raise RuntimeError('Kejtu stopped, but its backup failed: '+str(error)+'. Run ./kejtu backup before updating.') from None
    return dict(stopped=True,backup=saved)


# --- Package updates (installed Linux) -------------------------------------------------
# The package manager's step before files change (Debian: this version's prerm upgrade;
# Arch: this version's PreTransaction hook) runs `package-prepare` as root with the files
# of the running version. Each running copy is asked, as its own user, to finish or stop
# its work; its backend acknowledges a complete drain in operations/update.json.
# Root never writes into a person's data folder and never opens an archive.
UPDATE_PROTOCOL=1
UPDATE_GRACE=120
UPDATE_BOUND=UPDATE_GRACE+60+10+30  # grace, stop, helpers, margin: the backend reports first
UPDATE_ACTIONS=('update-protocol','prepare-update','resume-update','package-prepare','package-check','package-resume')


class UpdateRefused(RuntimeError):
    """The update must not replace files now."""


class NotPausable(UpdateRefused):
    """A running copy without update support (an older build): it has to be closed first."""


def cmdline(pid):
    try:return (Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
    except OSError:return []


def running_users():
    """uid -> name of every user running the installed app (the package step, as root)."""
    program=str(ROOT/'bin/kejtu');users={}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                if os.readlink(entry/'exe') in (program,program+' (deleted)'):
                    uid=entry.stat().st_uid;users[uid]=pwd.getpwuid(uid).pw_name
            except (OSError,KeyError):pass
    return users


def update_target(uid=None):
    """A user's running app: its backend and the archive it works on, or None when closed."""
    rows=installed_processes(uid=uid)
    if not rows:return None
    backends=[r for r in rows if backend(cmdline(r['pid']))]
    if len(backends)!=1:raise UpdateRefused('Kejtu is starting or closing; run the update again in a moment.')
    return backends[0],archive_of(backends[0])


def update_capability(row,operations):
    path=operations/'update-capability.json'
    try:
        info=path.lstat()
        value=json.loads(path.read_text()) if stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() else None
    except (OSError,ValueError):value=None
    if (not isinstance(value,dict) or value.get('protocol')!=UPDATE_PROTOCOL
            or value.get('pid')!=row['pid'] or value.get('start')!=row['start']):
        raise NotPausable('A running Kejtu cannot pause for updates (an older version). Close it once, then update.')
    return value


def private_write(path,value):
    temporary=path.with_name('.'+path.name+'.tmp')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as stream:stream.write(json.dumps(value)+'\n')
    os.replace(temporary,path)


def ring(row,operations,action,grace):
    """Write the request, then ring the backend's doorbell. Only a backend that advertised
    this protocol is ever signalled."""
    value=update_capability(row,operations)
    request=secrets.token_hex(16)
    private_write(operations/'update-request.json',dict(protocol=UPDATE_PROTOCOL,request_id=request,action=action,
        process_instance_id=value['process_instance_id'],grace_seconds=grace))
    if same(row):os.kill(row['pid'],signal.SIGUSR1)
    return request


def prepare_update(grace=UPDATE_GRACE,bound=UPDATE_BOUND):
    """This user's app: hold new work, let admitted work finish (then stop it), confirm."""
    target=update_target()
    if target is None:return dict(prepared=True,running=False)
    row,state=target
    operations=state.parent/'operations'
    request=ring(row,operations,'prepare',grace)
    print(f'Kejtu: finishing current work before the update (up to {f"{grace // 60} minutes" if grace>=60 else f"{grace} seconds"}) ...',file=sys.stderr,flush=True)
    end,seen=time.monotonic()+bound,None
    while time.monotonic()<end:
        if not same(row):return dict(prepared=True,running=False)  # closed meanwhile: nothing runs
        try:report=json.loads((operations/'update.json').read_text())
        except (OSError,ValueError):report={}
        if report.get('request_id')==request and report.get('pid')==row['pid']:
            phase=report.get('phase')
            if phase=='stopping' and seen!='stopping':
                print('Kejtu: stopping work that did not finish in time (as Stop does; nothing is resent).',file=sys.stderr,flush=True)
            seen=phase
            if phase in ('held','updated'):return dict(prepared=True,running=True,phase=phase)
            if phase=='failed':raise UpdateRefused(f"Kejtu could not pause for the update ({report.get('detail')}). Nothing was replaced.")
        time.sleep(.2)
    raise UpdateRefused('Kejtu did not confirm in time that it paused for the update. Nothing was replaced.')


def resume_update():
    """This user's app: the update ended without replacing its files; take work again
    once the backend has verified that itself."""
    try:target=update_target()
    except UpdateRefused:return dict(resumed=False)
    if target is None:return dict(resumed=False,running=False)
    row,state=target
    try:ring(row,state.parent/'operations','resume',0)
    except NotPausable:return dict(resumed=False)
    return dict(resumed=True)


def as_user(user,action):
    """Run this file's command as a person, with a clean environment; never as root in their folder."""
    home=pwd.getpwnam(user).pw_dir
    runuser=shutil.which('runuser',path='/usr/sbin:/usr/bin:/sbin:/bin')
    if runuser is None:raise UpdateRefused('runuser is missing; cannot ask the running Kejtu to pause.')
    return subprocess.run([runuser,'-u',user,'--',str(ROOT/'.venv/bin/python'),'-I','-B',str(Path(__file__).resolve()),action],
        env={'PATH':'/usr/bin:/bin','HOME':home,'LANG':'C.UTF-8'},stdin=subprocess.DEVNULL).returncode


def package_prepare():
    """Root, before files change: every running copy pauses, or the update stops here."""
    paused=[]
    for _,user in sorted(running_users().items()):
        code=as_user(user,'prepare-update')
        if code!=0:
            for other in paused:as_user(other,'resume-update')
            if code==3:raise NotPausable(f'The running Kejtu of {user} is an older version that cannot pause for updates. '
                                         'Close it once, then run the update again. Nothing was replaced.')
            raise UpdateRefused(f'Kejtu of {user} could not pause for the update. Nothing was replaced.')
        paused.append(user)
    return dict(paused=paused)


def package_check():
    """Root (Debian preinst, prerm failed-upgrade): go on only when no copy runs unpaused."""
    for uid,user in sorted(running_users().items()):
        try:target=update_target(uid)
        except UpdateRefused:target=False
        if target is None:continue
        if target:
            row,state=target
            try:report=json.loads((state.parent/'operations/update.json').read_text())
            except (OSError,ValueError):report={}
            if report.get('pid')==row['pid'] and report.get('phase') in ('held','updated'):continue
        raise UpdateRefused(f'Kejtu of {user} is running and did not pause for this update. Close it, then update again.')
    return dict(checked=True)


def package_resume():
    """Root (Debian postinst abort-upgrade): this version is back; the paused copies may work again."""
    return dict(resumed=[user for _,user in sorted(running_users().items()) if as_user(user,'resume-update')==0])


def update_action(action):
    if action=='update-protocol':return dict(protocol=UPDATE_PROTOCOL)
    if action.startswith('package-') and os.getuid()!=0:raise UpdateRefused('Package steps run as root.')
    return {'prepare-update':prepare_update,'resume-update':resume_update,'package-prepare':package_prepare,
            'package-check':package_check,'package-resume':package_resume}[action]()


RESTORED=('runtime/model-settings.json','runtime/mcp-settings.json','runtime/preference-settings.json','runtime/access-settings.json','runtime/onboarding.json','runtime.identity.json','runtime/shelf-backup.json')


def restore(source):
    """Put one backup into this checkout's state folder, which must be fresh. Never over existing work.

    The backup must be a real folder whose archive is the one its receipt names, whole, and in the archive
    format this version reads. The archive, the settings, the launch profile and each package's own data
    are put back; nothing else is. A backup holds no program files and no connection keys, and it may have
    left a package's data out by choice: what is still to do is returned and left for the app to show.
    If anything fails, what this call created is removed again and the state folder is fresh as before.
    """
    state=STATE.parent
    if state.resolve()!=state or STATE.resolve()!=STATE:
        raise RuntimeError('Restore requires a real state folder without linked ancestors; nothing was changed.')
    taken=[str(p.relative_to(state)) for p in (STATE/'core.sqlite3',STATE/'owner.lock',STATE/'shelf',STATE/'shelf-restored.json',
        state/'launch',*(state/rel for rel in RESTORED)) if p.exists() or p.is_symlink()]
    if owned() or taken:
        raise RuntimeError(('Kejtu' if INSTALLED else 'This checkout')+' is running or already has an archive or settings ('+', '.join(taken[:3] or ['running'])+
            '). A backup is restored only into a fresh state folder; nothing was changed.')
    source=Path(source)
    receipt_file,database=source/'receipt.json',source/'core.sqlite3'
    if (not source.is_absolute() or source.resolve()!=source or not source.is_dir()
            or any(p.is_symlink() or not p.is_file() for p in (receipt_file,database))):
        raise RuntimeError('Name a real backup folder by its full path: one that holds receipt.json and core.sqlite3. Nothing was restored.')
    receipt=json.loads(receipt_file.read_text())
    if not isinstance(receipt,dict) or hashlib.sha256(database.read_bytes()).hexdigest()!=receipt.get('database_sha256'):
        raise RuntimeError('The archive in this backup is not the one its receipt names. Nothing was restored.')
    import sys
    code=str(Path(__file__).resolve().parents[1]/'src')
    if code not in sys.path:sys.path.insert(0,code)
    from kejtu_next.adapters.mcp.backups import PackageBackups,restore_needs,restore_packages
    from kejtu_next.adapters.storage.sqlite import SCHEMA_VERSION
    from kejtu_next.adapters.storage.upgrade import check_backup_archive
    from kejtu_next.core.records import Failure
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as held:
        if held.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('The archive in this backup is damaged. Nothing was restored.')
        schema=held.execute('PRAGMA user_version').fetchone()[0]
    if schema!=SCHEMA_VERSION:
        raise RuntimeError(f'This backup was made by another version of Kejtu (archive format {schema}; this version reads {SCHEMA_VERSION}). Nothing was restored.')
    pin=source/'runtime.identity.json'
    if pin.is_symlink():raise RuntimeError('The backup launch identity must be a plain file.')
    expected=pin.read_text() if pin.is_file() else receipt.get('state_id')
    if receipt.get('state_id',expected)!=expected:
        raise RuntimeError('The backup launch identity does not match its receipt. Nothing was restored.')
    try:check_backup_archive(database,expected)
    except Failure as error:raise RuntimeError(error.detail) from None
    PackageBackups(source/'runtime').choices()
    made=[]
    def create(path,body=None):
        # Only into a place that is not there: an existing file is never written over.
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as target:
            made.append(path)
            if body is not None:target.write(body)
    try:
        create(STATE/'core.sqlite3')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as held,closing(sqlite3.connect(STATE/'core.sqlite3')) as target:
            held.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('The restored archive did not verify. Nothing was kept of it.')
            try:
                programs=[dict(id=i,identity=n) for i,n in target.execute(
                    """SELECT i.id,i.identity FROM shelf_installations i WHERE i.state!='removed' AND EXISTS(
                        SELECT 1 FROM shelf_members m JOIN shelf_components c ON c.id=m.component_id
                        WHERE m.installation_id=i.id AND c.kind='package_tree') ORDER BY i.identity,i.id""")]
            except sqlite3.OperationalError:programs=[]
        create(STATE/'owner.lock')
        for rel in RESTORED:
            copy=source/rel
            if copy.is_file() and not copy.is_symlink():
                if rel=='runtime/mcp-settings.json':
                    from kejtu_next.adapters.mcp.config import Connections,restore_copy
                    create(state/rel,restore_copy(Connections(source/'runtime').read()).encode())
                else:create(state/rel,copy.read_bytes())
        if not (state/'runtime.identity.json').exists():create(state/'runtime.identity.json',expected.encode())
        if not (STATE/'shelf-backup.json').exists():
            # Older receipts retain explicit omissions even without the choices file.
            entry=receipt.get('packages') or {}
            left=[i['installation_id'] for i in entry.get('items',[]) if i.get('data')=='left_out']
            if left:
                create(STATE/'shelf-backup.json',json.dumps(dict(version=1,revision=1,
                    limit_bytes=entry.get('limit_bytes',1024**3),leave_out=left)).encode())
        PackageBackups(STATE).choices()
        if (source/'launch').is_dir() and not (source/'launch').is_symlink():
            made.append(state/'launch');shutil.copytree(source/'launch',state/'launch',symlinks=True)
        made.append(STATE/'shelf')
        data=restore_packages(STATE,source,receipt.get('packages'))
        needs=restore_needs(receipt,programs,ROOT)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        made.append(STATE/'shelf-restored.json')
        PackageBackups(STATE).note_restore(dict(needs,restored_at=stamp))
    except BaseException as error:
        for path in reversed(made):
            if path.is_dir() and not path.is_symlink():shutil.rmtree(path,ignore_errors=True)
            else:path.unlink(missing_ok=True)
        if isinstance(error,Failure):raise RuntimeError(error.detail) from None
        raise
    return dict(restored=True,backup=str(source),backup_created_at=needs['backup_created_at'],package_data_restored=len(data),
        reinstall=[p['identity'] for p in needs['reinstall']],
        reconnect=[dict(connection=r['connection'],fields=r['fields']) for r in needs['reconnect']],
        data_not_in_backup=[p['identity'] for p in needs['data_not_in_backup']],
        connections_in_backup=needs['connections_in_backup'],moved_from=needs['moved_from'],next='kejtu' if INSTALLED else './kejtu start')


def status():
    rows=owned();value=dict(root=str(ROOT),installed=INSTALLED,data=str(STATE.parent),running=bool(rows),
        owned_processes=len(rows),archive=str(STATE/'core.sqlite3'))
    if INSTALLED:
        manifest=json.loads((ROOT/'kejtu-package.json').read_text())
        from kejtu_next import __version__
        value.update(version=__version__,source=manifest['source']['commit'])
    if (STATE/'core.sqlite3').exists():
        with closing(archive()) as db:
            value.update(schema=db.execute('PRAGMA user_version').fetchone()[0],
                running_replies=db.execute("SELECT count(*) FROM turns WHERE status='running'").fetchone()[0],
                running_jobs=db.execute("SELECT count(*) FROM background_runs WHERE status IN ('running','cancel_requested')").fetchone()[0])
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('initialize','start','stop','status','backup','restore','setup-web','setup-files',*UPDATE_ACTIONS))
    parser.add_argument('backup',nargs='?',help='For restore: the full path of the backup folder to put into this fresh checkout.')
    parser.add_argument('--composer-empty',action='store_true',help='Operator has verified no unsent drafts would be lost.')
    args=parser.parse_args()
    if (args.action=='restore')!=(args.backup is not None):parser.error('Name the backup folder with restore, and only with restore.')
    if args.action in UPDATE_ACTIONS:
        # Package steps: no lock or folder of the caller's own, which may be root's.
        if not INSTALLED:parser.exit(1,'Package updates apply to the installed app only.\n')
        try:print(json.dumps(update_action(args.action)))
        except NotPausable as error:parser.exit(3,str(error)+'\n')
        except (UpdateRefused,OSError,ValueError,KeyError) as error:parser.exit(1,str(error)+'\n')
        return
    if INSTALLED and args.action in ('initialize','setup-web','setup-files'):
        parser.exit(1,'The installed package provides its own setup; use status, start, stop, backup or restore.\n')
    if args.action in ('setup-web','setup-files'):
        script='setup_web.py' if args.action=='setup-web' else 'setup_files.py'
        raise SystemExit(subprocess.call([str(ROOT/'.venv/bin/python'),str(ROOT/'tools'/script)]))
    try:
        OPERATIONS.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (OPERATIONS/'control.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=start(initialize=args.action=='initialize') if args.action in ('start','initialize') else stop(args.composer_empty) if args.action=='stop' else status() if args.action=='status' else restore(args.backup) if args.action=='restore' else backup_result(backup())
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
