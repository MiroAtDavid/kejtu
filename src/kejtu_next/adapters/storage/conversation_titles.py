"""Derived journal names. Original turns and their ordering remain authoritative."""
import hashlib
import json
import re
from .conversation_import import imported

TITLE_SCHEMA = '''
CREATE TABLE conversation_titles(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 source_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','waiting','ready','failed')),
 title TEXT, CHECK((status='ready' AND title IS NOT NULL) OR (status!='ready' AND title IS NULL)));
'''


def fallback(text):
    line = text.strip().splitlines()[0] if text.strip() else ''
    line = re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', line)).strip()
    return (line[:65] + '…' if len(line) > 65 else line) or 'A new conversation'


class ConversationTitles:
    def __init__(self, store):
        self.store = store

    def source(self, cid):
        origin = imported(self.store, cid)
        if origin:
            return dict(label=origin["title"] or "Imported conversation", material=None)
        # Read a bounded, integrity-checked excerpt, never drain long originals
        # or mistake the newest transcript page for the first question.
        first = self.store.db.execute('SELECT id,user_id FROM turns WHERE conversation_id=? ORDER BY sequence LIMIT 1', (cid,)).fetchone()
        label = 'A new conversation'
        if first:
            label = fallback(self.store.body_page(cid, first['id'], first['user_id'], 0, 1024)['content'])
        row = self.store.db.execute("SELECT id,user_id,answer_id FROM recall_turns WHERE conversation_id=? AND status='completed' AND answer_id IS NOT NULL ORDER BY sequence LIMIT 1", (cid,)).fetchone()
        if not row:
            return dict(label=label, material=None)
        question = self.store.body_page(cid, row['id'], row['user_id'], 0, 1000)
        answer = self.store.body_page(cid, row['id'], row['answer_id'], 0, 2000)
        binding = [row['id'], question['body_id'], question['sha256'], answer['body_id'], answer['sha256']]
        return dict(label=label, material=dict(hash=hashlib.sha256(json.dumps(binding).encode()).hexdigest(),
                    question_excerpt=question['content'], answer_excerpt=answer['content']))

    def read(self, cid):
        origin = imported(self.store, cid)
        if origin:
            return dict(conversation_id=cid, title=origin["title"] or "Imported conversation", status="ready")
        source = self.source(cid)
        material = source['material']
        saved = self.store.db.execute('SELECT * FROM conversation_titles WHERE conversation_id=?', (cid,)).fetchone()
        if saved and material and saved['source_hash'] == material['hash']:
            return dict(conversation_id=cid, title=saved['title'] or source['label'], status=saved['status'])
        return dict(conversation_id=cid, title=source['label'], status='waiting' if material else 'unavailable')

    def save(self, cid, material, status, title=None):
        # Even a late result must still name precisely the exchange it used.
        with self.store.db:
            current = self.source(cid)['material']
            if not current or current['hash'] != material['hash']:
                return False
            self.store.db.execute('INSERT INTO conversation_titles VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET source_hash=excluded.source_hash,status=excluded.status,title=excluded.title',
                                  (cid, material['hash'], status, title))
        return True
