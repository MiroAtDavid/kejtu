"""Local web settings and immutable, exchange-owned fetched sources."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from ...core.records import Failure
from ...core.reference import exact_page
from ...core.web import settings_digest
from .web_usage import WebUsage

WEB_SCHEMA = '''
CREATE TABLE web_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 provider TEXT NOT NULL, revision INTEGER NOT NULL);
INSERT INTO web_settings VALUES(1,0,'tavily',0);
CREATE TABLE web_setting_operations(mutation_key TEXT PRIMARY KEY,intent_sha256 TEXT NOT NULL,receipt TEXT NOT NULL);
CREATE TABLE web_snapshots(id TEXT PRIMARY KEY,turn_id TEXT NOT NULL REFERENCES turns(id),provider TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('search','page')),request TEXT NOT NULL,metadata TEXT NOT NULL,
 content TEXT NOT NULL,sha256 TEXT NOT NULL,binding TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX web_snapshot_turn ON web_snapshots(turn_id,created_at,id);
'''
MAX_SOURCE_BYTES = 2_000_000


def encode(v): return json.dumps(v,ensure_ascii=False,separators=(',',':'))
def digest(v): return hashlib.sha256(encode(v).encode()).hexdigest()


class WebStore:
    def __init__(self, store):
        self.store,self.db = store,store.db
        self.usage = WebUsage(store.db)

    def settings(self):
        r=self.db.execute('SELECT * FROM web_settings WHERE singleton=1').fetchone()
        return dict(enabled=bool(r['enabled']),provider=r['provider'],revision=r['revision'])

    def settings_operation(self,args):
        if args.get('action') not in ('read','set','lookup'):
            raise Failure('web_settings','Unknown web setting operation.')
        if args['action']=='read': return self.settings()
        if args['action']=='set' and (type(args.get('enabled')) is not bool or
                type(args.get('expected_revision')) is not int or args['expected_revision']<0 or
                not isinstance(args.get('provider'),str) or not args['provider'] or len(args['provider'])>80):
            raise Failure('web_settings','Invalid web setting choice.')
        row=self.db.execute('SELECT * FROM web_setting_operations WHERE mutation_key=?',(args['mutation_key'],)).fetchone()
        expected=args['intent_sha256'] if args['action']=='lookup' else settings_digest(args)
        if row:
            if row['intent_sha256']!=expected: raise Failure('mutation_conflict','That web setting key belongs to another choice.')
            return json.loads(row['receipt'])
        if args['action']=='lookup': return dict(status='absent')
        if self.store.active_turn_id() is not None:
            raise Failure('busy','Stop or finish the current reply before changing web access.')
        with self.db:
            current=self.settings()
            if current['revision']!=args['expected_revision']:
                raise Failure('web_settings_conflict','Web settings changed; refresh before choosing again.')
            changed=current['enabled']!=args['enabled'] or current['provider']!=args['provider']
            revision=current['revision']+int(changed)
            self.db.execute('UPDATE web_settings SET enabled=?,provider=?,revision=? WHERE singleton=1',
                            (int(args['enabled']),args['provider'],revision))
            result=dict(status='committed' if changed else 'unchanged',enabled=args['enabled'],provider=args['provider'],
                        revision=revision,mutation_key=args['mutation_key'],intent_sha256=expected)
            self.db.execute('INSERT INTO web_setting_operations VALUES(?,?,?)',(args['mutation_key'],expected,encode(result)))
            return result

    def guard(self,conversation_id,revision):
        s=self.settings()
        if not s['enabled'] or s['revision']!=revision:
            raise Failure('web_forbidden','Web access changed; no further requests or source delivery are permitted in this turn.')
        if not self.store.conversation(conversation_id).history_enabled:
            raise Failure('web_forbidden','Web tools require the conversation execution policy.')

    def save(self,turn_id,provider,kind,request,metadata,content):
        if kind not in ('search','page') or not isinstance(content,str): raise Failure('web_source','Invalid web source.')
        if len(encode([request,metadata,content]).encode())>MAX_SOURCE_BYTES:
            raise Failure('web_source_size','Fetched source exceeds the supported snapshot size.')
        row=self.db.execute('SELECT status FROM recall_turns WHERE id=?',(turn_id,)).fetchone()
        if row is None or row['status']!='running': raise Failure('web_source','The source no longer belongs to an active eligible turn.')
        if self.db.execute('SELECT count(*) FROM web_snapshots WHERE turn_id=?',(turn_id,)).fetchone()[0]>=max(32,self.store.execution.for_turn(turn_id).operations):
            raise Failure('web_source_limit','This turn reached its fetched-source allowance.')
        source_id=uuid.uuid4().hex; created=datetime.now(timezone.utc).isoformat()
        binding=digest([source_id,turn_id,provider,kind,request,metadata,content,created])
        with self.db:
            self.db.execute('INSERT INTO web_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)',
                (source_id,turn_id,provider,kind,encode(request),encode(metadata),content,hashlib.sha256(content.encode()).hexdigest(),binding,created))
        return source_id

    def load(self,source_id,conversation_id,*,manual=False):
        table='turns' if manual else 'recall_turns'
        size=self.db.execute(f"""SELECT length(CAST(s.request AS BLOB))+length(CAST(s.metadata AS BLOB))+length(CAST(s.content AS BLOB))
            FROM web_snapshots s JOIN {table} t ON t.id=s.turn_id WHERE s.id=? AND t.conversation_id=?""",(source_id,conversation_id)).fetchone()
        if size and size[0]>MAX_SOURCE_BYTES: raise Failure('web_source_integrity','The saved source exceeds its supported size.')
        row=self.db.execute(f'''SELECT s.* FROM web_snapshots s JOIN {table} t ON t.id=s.turn_id
            WHERE s.id=? AND t.conversation_id=?''',(source_id,conversation_id)).fetchone()
        if row is None: raise Failure('web_source_unavailable','This saved web source is not available in this conversation.')
        r=dict(row)
        try:
            r['request']=json.loads(r['request']);r['metadata']=json.loads(r['metadata'])
            if (len(encode(r).encode())>MAX_SOURCE_BYTES+4096 or
                r['binding']!=digest([r[k] for k in ('id','turn_id','provider','kind','request','metadata','content','created_at')])
                or r['sha256']!=hashlib.sha256(r['content'].encode()).hexdigest()): raise ValueError
        except (ValueError,TypeError): raise Failure('web_source_integrity','The saved web source could not be verified.') from None
        return r

    def sources(self,turn_id):
        self.store.turn_metadata(turn_id)
        limit=max(32,self.store.execution.for_turn(turn_id).operations)
        rows=self.db.execute('SELECT s.id,t.conversation_id FROM web_snapshots s JOIN turns t ON t.id=s.turn_id WHERE s.turn_id=? ORDER BY s.created_at,s.id LIMIT ?',(turn_id,limit+1)).fetchall()
        if len(rows)>limit: raise Failure('web_source_integrity','Too many saved sources for this exchange.')
        result=[]
        for row in rows:
            r=self.load(row['id'],row['conversation_id'],manual=True); meta=r['metadata']
            result.append(dict(source_id=r['id'],provider=r['provider'],kind=r['kind'],
                title=str(meta.get('title','Search results'))[:300],url=str(meta.get('url',''))[:2048],
                status=str(meta.get('status','unknown'))[:40],fetched_at=str(meta.get('fetched_at',r['created_at']))[:80]))
        return result

    def inspect(self,source_id,offset,maximum):
        row=self.db.execute('SELECT t.conversation_id FROM web_snapshots s JOIN turns t ON t.id=s.turn_id WHERE s.id=?',(source_id,)).fetchone()
        if row is None: raise Failure('web_source_unavailable','That saved web source is unavailable.')
        r=self.load(source_id,row[0],manual=True)
        # All metadata remains readable, but stays untrusted plain text in UI.
        body=encode(dict(provider=r['provider'],request=r['request'],metadata=r['metadata']))+'\n\n'+r['content']
        page=exact_page(source_id,body,offset,maximum).envelope()
        return dict(source_id=source_id,content=page['content'],offset=page['offset'],next_offset=page['next_offset'],total_bytes=page['total_bytes'])

    def history_actions(self,turn_id):
        return [dict(source_id=r['id'],kind=r['kind']) for r in self.db.execute('SELECT id,kind FROM web_snapshots WHERE turn_id=? ORDER BY created_at,id',(turn_id,))]
