"""Stable identities; individually sourced assertions, never notebook-owned facts.

Original artifacts own evidence. Revisions store typed offsets and hashes, not
another copy of the conversation. Read eligibility is checked before projections;
source withdrawal cannot delete an independent identity or another assertion.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import sqlite3
import time
import uuid

from ...core.people import RELATIONS, SYMMETRIC, WRITES, family_paths, identity, terms, validate, relation_qualifiers
from ...core.records import Failure
from .history import encoded

PEOPLE_SCHEMA = '''
CREATE TABLE people_ids(id TEXT PRIMARY KEY, merged_into TEXT REFERENCES people_ids(id), created_at TEXT NOT NULL);
INSERT INTO people_ids VALUES('self',NULL,'');
CREATE INDEX people_merged ON people_ids(merged_into,id);
CREATE TABLE people_merge_sources(person_id TEXT PRIMARY KEY REFERENCES people_ids(id), artifact_id TEXT NOT NULL,
 sha256 TEXT NOT NULL,start INTEGER NOT NULL,bytes INTEGER NOT NULL);
CREATE TABLE people_assertions(id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
CREATE TABLE people_versions(sequence INTEGER PRIMARY KEY, id TEXT NOT NULL REFERENCES people_assertions(id),
 revision INTEGER NOT NULL, subject TEXT NOT NULL REFERENCES people_ids(id), object TEXT REFERENCES people_ids(id),
 kind TEXT NOT NULL, value TEXT NOT NULL, qualifier TEXT NOT NULL, certainty TEXT NOT NULL,
 status TEXT NOT NULL, since TEXT, until TEXT, withdrawn INTEGER NOT NULL CHECK(withdrawn IN (0,1)),
 observed_at TEXT NOT NULL, UNIQUE(id,revision));
CREATE INDEX people_subject ON people_versions(subject,sequence);
CREATE INDEX people_object ON people_versions(object,sequence);
CREATE TABLE people_sources(id TEXT NOT NULL, revision INTEGER NOT NULL, artifact_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, start INTEGER NOT NULL, bytes INTEGER NOT NULL,
 PRIMARY KEY(id,revision), FOREIGN KEY(id,revision) REFERENCES people_versions(id,revision));
CREATE INDEX people_source_artifact ON people_sources(artifact_id,id,revision);
CREATE TABLE people_source_withdrawals(artifact_id TEXT PRIMARY KEY, withdrawn_at TEXT NOT NULL);
CREATE TABLE people_name_terms(term TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(term,id,revision), FOREIGN KEY(id,revision) REFERENCES people_versions(id,revision));
CREATE TABLE people_operations(operation_key TEXT PRIMARY KEY, intent TEXT NOT NULL, turn_id TEXT,
 receipt TEXT NOT NULL);
CREATE INDEX people_operation_turn ON people_operations(turn_id,operation_key);
CREATE TABLE people_grants(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 mode TEXT NOT NULL CHECK(mode IN ('off','read','write')), revision INTEGER NOT NULL);
CREATE TABLE people_grant_operations(mutation_key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
 intent TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE people_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),epoch INTEGER NOT NULL);
INSERT INTO people_state VALUES(1,0);
CREATE TRIGGER people_source_grant_change AFTER UPDATE OF enabled,bank_id ON memory_grants
 BEGIN UPDATE people_state SET epoch=epoch+1 WHERE singleton=1; END;
CREATE TRIGGER people_source_grant_delete AFTER DELETE ON memory_grants
 BEGIN UPDATE people_state SET epoch=epoch+1 WHERE singleton=1; END;
CREATE INDEX people_turn_user ON turns(user_id);
'''

# Every projection uses this indexed, row-local test. Names cannot outlive their
# supporting source merely because their opaque identity is still retained.
ELIGIBLE = '''EXISTS(SELECT 1 FROM people_sources s JOIN artifacts a ON a.id=s.artifact_id
 JOIN recall_turns t ON t.user_id=a.id JOIN memory_grants g ON g.conversation_id=t.conversation_id AND g.enabled=1
 WHERE s.id=v.id AND s.revision=v.revision AND a.kind='user_message' AND a.sha256=s.sha256
 AND NOT EXISTS(SELECT 1 FROM people_source_withdrawals w WHERE w.artifact_id=a.id))'''
MERGE_ELIGIBLE = '''EXISTS(SELECT 1 FROM people_merge_sources m JOIN artifacts a ON a.id=m.artifact_id AND a.sha256=m.sha256
 JOIN recall_turns t ON t.user_id=a.id JOIN memory_grants g ON g.conversation_id=t.conversation_id AND g.enabled=1
 WHERE m.person_id=p.id AND a.kind='user_message' AND NOT EXISTS(SELECT 1 FROM people_source_withdrawals w WHERE w.artifact_id=a.id))'''
HEAD = 'JOIN people_assertions h ON h.id=v.id AND h.revision=v.revision'


class PeopleStore:
    def __init__(self, store):
        self.store, self.db, self.ledger = store, store.db, None

    def query(self, sql, parameters=()):
        if self.ledger:
            self.ledger.spend_sql(100)
        return self.db.execute(sql, parameters)

    @contextmanager
    def transaction(self, ledger=None, *, write=False):
        if self.db.in_transaction:
            raise Failure('people_busy', 'Another operation owns the archive.')
        self.ledger = ledger
        deadline = ledger.deadline if ledger else time.monotonic() + 5
        steps, stopped, committing = 0, None, False
        def progress():
            nonlocal steps, stopped
            steps += 100
            try:
                if ledger:
                    ledger.spend_sql(100)
                elif steps > 200000:
                    raise Failure('people_work_limit', 'People operation reached its work bound; narrow the request.')
                if time.monotonic() >= deadline:
                    raise Failure('timeout', 'People operation reached its deadline.')
            except Failure as error:
                stopped = error
                return 1
            return 0
        self.db.set_progress_handler(progress, 100)
        try:
            if progress():
                raise stopped
            self.query('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield
            if progress():
                raise stopped
            committing = True
            self.db.commit()
        except BaseException as error:
            unknown = write and committing and not self.db.in_transaction
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            if unknown:
                raise Failure('people_outcome_unknown', 'Inspect the existing operation receipt before retrying.') from error
            if stopped:
                raise stopped from None
            if isinstance(error, sqlite3.Error):
                raise Failure('people_store_unavailable', 'People storage failed; inspect the existing operation receipt.') from error
            raise
        finally:
            self.db.set_progress_handler(None, 0)
            self.ledger = None

    def epoch(self, ledger=None):
        if ledger is not None:
            with self.transaction(ledger):
                return self.epoch()
        return self.query('SELECT epoch FROM people_state WHERE singleton=1').fetchone()[0] + self.store.access_settings.value['revision']

    def bump(self):
        self.query('UPDATE people_state SET epoch=epoch+1 WHERE singleton=1')

    def status(self, cid):
        conv = self.store.conversation(cid)
        row = self.query('SELECT mode,revision FROM people_grants WHERE conversation_id=?', (cid,)).fetchone()
        available = conv.history_enabled and conv.memory_bank_id is not None and self.query(
            'SELECT 1 FROM memory_grants WHERE conversation_id=? AND enabled=1', (cid,)).fetchone() is not None
        return dict(conversation_id=cid, mode=conv.people_mode, revision=row['revision'] if row else 0,
                    available=bool(available))

    def access(self, args):
        fields = {'read': {'action','conversation_id'}, 'set': {'action','conversation_id','mode','expected_revision','mutation_key'},
                  'lookup': {'action','conversation_id','mutation_key','intent_sha256'}}
        action, cid = args.get('action'), args.get('conversation_id')
        if not isinstance(action, str) or action not in fields or set(args) != fields[action] or not self.store._identity(cid):
            raise Failure('people_access', 'Use an exact people permission request.')
        if action != 'read' and not self.store._identity(args['mutation_key']):
            raise Failure('people_access', 'Permission change needs a mutation identity.')
        if action == 'set' and (args['mode'] not in ('off','read','write') or type(args['expected_revision']) is not int
                                or not 0 <= args['expected_revision'] < 2**53):
            raise Failure('people_access', 'Choose off/read/write with the current permission revision.')
        with self.transaction(write=action == 'set'):
            current = self.status(cid)
            if action == 'read':
                return current
            prior = self.query('SELECT * FROM people_grant_operations WHERE mutation_key=?', (args['mutation_key'],)).fetchone()
            if action == 'lookup':
                if prior and (prior['conversation_id'] != cid or prior['intent'] != args['intent_sha256']):
                    raise Failure('people_conflict','Permission receipt belongs to another exact intent.')
                return json.loads(prior['receipt']) | {'replayed': True} if prior else dict(status='absent')
            intent = hashlib.sha256(encoded({k:args[k] for k in ('action','conversation_id','mode','expected_revision')}).encode()).hexdigest()
            if prior:
                if prior['intent'] != intent:
                    raise Failure('people_conflict', 'Permission mutation belongs to a different intent.')
                return json.loads(prior['receipt']) | {'replayed': True}
            self.store.access_settings.require_conversation_control('people')
            if current['revision'] != args['expected_revision']:
                raise Failure('people_conflict', 'People permission changed; read it again.')
            if args['mode'] != 'off' and not current['available']:
                raise Failure('people_forbidden', 'People access cannot grant missing conversation memory access.')
            changed = args['mode'] != current['mode']
            revision = current['revision'] + int(changed)
            self.query('INSERT INTO people_grants VALUES(?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET mode=excluded.mode,revision=excluded.revision',
                       (cid,args['mode'],revision))
            if changed:
                self.bump()
            result = dict(status='committed' if changed else 'unchanged', conversation_id=cid, mode=args['mode'], revision=revision,
                          mutation_key=args['mutation_key'], intent_sha256=intent, replayed=False)
            self.query('INSERT INTO people_grant_operations VALUES(?,?,?,?)', (args['mutation_key'],cid,intent,encoded(result)))
            return result

    def guard(self, cid, writing=False, ledger=None):
        if ledger is not None:
            with self.transaction(ledger):
                return self.guard(cid, writing)
        state = self.status(cid)
        if not state['available'] or state['mode'] == 'off' or writing and state['mode'] != 'write':
            raise Failure('people_forbidden', 'People reading/writing is not granted for this conversation.')

    def token(self, value):
        key = self.query('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
        packet = encoded([self.store.state_id, value]).encode()
        return 'P1.' + base64.urlsafe_b64encode(packet + hmac.digest(key, packet, 'sha256')).decode().rstrip('=')

    def decode(self, token):
        try:
            if not token.startswith('P1.') or len(token) > 4096:
                raise ValueError
            raw = base64.b64decode(token[3:] + '=' * (-len(token[3:]) % 4), altchars=b'-_', validate=True)
            packet, tag = raw[:-32], raw[-32:]
            key = self.query('SELECT handle_key FROM memory_banks WHERE slot=1').fetchone()[0]
            if not hmac.compare_digest(hmac.digest(key, packet, 'sha256'), tag):
                raise ValueError
            state, value = json.loads(packet)
            if state != self.store.state_id:
                raise ValueError
            return value
        except (ValueError, TypeError, UnicodeError):
            raise Failure('people_reference', 'People reference is invalid; read current state again.') from None

    def root(self, ident):
        seen = set()
        while ident not in seen and len(seen) < 128:
            seen.add(ident)
            row = self.query('SELECT merged_into,'+MERGE_ELIGIBLE+' AS eligible FROM people_ids p WHERE p.id=?', (ident,)).fetchone()
            if row is None:
                raise Failure('people_identity', 'Person identity is unavailable.')
            if row[0] is None or not row[1]:
                return ident
            ident = row[0]
        raise Failure('people_identity', 'Identity resolution is cyclic or exceeds its bound.')

    def members(self, ident):
        root = self.root(ident)
        rows = self.query('''WITH RECURSIVE ids(id) AS (SELECT ? UNION SELECT p.id FROM people_ids p JOIN ids ON p.merged_into=ids.id WHERE '''+MERGE_ELIGIBLE+''')
            SELECT id FROM ids LIMIT 1001''', (root,)).fetchall()
        if len(rows) > 1000:
            raise Failure('people_work_limit', 'This identity group exceeds the bounded projection; no incomplete identity was returned.')
        return [row[0] for row in rows]

    def source(self, row):
        saved = self.query('''SELECT s.*,a.bytes AS total_bytes,t.id AS turn_id,t.conversation_id FROM people_sources s
            JOIN recall_turns t ON t.user_id=s.artifact_id JOIN memory_grants g ON g.conversation_id=t.conversation_id AND g.enabled=1
            JOIN artifacts a ON a.id=s.artifact_id AND a.sha256=s.sha256 AND a.kind='user_message'
            WHERE s.id=? AND s.revision=? AND NOT EXISTS(SELECT 1 FROM people_source_withdrawals w WHERE w.artifact_id=s.artifact_id)''',
            (row['id'],row['revision'])).fetchone()
        if saved is None:
            raise Failure('people_source_unavailable', 'The original supporting user passage is unavailable.')
        return self.source_page(saved)

    def source_page(self, saved):
        # Reuse exact chunk verification; only the selected passage is loaded.
        if self.ledger:
            self.ledger.spend_payload(min(saved['total_bytes'], 2 * 32768))
        page = self.store.body_page(saved['conversation_id'],saved['turn_id'],saved['artifact_id'],saved['start'],saved['bytes'])
        if page['sha256'] != saved['sha256'] or page['end'] != saved['start'] + saved['bytes']:
            raise Failure('store_integrity', 'People source extent changed.')
        return dict(artifact_id=saved['artifact_id'], conversation_id=saved['conversation_id'], turn_id=saved['turn_id'],
                    sha256=saved['sha256'], offset=saved['start'], end=page['end'], quote=page['content'])

    def current_source(self, cid, tid, args):
        turn = self.store._row(tid)
        if turn['conversation_id'] != cid or turn['status'] != 'running':
            raise Failure('people_forbidden', 'People changes require the current active user turn.')
        meta = self.query('SELECT sha256,bytes FROM artifacts WHERE id=? AND kind=\'user_message\'', (turn['user_id'],)).fetchone()
        if self.query('SELECT 1 FROM people_source_withdrawals WHERE artifact_id=?',(turn['user_id'],)).fetchone():
            raise Failure('people_source_unavailable','This original passage was withdrawn from people capture.')
        if self.ledger:
            self.ledger.spend_payload(meta['bytes'])
        raw = self.store._body(turn['user_id']).encode()
        quote = args['source_quote'].encode()
        offset = args.get('source_offset')
        if offset is None:
            if raw.count(quote) != 1:
                raise Failure('people_quote', 'Quote must occur once in the current user message, or supply its exact UTF-8 source_offset.')
            offset = raw.index(quote)
        if raw[offset:offset+len(quote)] != quote:
            raise Failure('people_quote', 'Source quote must match the current user message exactly.')
        return (turn['user_id'],meta['sha256'],offset,len(quote))

    def fit(self, result, maximum, budget):
        if len(encoded(result).encode()) > maximum or budget is not None and not budget.fits(result):
            raise Failure('people_result_capacity', 'The complete people result cannot fit; no change committed. Request a smaller page.')
        return result

    def execute(self, args, *, conversation_id, turn_id=None, operation_key=None, ledger=None,
                maximum=65536, budget=None, read_refs=frozenset()):
        args = validate(args)
        writing = args['action'] in WRITES
        with self.transaction(ledger, write=writing):
            self.guard(conversation_id, writing)
            fit = lambda result: self.fit(dict(domain='people', **result), maximum, budget)
            if not writing:
                result = self.read(args, fit)
            else:
                if not isinstance(operation_key,str) or not 1 <= len(operation_key) <= 256:
                    raise Failure('people_arguments','A bounded application operation identity is required.')
                # JSON object field order is not a new intent. The application
                # key and durable comparison must agree after a lost reply.
                intent = hashlib.sha256(encoded([conversation_id,dict(sorted(args.items()))]).encode()).hexdigest()
                prior = self.query('SELECT * FROM people_operations WHERE operation_key=?',(operation_key,)).fetchone()
                if prior:
                    if prior['intent'] != intent:
                        raise Failure('people_conflict','Operation identity belongs to another intent.')
                    result = fit(json.loads(prior['receipt']) | {'replayed':True})
                else:
                    source = self.current_source(conversation_id,turn_id,args)
                    result = fit(self.write(args,source,read_refs))
                    self.query('INSERT INTO people_operations VALUES(?,?,?,?)',
                               (operation_key,intent,turn_id,encoded({k:v for k,v in result.items() if k != 'domain'})))
                    self.bump()
            if ledger:
                ledger.spend_payload(len(encoded(result).encode()))
            return result

    def write(self, args, source, reads):
        action = args['action']
        if action in ('merge','split'):
            preview = self.decode(args['preview'])
            expected = 'merge' if action == 'merge' else 'split'
            if (not isinstance(preview,list) or len(preview)!=4 or preview[0]!=expected or preview[3]!=self.epoch()
                    or action=='split' and preview[1]!=args['person_id']):
                raise Failure('people_conflict','Identity preview changed; inspect a fresh preview before applying.')
            person, target = preview[1:3]
            if action=='merge':
                if self.root(person)!=person or self.root(target)!=target or person==target or 'self' in (person,target):
                    raise Failure('people_conflict','Identity merge is no longer valid.')
                self.query('UPDATE people_ids SET merged_into=? WHERE id=?',(target,person))
                self.query('INSERT INTO people_merge_sources VALUES(?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET artifact_id=excluded.artifact_id,sha256=excluded.sha256,start=excluded.start,bytes=excluded.bytes',(person,*source))
                # A merge cannot silently turn a recorded connection into self-family.
                members = set(self.members(target))
                if self.query('SELECT 1 FROM people_versions v '+HEAD+' WHERE v.withdrawn=0 AND v.kind=\'relation\' AND v.subject IN ('+
                        ','.join('?' for _ in members)+') AND v.object IN ('+','.join('?' for _ in members)+') AND '+ELIGIBLE+' LIMIT 1',
                        (*members,*members)).fetchone():
                    raise Failure('people_conflict','These identities have a recorded connection. Correct it before merging.')
                edges,complete=self.neighbors(target,project=False)
                if not complete:
                    raise Failure('people_work_limit','Cannot certify merged ancestry within the neighborhood bound.')
                for edge in edges:
                    if edge['value']=='parent' and edge['status']=='current' and self.root(edge['subject'])==target:
                        self.no_parent_cycle(target,self.root(edge['object']))
            else:
                row=self.query('SELECT merged_into FROM people_ids WHERE id=?',(person,)).fetchone()
                if not row or row[0]!=target:
                    raise Failure('people_conflict','This merge is no longer current.')
                self.query('UPDATE people_ids SET merged_into=NULL WHERE id=?',(person,))
                self.query('DELETE FROM people_merge_sources WHERE person_id=?',(person,))
            return dict(status='committed',action=action,person_id=person,target_id=target,
                        source=dict(artifact_id=source[0],sha256=source[1],offset=source[2],bytes=source[3]),replayed=False)
        old = None
        if action in ('correct','withdraw'):
            ref = self.decode(args['read_ref'])
            if not isinstance(ref,list) or len(ref)!=3 or ref[0]!='assertion':
                raise Failure('people_reference','Use a current assertion read_ref.')
            old = self.query('SELECT v.* FROM people_versions v '+HEAD+' WHERE v.id=? AND v.revision=? AND '+ELIGIBLE,
                             (ref[1],ref[2])).fetchone()
            if old is None or old['withdrawn']:
                raise Failure('people_conflict','Assertion changed or is unavailable; read it again.')
            if args['read_ref'] not in reads:
                raise Failure('people_read_required','Read the current assertion in this turn before changing it.')
        fields = ('subject','object','kind','value','qualifier','certainty','status','since','until','withdrawn')
        record = dict(old) if old else dict(subject=args['subject'],object=None,kind=args['kind'],value=args['value'],
                                          qualifier='unspecified',certainty='explicit',status='current',since=None,until=None,withdrawn=0)
        for field in fields:
            if field in args:
                record[field] = args[field]
        if action=='withdraw':
            record['withdrawn']=1
        if record['subject']=='new':
            if record['kind']=='relation':
                raise Failure('people_identity','Create a name or fact first, then use its returned person ID.')
            record['subject']=uuid.uuid4().hex
            self.query('INSERT INTO people_ids VALUES(?,NULL,?)',(record['subject'],datetime.now(timezone.utc).isoformat()))
        else:
            self.root(record['subject'])
        if record['kind']=='name' and not record['withdrawn']:
            if len(record['value'].encode())>200 or not re_name(args['source_quote'],record['value']):
                raise Failure('people_quote','A name or alias must occur as a name in the exact source quotation.')
        if record['kind']=='relation':
            if record['value'] not in RELATIONS or not record['object'] or self.root(record['subject'])==self.root(record['object']):
                raise Failure('people_relation','Choose a supported relation and two distinct people.')
            options=relation_qualifiers(record['value'])
            if record['qualifier'] not in options:
                raise Failure('people_relation', 'Allowed qualifiers for ' + record['value'] + ': ' + ', '.join(options) + '. Use only the qualifier supported by the user; no change ran.')
        elif record['object'] is not None or record['qualifier']!='unspecified':
            raise Failure('people_relation','Only relationships have an object or family qualifier.')
        if record['since'] and record['until'] and record['since']>record['until']:
            raise Failure('people_dates','Relationship dates are reversed.')
        ident, revision = (old['id'],old['revision']+1) if old else (uuid.uuid4().hex,1)
        if old is None:
            self.query('INSERT INTO people_assertions VALUES(?,?)',(ident,revision))
        else:
            self.query('UPDATE people_assertions SET revision=? WHERE id=?',(revision,ident))
        self.query('INSERT INTO people_versions(id,revision,'+','.join(fields)+',observed_at) VALUES('+','.join('?' for _ in range(13))+')',
                   (ident,revision,*(record[k] for k in fields),datetime.now(timezone.utc).isoformat()))
        self.query('INSERT INTO people_sources VALUES(?,?,?,?,?,?)',(ident,revision,*source))
        if record['kind']=='name' and not record['withdrawn']:
            for term in terms(record['value']):
                self.query('INSERT INTO people_name_terms VALUES(?,?,?)',(term,ident,revision))
        if record['kind']=='relation' and record['value'] in ('parent','grandparent') and record['status']=='current' and not record['withdrawn']:
            self.no_parent_cycle(self.root(record['subject']),self.root(record['object']))
        return dict(status='committed',action=action,person_id=record['subject'],assertion_id=ident,revision=revision,replayed=False)

    def project(self, row, *, reference=True):
        value = {k:row[k] for k in ('id','revision','subject','object','kind','value','qualifier','certainty','status','since','until','withdrawn','observed_at')}
        value['source'] = self.source(row)
        value['declared_subject'] = value['subject']
        value['subject'] = self.root(value['subject'])
        if value['object']:
            value['declared_object'] = value['object']
            value['object'] = self.root(value['object'])
        value['identity_sources'] = []
        for original in (row['subject'],row['object']):
            visited=set()
            while original and original not in visited and self.root(original)!=original:
                visited.add(original)
                link=self.query('''SELECT p.merged_into,m.*,a.bytes AS total_bytes,t.id AS turn_id,t.conversation_id
                    FROM people_ids p JOIN people_merge_sources m ON m.person_id=p.id
                    JOIN artifacts a ON a.id=m.artifact_id JOIN recall_turns t ON t.user_id=a.id WHERE p.id=?''',(original,)).fetchone()
                value['identity_sources'].append(dict(person_id=original,target_id=link['merged_into'],
                    source=self.source_page(link)))
                original=link['merged_into']
        if reference:
            value['read_ref'] = self.token(['assertion',row['id'],row['revision']])
        return value

    def neighbors(self, person, *, project=True):
        members=self.members(person)
        slots=','.join('?' for _ in members)
        rows=self.query('SELECT v.* FROM people_versions v '+HEAD+
            ' WHERE v.withdrawn=0 AND v.kind=\'relation\' AND (v.subject IN ('+slots+') OR v.object IN ('+slots+')) AND '+
            ELIGIBLE+' ORDER BY v.sequence LIMIT 1001',(*members,*members)).fetchall()
        return ([self.project(row,reference=False) if project else dict(row) for row in rows[:1000]],len(rows)<=1000)

    def read(self, args, fit):
        action=args['action']
        if action in ('preview_merge','preview_split'):
            person=args['person_id']
            if action=='preview_merge':
                target=args['target_id']
                if 'self' in (person,target) or self.root(person)!=person or self.root(target)!=target or person==target:
                    raise Failure('people_identity','Select two distinct unmerged person identities; self cannot be merged.')
                left,right=self.members(person),self.members(target)
                return fit(dict(status='preview',person_id=person,target_id=target,
                    source_identities=left,target_identities=right,
                    effect='Group identities for reads. Original assertions keep their declared person IDs. Split reverses this link; later claims stay with their declared IDs.',
                    preview=self.token(['merge',person,target,self.epoch()])))
            row=self.query('SELECT merged_into FROM people_ids WHERE id=?',(person,)).fetchone()
            if not row or row[0] is None:
                raise Failure('people_identity','Select the original identity that was merged, not its target.')
            return fit(dict(status='preview',person_id=person,target_id=row[0],
                effect='Detach this original identity and its attached identities. Restore reads of their original claims; later claims on the target stay there.',
                preview=self.token(['split',person,row[0],self.epoch()])))
        digest=hashlib.sha256(encoded(dict(sorted((k,v) for k,v in args.items() if k not in ('cursor','limit')))).encode()).hexdigest()
        after=0
        if args.get('cursor'):
            cursor=self.decode(args['cursor'])
            if not isinstance(cursor,list) or len(cursor)!=4 or cursor[:3]!=['page',digest,self.epoch()] or type(cursor[3]) is not int:
                raise Failure('people_cursor','People/source state changed or cursor belongs to another query. Start a fresh page.')
            after=cursor[3]
        limit=args.get('limit',12)
        extra={}
        if action=='find':
            query_terms=terms(args.get('query',''))
            if query_terms:
                slots=','.join('?' for _ in query_terms)
                # Indexed term probes, not a scan of all names on every question.
                rows=self.query('SELECT DISTINCT v.* FROM people_name_terms n JOIN people_versions v ON v.id=n.id AND v.revision=n.revision '+HEAD+
                    ' WHERE n.term IN ('+slots+') AND v.sequence>? AND v.withdrawn=0 AND '+ELIGIBLE+
                    ' ORDER BY v.sequence LIMIT ?',(*query_terms,after,limit+1)).fetchall()
            elif args.get('query','').strip():
                rows=[]
            else:
                rows=self.query('SELECT v.* FROM people_versions v '+HEAD+
                    ' WHERE v.kind=\'name\' AND v.sequence>? AND v.withdrawn=0 AND '+ELIGIBLE+
                    ' ORDER BY v.sequence LIMIT ?',(after,limit+1)).fetchall()
            def project(row):
                self.source(row)  # Verify only matched evidence, never scan other people.
                return dict(person_id=self.root(row['subject']),declared_person_id=row['subject'],
                            name=row['value'],assertion_id=row['id'],certainty=row['certainty'],status=row['status'])
            extra['identity_policy']='Matches may include distinct people with the same name. Read and clarify; never auto-merge.'
        elif action=='read':
            members=self.members(args['person_id'])
            slots=','.join('?' for _ in members)
            historical=args.get('view','current')=='history'
            rows=self.query('SELECT v.* FROM people_versions v '+('' if historical else HEAD)+
                ' WHERE v.sequence>? AND (v.subject IN ('+slots+') OR v.object IN ('+slots+')) AND '+
                ('1' if historical else 'v.withdrawn=0')+' AND '+ELIGIBLE+' ORDER BY v.sequence LIMIT ?',
                (after,*members,*members,limit+1)).fetchall()
            project=lambda row:self.project(row,reference=not historical)
            extra.update(person_id=self.root(args['person_id']),view='history' if historical else 'current')
        elif action=='kinship':
            start,end=self.root(args['relative_to']),self.root(args['person_id'])
            cache={}
            def neighbors(ident):
                if ident not in cache:
                    cache[ident]=self.neighbors(ident)
                return cache[ident]
            paths,exhaustive,work=family_paths(start,end,neighbors,args['as_of'])
            rows=[dict(sequence=i+1,**path) for i,path in enumerate(paths) if i+1>after][:limit+1]
            project=lambda row:{k:v for k,v in row.items() if k!='sequence'}
            extra.update(as_of=args['as_of'],exhaustive=exhaustive,visited=work,
                         scope='Supported family paths only; no social transitivity or unstated intermediate people.',
                         limitation=None if exhaustive else 'Traversal bound reached. Missing paths do not establish absence.')
        else:
            raise Failure('people_arguments','Unsupported people read.')
        result=dict(status='complete',items=[],next_cursor=None,complete=False,**extra)
        fit(result)
        consumed=0
        for row in rows[:limit]:
            item=project(row)
            candidate=dict(result,items=result['items']+[item],next_cursor=self.token(['page',digest,self.epoch(),row['sequence']]))
            try:
                fit(candidate)
            except Failure as error:
                if error.code!='people_result_capacity':
                    raise
                break
            result=candidate
            consumed+=1
        if rows and not consumed:
            raise Failure('people_result_capacity','One complete people item cannot fit; no truncated claim or source was delivered.')
        result['complete']=len(rows)<=consumed
        if result['complete']:
            result['next_cursor']=None
        return fit(result)

    def no_parent_cycle(self, parent, child):
        pending, seen = [child], set()
        while pending:
            node=pending.pop()
            if node==parent:
                raise Failure('people_cycle','This parent assertion would create an ancestry cycle.')
            if node in seen:
                continue
            seen.add(node)
            if len(seen)>1000:
                raise Failure('people_work_limit','Cannot certify ancestry within this operation bound; no assertion committed.')
            rows,complete=self.neighbors(node,project=False)
            if not complete:
                raise Failure('people_work_limit','Cannot certify the complete parent neighborhood; no assertion committed.')
            pending.extend(self.root(e['object']) for e in rows if e['value'] in ('parent','grandparent') and e['status']=='current'
                           and self.root(e['subject'])==node)

    def withdraw_source(self, artifact_id):
        """Local lifecycle hook: revoke only assertions citing this original.

        This is logical withdrawal, not physical erasure or conversation deletion.
        It is intentionally outside model tools and never creates a note cascade.
        """
        if not self.store._identity(artifact_id):
            raise Failure('people_source','Use an exact source artifact ID.')
        with self.transaction(write=True):
            self.query('INSERT OR IGNORE INTO people_source_withdrawals VALUES(?,?)',
                       (artifact_id,datetime.now(timezone.utc).isoformat()))
            self.bump()
        return dict(status='withdrawn',artifact_id=artifact_id)

    def history_actions(self, tid, ledger):
        with self.transaction(ledger):
            rows=self.query('SELECT receipt FROM people_operations WHERE turn_id=? ORDER BY operation_key LIMIT ?',
                            (tid,self.store.execution.for_turn(tid).operations)).fetchall()
            # Bodies/names never enter the action audit. It records committed
            # effects, not a fresh assertion that the claim remains true.
            return [json.loads(row[0]) for row in rows]


def re_name(quote, name):
    import re
    return bool(re.search(r'(?<!\w)'+re.escape(name.casefold())+r'(?!\w)',quote.casefold()))
