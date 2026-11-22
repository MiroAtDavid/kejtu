"""Hold new work while the installed Linux package is replaced, and say when it is done.

The package manager's pre-update step runs `ops/control.py prepare-update` as the person,
which asks the running app to prepare:
- New independent work is refused at once.
- Work already admitted gets two minutes; anything still running is then stopped the way
  Stop does, and never retried.
- The app reports `held` only after nothing it started is still running and no helper
  process uses the package folder.

Afterwards the package manager's own records say whether the installation completed
(offer Relaunch) or ended without replacing this version (resume).

Nothing is stored in the archive. Development checkouts get an inactive owner.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from ..core.records import Failure

PROTOCOL = 1
GRACE_SECONDS = 120
STOP_SECONDS = 60
HELPER_SECONDS = 10
WATCH_SECONDS = 2
MANIFEST = 'kejtu-package.json'
PHASES = ('normal', 'holding', 'stopping', 'held', 'updated', 'failed')
# Watching a reply finish, reading, and stopping work stay available while held.
# Everything else is refused with `updating`.
ALLOWED = frozenset({
    'hello', 'update.status', 'background.status', 'background.observe', 'background.attach',
    'background.detach', 'turn.status', 'turn.stream', 'turn.inspect', 'turns.page',
    'conversations.list', 'conversation.title', 'body.read', 'body.view', 'mutation.lookup',
    'turn.cancel', 'jobs.cancel_run',
})
REFUSAL = 'Kejtu is being updated. New work starts again after the update.'


# --- What the package manager says (one rule; Rust checks the same fixture) -------------

def _dpkg(text):
    for stanza in text.split('\n\n'):
        fields = dict(line.split(':', 1) for line in stanza.splitlines() if ':' in line and not line.startswith(' '))
        if fields.get('Package', '').strip() == 'kejtu':
            return dict(manager='dpkg', settled=fields.get('Status', '').strip() == 'install ok installed',
                        version=fields.get('Version', '').strip() or None)
    return None


def _pacman_desc(text):
    values, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line.strip('%')
        elif key and line:
            values.setdefault(key, line)
    return values


def package_record(system_root='/'):
    """The kejtu package as dpkg or pacman records it: manager, settled, version; None when
    neither knows it (an unpacked folder)."""
    system = Path(system_root)
    status = system / 'var/lib/dpkg/status'
    try:
        found = _dpkg(status.read_text(errors='replace'))
    except OSError:
        found = None
    if found is not None:
        return found
    local = system / 'var/lib/pacman/local'
    try:
        entries = [_pacman_desc((entry / 'desc').read_text(errors='replace'))
                   for entry in local.iterdir() if entry.name.startswith('kejtu-')]
    except OSError:
        return None
    versions = [entry.get('VERSION') for entry in entries if entry.get('NAME') == 'kejtu']
    if not versions:
        return None
    # One entry, and no transaction holding pacman's lock.
    return dict(manager='pacman', settled=len(versions) == 1 and not (system / 'var/lib/pacman/db.lck').exists(),
                version=versions[0] if len(versions) == 1 else None)


def names_commit(version, commit):
    return bool(version) and bool(commit) and re.search(r'\.g' + re.escape(commit[:7]) + r'(?:-|$)', version) is not None


def verdict(record, manifest_commit, running_commit):
    """complete: installed, and a different version than the one running.
    unchanged: installed, and the same version. in_progress: anything else."""
    if record is not None:
        if not record['settled'] or not names_commit(record['version'], manifest_commit):
            return 'in_progress'
    if manifest_commit is None:
        return 'in_progress'
    return 'unchanged' if manifest_commit == running_commit else 'complete'


def manifest_commit(root):
    try:
        return json.loads((Path(root) / MANIFEST).read_text())['source']['commit']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def process_start(pid):
    try:
        return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def package_children(root, pid=None):
    """Processes this backend started that use the package folder (program, arguments or
    working folder): they would read replaced files."""
    root, pid = str(root), pid or os.getpid()
    rows = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                rows[int(entry.name)] = int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                pass
    family, grown = {pid}, True
    while grown:
        more = {child for child, parent in rows.items() if parent in family} - family
        family |= more
        grown = bool(more)
    found = []
    for child in sorted(family - {pid}):
        folder = Path('/proc') / str(child)
        try:
            places = [os.readlink(folder / 'exe'), os.readlink(folder / 'cwd'),
                      *(folder / 'cmdline').read_bytes().decode(errors='replace').split('\0')]
        except OSError:
            continue
        if any(place == root or place.startswith(root + '/') or root + '/' in place for place in places):
            found.append(child)
    return found


# --- The owner ------------------------------------------------------------------------

class InstalledUpdate:
    def __init__(self, app, *, root=None, operations=None, system_root='/', enabled=True, clock=time.monotonic):
        self.app, self.enabled, self.clock = app, enabled, clock
        self.root = Path(root) if root is not None else None
        self.operations = Path(operations) if operations is not None else None
        self.system_root = system_root
        self.phase, self.request_id, self.detail = 'normal', None, None
        self.working = 0
        self.resume_requested = False
        self.task = self.watch = None
        self.running_commit = manifest_commit(self.root) if enabled else None
        self._identity = self._manifest_identity() if enabled else None
        self._checked = 0.0

    @classmethod
    def for_app(cls, app, store, root=None):
        # The same rule as adapters/mcp/layout.installed(), which the application layer may
        # not import; tests/test_installed_update.py keeps the two in agreement.
        root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
        if not (sys.platform == 'linux' and (root / MANIFEST).is_file()):
            return cls(app, enabled=False)
        # The archive is <data>/runtime/core.sqlite3; reports sit in <data>/operations.
        return cls(app, root=root, operations=Path(store.path).parent.parent / 'operations')

    # Admission ------------------------------------------------------------------------

    @property
    def holding(self):
        return self.phase != 'normal'

    def admit(self, operation):
        if not self.enabled:
            return
        self._notice_replaced_files()
        if self.phase != 'normal' and operation not in ALLOWED:
            raise Failure('updating', REFUSAL)

    def counted(self, operation):
        return self.enabled and operation not in ALLOWED

    def status(self):
        return {'phase': self.phase, 'relaunch': self.phase == 'updated',
                'detail': self.detail} if self.enabled else None

    # Requests from the package step ---------------------------------------------------

    def advertise(self):
        """Tell the package step this backend understands update requests (protocol 1)."""
        if not self.enabled:
            return
        self._write('update-capability.json', dict(protocol=PROTOCOL, pid=os.getpid(),
            start=process_start(os.getpid()), process_instance_id=self.app.process_instance_id))
        self._report()

    def doorbell(self):
        if not self.enabled:
            return
        try:
            request = self._read_request()
        except (OSError, ValueError):
            return
        if request['action'] == 'prepare':
            self.prepare(request['request_id'], grace=request.get('grace_seconds', GRACE_SECONDS))
        else:
            self.resume_requested = True
            self._check_package()

    def _read_request(self):
        path = self.operations / 'update-request.json'
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('update request is not a private file of this user')
        request = json.loads(path.read_text())
        if (request.get('protocol') != PROTOCOL or request.get('action') not in ('prepare', 'resume')
                or not isinstance(request.get('request_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', request['request_id'])
                or request.get('process_instance_id') != self.app.process_instance_id):
            raise ValueError('unsupported update request')
        grace = request.get('grace_seconds', GRACE_SECONDS)
        if type(grace) is not int or not 0 <= grace <= GRACE_SECONDS:
            raise ValueError('unsupported grace period')
        return request

    def prepare(self, request_id, *, grace=GRACE_SECONDS, detail=None):
        """Hold admission now, then drain in the background and report."""
        self.request_id, self.resume_requested = request_id, False
        if self.phase in ('holding', 'stopping'):
            self._report()
            return
        if self.phase in ('held', 'updated'):
            self._report()
            return
        self.phase, self.detail = 'holding', detail
        media = self._media()
        if media is not None:
            media.hold_observation()
        self._report()
        self.task = asyncio.get_running_loop().create_task(self._drain(grace), name='installed-update-drain')

    # Drain ----------------------------------------------------------------------------

    def _media(self):
        return getattr(getattr(self.app, 'workspace_controls', None), 'media', None)

    def _sign_ins(self):
        """Browser sign-ins of remote connections and of the model account. Each owns a
        loopback listener whose callback exchanges tokens without passing dispatch."""
        owners = (getattr(getattr(self.app, 'mcp_host', None), 'oauth', None),
                  getattr(getattr(self.app, 'model_controls', None), 'chatgpt', None))
        return [owner for owner in owners if owner is not None]

    @staticmethod
    def _signing_in(owner):
        return owner.server is not None or bool(owner.handlers)

    async def _close_sign_ins(self, *, force=False):
        """A pending sign-in cannot finish while held (its finish step is refused), and its
        listener would still accept a callback after "held". Close it through its owner
        (listener closed, callbacks joined) as soon as no callback is exchanging tokens; one
        that is may finish within the grace period, after which the owner cancels and joins
        it too. Saved accounts are not touched; only the unfinished sign-in ends."""
        for owner in self._sign_ins():
            if self._signing_in(owner) and (force or not owner.handlers):
                await owner.cancel()

    def settled(self):
        app, media = self.app, self._media()
        return ((media is None or media.settled) and self.working == 0 and not app.tasks and app.background.active is None and not app.background.preparing
                and (app.titles.active is None or app.titles.active.done())
                and (app.maintenance_active is None or app.maintenance_active.done())
                and not app.skill_controls.lock.locked() and not app.shelf_controls.lock.locked()
                and not (app.mcp_host is not None and app.mcp_host.lock.locked())
                and not getattr(app.mcp_host, 'terminal_sessions', {})
                and not (app.model_controls is not None and app.model_controls.lock.locked())
                and not app.research_changes.locked()
                # Startup keyring restores are admitted work too (secret-tool runs for them).
                and all(task is None or task.done() for task in (app.web_restore, app.model_restore))
                and not any(self._signing_in(owner) for owner in self._sign_ins()))

    async def _wait_settled(self, seconds):
        end = self.clock() + seconds
        while True:
            await self._close_sign_ins()
            if self.settled():
                return True
            if self.clock() >= end:
                return False
            await asyncio.sleep(.1)

    async def _stop_admitted(self):
        """Stop what is still running the way Stop does; outcomes are recorded, never retried."""
        app = self.app
        replies = list(app.tasks.items())
        for _, task in replies:
            task.cancel()
        if replies:
            await asyncio.wait([task for _, task in replies])
        for turn_id, task in replies:
            if task.done():  # A reply that ignored Stop is not marked as stopped.
                app._finish_unstarted_cancellation(turn_id)
        if app.mcp_host is not None:
            await app.mcp_host.close_terminals()
        await app.background.interrupt('job_cancelled')
        await app.titles.cancel()
        await app._stop_maintenance_batch()
        await self._close_sign_ins(force=True)
        # Keyring restores: cancelling ends their secret-tool; a verified resume restarts them.
        restores = [task for task in (app.web_restore, app.model_restore) if task is not None and not task.done()]
        for task in restores:
            task.cancel()
        if restores:
            await asyncio.wait(restores)

    async def _drain(self, grace):
        try:
            if not await self._wait_settled(grace):
                self.phase = 'stopping'
                self._report()
                end = self.clock() + STOP_SECONDS
                try:  # Joining is bounded too: work that ignores Stop fails the drain.
                    await asyncio.wait_for(self._stop_admitted(), STOP_SECONDS)
                except TimeoutError:
                    pass
                if not await self._wait_settled(max(0, end - self.clock())):
                    return self._fail('work_still_running')
            end = self.clock() + HELPER_SECONDS
            while package_children(self.root):
                if self.clock() >= end:
                    return self._fail('helper_still_running')
                await asyncio.sleep(.1)
            self.phase = 'held'
            self._report()
            self.watch = asyncio.get_running_loop().create_task(self._watch(), name='installed-update-watch')
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail('drain_failed')

    def _fail(self, detail):
        self.phase, self.detail = 'failed', detail
        self._report()
        if self.watch is None:
            self.watch = asyncio.get_running_loop().create_task(self._watch(), name='installed-update-watch')

    # After the package step ---------------------------------------------------------

    async def _watch(self):
        while self.phase in ('held', 'failed'):
            self._check_package()
            await asyncio.sleep(WATCH_SECONDS)

    def _check_package(self):
        """complete → updated (Relaunch). unchanged → back to normal only when the package
        manager shows the transaction is over (pacman: its lock is free) or the package step
        asked to resume after dpkg restored this version. Otherwise stay held."""
        if self.phase not in ('held', 'failed', 'holding', 'stopping'):
            return
        record = package_record(self.system_root)
        result = verdict(record, manifest_commit(self.root), self.running_commit)
        # Relaunch only after admitted work has settled: a failed drain (work still
        # running when files were replaced anyway) waits until it has.
        if result == 'complete' and (self.phase == 'held' or self.phase == 'failed' and self.settled()):
            self.phase, self.detail = 'updated', None
            self._report()
        elif result == 'unchanged' and (self.resume_requested or record is not None and record['manager'] == 'pacman'
                                        and self.phase in ('held', 'failed')):
            self.resume()

    def resume(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.watch is not None and not self.watch.done() and self.watch is not asyncio.current_task():
            self.watch.cancel()
        self.phase, self.detail, self.resume_requested = 'normal', None, False
        self.task = self.watch = None
        media = self._media()
        if media is not None:
            media.release_observation()
        if self.enabled:
            self.app.start_restores(missing_only=True)
        self._identity = self._manifest_identity()
        self._report()

    def _manifest_identity(self):
        try:
            info = (self.root / MANIFEST).stat()
            return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except OSError:
            return None

    def _notice_replaced_files(self):
        """Files replaced without the package step (bypassed hooks, first Arch transition):
        hold at once and drain the same way. Only here can admitted work meet new files."""
        if self.phase != 'normal' or self.clock() - self._checked < 1:
            return
        self._checked = self.clock()
        if self._manifest_identity() != self._identity and manifest_commit(self.root) != self.running_commit:
            self.prepare(None, detail='files_changed')

    # Reports ------------------------------------------------------------------------

    def _report(self):
        self._write('update.json', dict(protocol=PROTOCOL, request_id=self.request_id,
            process_instance_id=self.app.process_instance_id, pid=os.getpid(), phase=self.phase,
            detail=self.detail, at=time.time()))

    def _write(self, name, value):
        try:
            self.operations.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = self.operations / name
            temporary = target.with_name('.' + name + '.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(json.dumps(value) + '\n')
            os.replace(temporary, target)
        except OSError:
            pass  # The package step then waits for a report that never comes, and fails safely.

    async def close(self):
        for task in (self.task, self.watch):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
