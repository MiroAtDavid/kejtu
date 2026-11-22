"""Durable record of installed Shelf packages, their resources and operations.

One installation is one installed package version from one source. Each resource
it relies on (a skill, a connection, a package tree, its persistent data, a cache
or a secret reference) is a component, identified by kind and key and pinned by its
detail. Membership records which installation owns or shares it. Reference counts
are derived from those memberships, never stored on their own, so an interrupted
operation can still say who owns what. Only identical pinned components are shared.

Operations follow the archive's receipt convention: a mutation key bound to an
intent digest, refused if reused for another intent. Install and update record
their exact target version and origin before any effect; the installation keeps
its installed pin until an update completes. Each external effect is recorded as
a prepared step *before* it happens. An effect held in this archive (a skill)
records its outcome and the membership it creates or ends inside the effect
owner's own transaction (`settle_within`), so the record and the effect commit
together. An effect whose result was not confirmed becomes uncertain (on failure
or restart) and stays visible until an explicit inspection settles it; nothing is
retried, and no later operation can mark the installation healthy while any of
its effects is unresolved. An installation counts as installed only while it
holds components.

Fields are flat, bounded, app-composed values (identifiers, versions, URLs, digests,
labels); callers must not place conversation, note or user-authored text in them.
These tables are therefore outside the note-removal copy scan
(`note_removal.TABLES`), like other non-copy tables. Whether that policy should change
is tracked separately (removal-scan thread).
"""
import json
from contextlib import nullcontext
import re
import uuid
from datetime import datetime, timezone

from ...core.included_packages import included_origin
from ...core.records import Failure

SHELF_SCHEMA = '''
CREATE TABLE shelf_installations(id TEXT PRIMARY KEY CHECK(length(id)=32),
 identity TEXT NOT NULL CHECK(length(identity) BETWEEN 1 AND 512),
 version TEXT NOT NULL CHECK(length(version) BETWEEN 1 AND 128),
 origin TEXT NOT NULL CHECK(length(origin) BETWEEN 2 AND 4096),
 state TEXT NOT NULL CHECK(state IN ('installing','installed','removing','removed','interrupted')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX shelf_live_identity ON shelf_installations(identity) WHERE state!='removed';
CREATE TABLE shelf_components(id TEXT PRIMARY KEY CHECK(length(id)=32),
 kind TEXT NOT NULL CHECK(kind IN ('skill','connection','package_tree','plugin_data','cache','secret')),
 key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 512),
 detail TEXT NOT NULL CHECK(length(detail) BETWEEN 2 AND 4096),
 created_at TEXT NOT NULL, UNIQUE(kind,key));
CREATE TABLE shelf_members(installation_id TEXT NOT NULL REFERENCES shelf_installations(id),
 component_id TEXT NOT NULL REFERENCES shelf_components(id),
 role TEXT NOT NULL CHECK(role IN ('owner','shared')), added_at TEXT NOT NULL,
 PRIMARY KEY(installation_id,component_id));
CREATE UNIQUE INDEX shelf_one_owner ON shelf_members(component_id) WHERE role='owner';
CREATE TABLE shelf_operations(mutation_key TEXT PRIMARY KEY CHECK(length(mutation_key)=32),
 intent_sha256 TEXT NOT NULL CHECK(length(intent_sha256)=64),
 kind TEXT NOT NULL CHECK(kind IN ('install','update','remove','repair')),
 installation_id TEXT NOT NULL REFERENCES shelf_installations(id),
 target_version TEXT CHECK(target_version IS NULL OR length(target_version) BETWEEN 1 AND 128),
 target_origin TEXT CHECK(target_origin IS NULL OR length(target_origin) BETWEEN 2 AND 4096),
 state TEXT NOT NULL CHECK(state IN ('applying','completed','failed','interrupted')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK((kind IN ('install','update')) = (target_version IS NOT NULL AND target_origin IS NOT NULL)));
CREATE UNIQUE INDEX shelf_one_applying ON shelf_operations(installation_id) WHERE state='applying';
CREATE TABLE shelf_operation_steps(mutation_key TEXT NOT NULL REFERENCES shelf_operations(mutation_key),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 255),
 component_kind TEXT NOT NULL CHECK(component_kind IN ('skill','connection','package_tree','plugin_data','cache','secret')),
 component_key TEXT NOT NULL CHECK(length(component_key) BETWEEN 1 AND 512),
 action TEXT NOT NULL CHECK(action IN ('download','materialize','register','enable','disable','unregister','delete','keep')),
 outcome TEXT NOT NULL CHECK(outcome IN ('prepared','applied','uncertain','failed','skipped')),
 code TEXT CHECK(code IS NULL OR length(code) BETWEEN 1 AND 64),
 updated_at TEXT NOT NULL, PRIMARY KEY(mutation_key,ordinal));
'''
SHELF_TABLES = ('shelf_installations', 'shelf_components', 'shelf_members', 'shelf_operations', 'shelf_operation_steps')

KINDS = ('skill', 'connection', 'package_tree', 'plugin_data', 'cache', 'secret')
ACTIONS = ('download', 'materialize', 'register', 'enable', 'disable', 'unregister', 'delete', 'keep')
OPERATIONS = ('install', 'update', 'remove', 'repair')
MAX_STEPS = 256
MAX_LISTED = 100
MAX_UNRESOLVED_LISTED = 20
_ID = re.compile(r'[0-9a-f]{32}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_CODE = re.compile(r'[a-z_]{1,64}\Z')
_FIELD = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
# Labels and keys are app-composed: printable, single-line and bounded. A field
# value may be empty where its meaning is empty, such as a repository-root path.
_TEXT = re.compile(r'[^\x00-\x1f\x7f]{1,512}\Z')
_VALUE = re.compile(r'[^\x00-\x1f\x7f]{0,512}\Z')
# A step's outcome may only move forward: prepared before the effect, then what
# was observed. An uncertain step is settled only by an explicit inspection.
_NEXT = {'prepared': ('applied', 'failed', 'skipped'), 'uncertain': ('applied', 'failed')}
_UNRESOLVED = "('prepared','uncertain')"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _label(value, field, maximum=512):
    if not isinstance(value, str) or len(value) > maximum or not _TEXT.fullmatch(value):
        raise Failure('shelf_record', f'Invalid {field}; nothing was saved.')
    return value


def _document(value, field):
    """A flat, bounded object of app-composed fields, stored canonically."""
    if (not isinstance(value, dict) or not 1 <= len(value) <= 16
            or any(not isinstance(k, str) or not _FIELD.fullmatch(k) for k in value)
            or any(not (type(v) in (int, bool) or isinstance(v, str) and _VALUE.fullmatch(v)) for v in value.values())):
        raise Failure('shelf_record', f'The {field} must be a small flat object of short fields; nothing was saved.')
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(text) > 4096:
        raise Failure('shelf_record', f'The {field} is too large; nothing was saved.')
    return text


class ShelfStore:
    def __init__(self, store):
        self.store, self.db = store, store.db

    # Operations

    def begin(self, kind, mutation_key, intent_sha256, *, installation_id, identity=None, version=None, origin=None, transaction=True, included=False):
        """Record an operation, and an install or update's exact target, before any effect.

        The same key with the same intent returns the existing receipt instead of
        starting again. The same key with another intent is refused.
        """
        if kind not in OPERATIONS or not _ID.fullmatch(mutation_key or '') or not _DIGEST.fullmatch(intent_sha256 or ''):
            raise Failure('shelf_record', 'Invalid Shelf operation identity; nothing was saved.')
        if not _ID.fullmatch(installation_id or ''):
            raise Failure('shelf_record', 'Invalid installation identity; nothing was saved.')
        target = (_label(version, 'version', 128), _document(origin, 'origin')) if kind in ('install', 'update') else (None, None)
        with self.db if transaction else nullcontext():
            existing = self._operation(mutation_key)
            if existing is not None:
                if existing['intent_sha256'] != intent_sha256:
                    raise Failure('mutation_conflict', 'That operation key belongs to another Shelf intent.')
                return self.receipt(mutation_key)
            now = stamp()
            if kind == 'install':
                identity = _label(identity, 'package identity')
                if self._installation(installation_id) is not None:
                    raise Failure('shelf_conflict', 'This installation already exists; nothing was saved.')
                if self.db.execute("SELECT 1 FROM shelf_installations WHERE identity=? AND state!='removed'",
                                   (identity,)).fetchone():
                    raise Failure('shelf_installed', 'This package is already installed; choose update or remove.')
                self.db.execute("INSERT INTO shelf_installations VALUES(?,?,?,?,'installing',?,?)",
                                (installation_id, identity, *target, now, now))
            else:
                installation = self._installation(installation_id)
                if installation is None or installation['state'] == 'removed':
                    raise Failure('shelf_missing', 'This installation is not present; nothing was saved.')
                if kind in ('remove', 'update') and included_origin(json.loads(installation['origin'])) and not included:
                    raise Failure('package_included', 'This package is included with Kejtu. You can disable its skills; updates come with the app.')
                if kind == 'update' and (self._unresolved_count(installation_id) or not (
                        installation['state'] == 'installed' or self._between_versions(installation_id))):
                    # An update that stopped half way can be taken up again by another update.
                    raise Failure('shelf_attention', 'Repair this installation before updating it.')
                if self.db.execute("SELECT 1 FROM shelf_operations WHERE installation_id=? AND state='applying'",
                                   (installation_id,)).fetchone():
                    raise Failure('shelf_busy', 'Another operation on this installation is unfinished.')
                if kind == 'remove':
                    self.db.execute("UPDATE shelf_installations SET state='removing',updated_at=? WHERE id=?",
                                    (now, installation_id))
            self.db.execute("INSERT INTO shelf_operations VALUES(?,?,?,?,?,?,'applying',?,?)",
                            (mutation_key, intent_sha256, kind, installation_id, *target, now, now))
        return self.receipt(mutation_key)

    def prepare(self, mutation_key, kind, key, action, *, transaction=True):
        """Record one external effect as prepared, before attempting it. Returns its ordinal."""
        if kind not in KINDS or action not in ACTIONS:
            raise Failure('shelf_record', 'Invalid Shelf step; nothing was saved.')
        key = _label(key, 'component key')
        with self.db if transaction else nullcontext():
            self._applying(mutation_key)
            ordinal = self.db.execute('SELECT count(*) FROM shelf_operation_steps WHERE mutation_key=?',
                                      (mutation_key,)).fetchone()[0]
            if ordinal >= MAX_STEPS:
                raise Failure('shelf_limit', 'This operation reached its step bound; nothing was saved.')
            self.db.execute("INSERT INTO shelf_operation_steps VALUES(?,?,?,?,?,'prepared',NULL,?)",
                            (mutation_key, ordinal, kind, key, action, stamp()))
            self._touch(mutation_key)
        return ordinal

    def settle(self, mutation_key, ordinal, outcome, code=None, *, attach=None, detach=None):
        """Record what a prepared step did, or what an inspection found for an uncertain one.

        A prepared step is settled by its own applying operation. An uncertain step
        can be settled at any time, so inspection never waits for a restart. A step
        that creates or ends a membership records it in the same transaction:
        ``attach`` is (kind, key, detail[, role]) for an applied step, ``detach`` the
        component id a step releases, both for the step's own installation. The
        record never shows an applied effect without its membership, or the reverse.
        """
        with self.db:
            self._settle(mutation_key, ordinal, outcome, code, attach, detach)

    def settle_within(self, mutation_key, ordinal, outcome, code=None, *, attach=None, detach=None):
        """The same, inside the transaction of the owner applying the effect.

        The owner commits or rolls back effect and record together, so a step still
        unsettled afterwards means its effect never committed.
        """
        self._settle(mutation_key, ordinal, outcome, code, attach, detach)

    def finish(self, mutation_key, state, *, transaction=True):
        """Close an applying operation.

        Completion requires every effect recorded for this installation, by this or
        any earlier operation, to be settled; an update then publishes its target as
        the installed pin. The completed state follows membership: removed when the
        installation holds nothing, so a repair that finds nothing never claims
        "installed". An install or repair is installed only when complete: every step
        its install declared took effect and every component it registered is still
        held; a partly installed or partly removed package stays interrupted (needs
        attention). A removal completes only once it has released every component. A
        failure marks this operation's unconfirmed effects uncertain, so they stay
        visible and can be inspected without a restart.
        """
        if state not in ('completed', 'failed'):
            raise Failure('shelf_record', 'Operations finish as completed or failed.')
        with self.db if transaction else nullcontext():
            operation = self._applying(mutation_key)
            installation_id = operation['installation_id']
            now = stamp()
            if state == 'completed':
                if self._unresolved_count(installation_id):
                    raise Failure('shelf_unresolved', 'Unsettled effects remain for this installation; inspect them first.')
                held = self._holds_components(installation_id)
                if operation['kind'] == 'remove' and held:
                    raise Failure('shelf_unresolved', 'This removal has not released every component.')
                self.db.execute('UPDATE shelf_operations SET state=?,updated_at=? WHERE mutation_key=?', (state, now, mutation_key))
                result = 'removed' if not held else 'interrupted' if (
                    operation['kind'] in ('install', 'repair') and not self._complete(installation_id)) else 'installed'
                if operation['kind'] == 'update':
                    self.db.execute('UPDATE shelf_installations SET version=?,origin=?,state=?,updated_at=? WHERE id=?',
                                    (operation['target_version'], operation['target_origin'], result, now, installation_id))
                else:
                    self.db.execute('UPDATE shelf_installations SET state=?,updated_at=? WHERE id=?',
                                    (result, now, installation_id))
            else:
                self.db.execute("UPDATE shelf_operation_steps SET outcome='uncertain',updated_at=? WHERE mutation_key=? AND outcome='prepared'",
                                (now, mutation_key))
                self.db.execute('UPDATE shelf_operations SET state=?,updated_at=? WHERE mutation_key=?', (state, now, mutation_key))
                # A failed install or removal leaves whatever its steps recorded, and a
                # failed update or repair with unresolved effects may have changed
                # something: either way the installation needs inspection, not an
                # assumed rollback. Its installed pin (version and origin) stays.
                # Only an install holding no component and no unresolved effect left
                # nothing behind; it closes as removed so the package can be retried.
                unresolved = self._unresolved_count(installation_id)
                # An update that failed while only preparing beside the old version changed
                # nothing in use. Once it switched or released a part, the package is in
                # between two versions and needs attention.
                switched = operation['kind'] == 'update' and self._switched(mutation_key)
                if operation['kind'] == 'install' and not unresolved and not self._holds_components(installation_id):
                    self.db.execute("UPDATE shelf_installations SET state='removed',updated_at=? WHERE id=?",
                                    (now, installation_id))
                elif operation['kind'] in ('install', 'remove') or unresolved or switched:
                    self.db.execute("UPDATE shelf_installations SET state='interrupted',updated_at=? WHERE id=?",
                                    (now, installation_id))
        return self.receipt(mutation_key)

    # Components and ownership

    def attach(self, installation_id, kind, key, detail, *, role='owner'):
        """Record that an installation owns or shares a component; returns the component id.

        ``detail`` is the component's pinned identity (revision, digest, version; for a
        program's connection also its tree and the program inside it). An
        existing component is shared only when that pin is identical; a different
        pin under the same key is a collision for the caller to resolve explicitly.
        """
        with self.db:
            return self._attach(installation_id, kind, key, detail, role)

    def detach(self, installation_id, component_id):
        """Remove one membership. Returns the remaining members, derived from the record.

        When the owner leaves a shared component, the longest-standing remaining
        member becomes its owner, so a live component is never ownerless.
        """
        with self.db:
            return self._detach(installation_id, component_id)

    def owner(self, kind, key):
        """The installation owning a component, or None when no Shelf installation records it."""
        row = self.db.execute('''SELECT m.installation_id FROM shelf_components c JOIN shelf_members m
            ON m.component_id=c.id AND m.role='owner' WHERE c.kind=? AND c.key=?''', (kind, key)).fetchone()
        return row['installation_id'] if row else None

    def members(self, installation_id):
        """This installation's components with roles and the other members' count."""
        return [dict(id=r['id'], kind=r['kind'], key=r['key'], role=r['role'], detail=json.loads(r['detail']), others=r['others'])
                for r in self.db.execute('''SELECT c.*,m.role,(SELECT count(*) FROM shelf_members x
                    WHERE x.component_id=c.id AND x.installation_id!=m.installation_id) AS others
                    FROM shelf_members m JOIN shelf_components c ON c.id=m.component_id
                    WHERE m.installation_id=? ORDER BY c.kind,c.key''', (installation_id,))]

    def unresolved(self, installation_id):
        """Every prepared or uncertain step of this installation, oldest first."""
        return [dict(mutation_key=s['mutation_key'], ordinal=s['ordinal'], component_kind=s['component_kind'],
                     component_key=s['component_key'], action=s['action'], outcome=s['outcome'])
                for s in self.db.execute(f'''SELECT s.* FROM shelf_operation_steps s
                    JOIN shelf_operations o ON o.mutation_key=s.mutation_key
                    WHERE o.installation_id=? AND s.outcome IN {_UNRESOLVED}
                    ORDER BY o.created_at,s.mutation_key,s.ordinal''', (installation_id,))]

    def installation(self, installation_id):
        row = self._installation(installation_id)
        return None if row is None else dict(id=row['id'], identity=row['identity'], version=row['version'],
                                             origin=json.loads(row['origin']), state=row['state'])

    # Reading

    def receipt(self, mutation_key):
        operation = self._operation(mutation_key)
        if operation is None:
            return dict(status='absent')
        steps = [dict(ordinal=r['ordinal'], component_kind=r['component_kind'], component_key=r['component_key'],
                      action=r['action'], outcome=r['outcome'], code=r['code'])
                 for r in self.db.execute('SELECT * FROM shelf_operation_steps WHERE mutation_key=? ORDER BY ordinal', (mutation_key,))]
        return dict(status=operation['state'], kind=operation['kind'], mutation_key=mutation_key,
                    intent_sha256=operation['intent_sha256'], installation_id=operation['installation_id'],
                    target=self._target(operation), steps=steps)

    def lookup(self, mutation_key, intent_sha256):
        """Receipt for a recorded operation key; a different intent is a conflict, never a match."""
        operation = self._operation(mutation_key)
        if operation is None:
            return dict(status='absent')
        if operation['intent_sha256'] != intent_sha256:
            raise Failure('mutation_conflict', 'That operation key belongs to another Shelf intent.')
        return self.receipt(mutation_key)

    def status(self):
        rows = self.db.execute("SELECT * FROM shelf_installations WHERE state!='removed' ORDER BY identity,id LIMIT ?",
                               (MAX_LISTED + 1,)).fetchall()
        items = []
        for row in rows[:MAX_LISTED]:
            components = [dict(id=c['id'], kind=c['kind'], key=c['key'], role=c['role'],
                               members=c['members'], detail=json.loads(c['detail']))
                          for c in self.db.execute('''SELECT c.*,m.role,(SELECT count(*) FROM shelf_members x WHERE x.component_id=c.id) AS members
                              FROM shelf_members m JOIN shelf_components c ON c.id=m.component_id
                              WHERE m.installation_id=? ORDER BY c.kind,c.key''', (row['id'],))]
            last = self.db.execute('SELECT * FROM shelf_operations WHERE installation_id=? ORDER BY created_at DESC,mutation_key DESC LIMIT 1',
                                   (row['id'],)).fetchone()
            unresolved = [dict(mutation_key=s['mutation_key'], ordinal=s['ordinal'], component_kind=s['component_kind'],
                               component_key=s['component_key'], action=s['action'], outcome=s['outcome'])
                          for s in self.db.execute(f'''SELECT s.* FROM shelf_operation_steps s
                              JOIN shelf_operations o ON o.mutation_key=s.mutation_key
                              WHERE o.installation_id=? AND s.outcome IN {_UNRESOLVED}
                              ORDER BY o.created_at,s.mutation_key,s.ordinal LIMIT ?''', (row['id'], MAX_UNRESOLVED_LISTED))]
            items.append(dict(id=row['id'], identity=row['identity'], version=row['version'], origin=json.loads(row['origin']),
                              state=row['state'], components=components,
                              last_operation=(dict(mutation_key=last['mutation_key'], kind=last['kind'], state=last['state'],
                                                   target=self._target(last)) if last else None),
                              unresolved=unresolved, unresolved_total=self._unresolved_count(row['id'])))
        return dict(items=items, complete=len(rows) <= MAX_LISTED)

    # Restart

    def recover(self):
        """Called inside the store owner's restart transaction. Nothing is retried.

        Installed pins are untouched: an interrupted update keeps the old version and
        its operation keeps the target it was attempting.
        """
        now = stamp()
        self.db.execute("UPDATE shelf_operation_steps SET outcome='uncertain',updated_at=? WHERE outcome='prepared'", (now,))
        self.db.execute("UPDATE shelf_installations SET state='interrupted',updated_at=? WHERE id IN "
                        "(SELECT installation_id FROM shelf_operations WHERE state='applying') OR state IN ('installing','removing')", (now,))
        self.db.execute("UPDATE shelf_operations SET state='interrupted',updated_at=? WHERE state='applying'", (now,))

    # Internal

    def _operation(self, mutation_key):
        return self.db.execute('SELECT * FROM shelf_operations WHERE mutation_key=?', (mutation_key,)).fetchone()

    def _installation(self, installation_id):
        return self.db.execute('SELECT * FROM shelf_installations WHERE id=?', (installation_id,)).fetchone()

    def _applying(self, mutation_key):
        operation = self._operation(mutation_key)
        if operation is None or operation['state'] != 'applying':
            raise Failure('shelf_step', 'This operation is not applying; nothing changed.')
        return operation

    def _settle(self, mutation_key, ordinal, outcome, code, attach, detach):
        if code is not None and not _CODE.fullmatch(code):
            raise Failure('shelf_record', 'Step codes are short labels; nothing was saved.')
        operation = self._operation(mutation_key)
        if operation is None:
            raise Failure('shelf_missing', 'Unknown Shelf operation.')
        row = self.db.execute('SELECT outcome FROM shelf_operation_steps WHERE mutation_key=? AND ordinal=?',
                              (mutation_key, ordinal)).fetchone()
        if row is None or outcome not in _NEXT.get(row['outcome'], ()):
            raise Failure('shelf_step', 'This step cannot move to that outcome; nothing changed.')
        if row['outcome'] == 'prepared' and operation['state'] != 'applying':
            raise Failure('shelf_step', 'Only an applying operation records new outcomes.')
        if attach is not None and outcome != 'applied' or detach is not None and outcome == 'failed':
            raise Failure('shelf_step', 'Only a step that took effect changes membership; nothing changed.')
        self.db.execute('UPDATE shelf_operation_steps SET outcome=?,code=?,updated_at=? WHERE mutation_key=? AND ordinal=?',
                        (outcome, code, stamp(), mutation_key, ordinal))
        # Release first: an update replaces a component's pinned detail in one step.
        if detach is not None:
            self._detach(operation['installation_id'], detach)
        if attach is not None:
            self._attach(operation['installation_id'], *attach)
        self._touch(mutation_key)

    def _attach(self, installation_id, kind, key, detail, role='owner'):
        if kind not in KINDS or role not in ('owner', 'shared'):
            raise Failure('shelf_record', 'Invalid component; nothing was saved.')
        key, detail = _label(key, 'component key'), _document(detail, 'component detail')
        installation = self._installation(installation_id)
        if installation is None or installation['state'] == 'removed':
            raise Failure('shelf_missing', 'This installation is not present; nothing was saved.')
        row = self.db.execute('SELECT id,detail FROM shelf_components WHERE kind=? AND key=?', (kind, key)).fetchone()
        if row is None:
            if role != 'owner':
                raise Failure('shelf_owner', 'A new component needs an owning installation.')
            component = uuid.uuid4().hex
            self.db.execute('INSERT INTO shelf_components VALUES(?,?,?,?,?)', (component, kind, key, detail, stamp()))
        else:
            component = row['id']
            if row['detail'] != detail:
                raise Failure('shelf_collision', 'A component with this name is already installed with different pinned contents. '
                              'Choose to replace it, keep it, or cancel; nothing was saved.')
            owned = self.db.execute("SELECT installation_id FROM shelf_members WHERE component_id=? AND role='owner'",
                                    (component,)).fetchone()
            if role == 'owner' and owned is not None and owned['installation_id'] != installation_id:
                raise Failure('shelf_owner', 'Another installation owns this component; share it or choose a replacement.')
        self.db.execute('INSERT OR IGNORE INTO shelf_members VALUES(?,?,?,?)', (installation_id, component, role, stamp()))
        return component

    def _detach(self, installation_id, component_id):
        role = self.db.execute('SELECT role FROM shelf_members WHERE installation_id=? AND component_id=?',
                               (installation_id, component_id)).fetchone()
        self.db.execute('DELETE FROM shelf_members WHERE installation_id=? AND component_id=?', (installation_id, component_id))
        remaining = [row['installation_id'] for row in self.db.execute(
            'SELECT installation_id FROM shelf_members WHERE component_id=? ORDER BY added_at,installation_id', (component_id,))]
        if not remaining:
            self.db.execute('DELETE FROM shelf_components WHERE id=?', (component_id,))
        elif role is not None and role['role'] == 'owner':
            self.db.execute("UPDATE shelf_members SET role='owner' WHERE installation_id=? AND component_id=?",
                            (remaining[0], component_id))
        return remaining

    def _complete(self, installation_id):
        """Every step the install declared applied, and each component it registered still held.

        After a finished update, the parts that update put in place are what must still
        be held: it replaced what the install had declared.
        """
        held = {(r['kind'], r['key']) for r in self.db.execute(
            'SELECT c.kind,c.key FROM shelf_members m JOIN shelf_components c ON c.id=m.component_id WHERE m.installation_id=?',
            (installation_id,))}
        if self._between_versions(installation_id):
            return False
        updated = self.db.execute("SELECT mutation_key FROM shelf_operations WHERE installation_id=? AND kind='update' "
                                  "AND state='completed' ORDER BY created_at DESC,mutation_key DESC LIMIT 1",
                                  (installation_id,)).fetchone()
        if updated is not None:
            steps = self.db.execute("SELECT component_kind,component_key FROM shelf_operation_steps WHERE mutation_key=? "
                                    "AND outcome='applied' AND action IN ('register','materialize')",
                                    (updated['mutation_key'],)).fetchall()
            return bool(held) and all((s['component_kind'], s['component_key']) in held for s in steps)
        steps = self.db.execute('''SELECT s.outcome,s.action,s.component_kind,s.component_key FROM shelf_operation_steps s
            JOIN shelf_operations o ON o.mutation_key=s.mutation_key
            WHERE o.installation_id=? AND o.kind='install' ''', (installation_id,)).fetchall()
        # An explicit reinstall can supply a part whose original install failed.
        # Keep that original receipt intact; only a completed repair with an applied
        # registration/materialization provides replacement evidence. Inspection alone
        # has no such steps and cannot turn a partial install into a complete one.
        recovered = {(s['component_kind'], s['component_key']) for s in self.db.execute('''
            SELECT s.component_kind,s.component_key FROM shelf_operation_steps s
            JOIN shelf_operations o ON o.mutation_key=s.mutation_key
            WHERE o.installation_id=? AND o.kind='repair' AND o.state='completed'
            AND s.outcome='applied' AND s.action IN ('register','materialize')''', (installation_id,))}
        return all(s['outcome'] == 'applied' or (s['component_kind'], s['component_key']) in recovered for s in steps) and all(
            (s['component_kind'], s['component_key']) in held for s in steps if s['action'] in ('register', 'materialize'))

    def _switched(self, mutation_key):
        """Whether an update got past preparing: it switched or released some part."""
        return self.db.execute(
            "SELECT 1 FROM shelf_operation_steps WHERE mutation_key=? AND outcome IN ('applied','skipped') "
            "AND action!='materialize' AND NOT (outcome='skipped' AND code='not_attempted') LIMIT 1",
            (mutation_key,)).fetchone() is not None

    def _between_versions(self, installation_id):
        """Whether the latest update of this installation ended after switching only some of its parts."""
        last = self.db.execute("SELECT mutation_key,state FROM shelf_operations WHERE installation_id=? AND kind IN ('update','remove') "
                               "ORDER BY created_at DESC,mutation_key DESC LIMIT 1", (installation_id,)).fetchone()
        return (last is not None and last['state'] != 'completed' and self._operation(last['mutation_key'])['kind'] == 'update'
                and self._switched(last['mutation_key']))

    def _holds_components(self, installation_id):
        return self.db.execute('SELECT 1 FROM shelf_members WHERE installation_id=? LIMIT 1',
                               (installation_id,)).fetchone() is not None

    def _unresolved_count(self, installation_id):
        return self.db.execute(f'''SELECT count(*) FROM shelf_operation_steps s JOIN shelf_operations o
            ON o.mutation_key=s.mutation_key WHERE o.installation_id=? AND s.outcome IN {_UNRESOLVED}''',
            (installation_id,)).fetchone()[0]

    @staticmethod
    def _target(operation):
        if operation['target_version'] is None:
            return None
        return dict(version=operation['target_version'], origin=json.loads(operation['target_origin']))

    def _touch(self, mutation_key):
        self.db.execute('UPDATE shelf_operations SET updated_at=? WHERE mutation_key=?', (stamp(), mutation_key))
