"""Explicit native notebook reads through the existing exact memory owner."""
import time

from ...core.budget import Ledger
from ...core.memory import MemorySnapshot
from ...core.notes import identity
from ...core.records import Failure


def view_token(memory, conversation, bank):
    snapshot = MemorySnapshot(conversation, bank['id'], bank['epoch'], bank['grant_epoch'])
    return memory._token(snapshot, bank['handle_key'], ['notebook_view'])


class NotebookStore:
    def __init__(self, store):
        self.store, self.memory = store, store.memory

    def _scope(self, conversation):
        if not identity(conversation):
            raise Failure('notebook_identity', 'An exact conversation identity is required.')
        saved = self.store.conversation(conversation)
        ledger = Ledger(saved.profile, time.monotonic(), with_history=True, with_memory=True,
                        history_policy=saved.history_policy)
        snapshot = self.memory.snapshot(conversation, saved.memory_bank_id, ledger)
        return snapshot, ledger

    def page(self, conversation, cursor=None, limit=20, collection='current'):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Failure('notebook_page', 'Notebook page size is outside its bound.')
        if collection not in ('current','historical'):
            raise Failure('notebook_page', 'Choose the current or historical notebook.')
        snapshot, ledger = self._scope(conversation)
        with self.store.notes._transaction(ledger):
            bank = self.memory._check(snapshot, ledger)
            before = 2**63 - 1
            if cursor is not None:
                value = self.memory._decode(snapshot, bank['handle_key'], cursor)
                if (not isinstance(value, list) or len(value) != 2 or value[0] != 'notebook:'+collection
                        or type(value[1]) is not int or not 0 < value[1] <= before):
                    raise Failure('notebook_cursor', 'Notebook page identity changed; refresh the list.')
                before = value[1]
            membership = "(n.bank_id=? OR v.author='user' AND v.origin='manual_memory')" if collection=='current' else 'h.bank_id=?'
            rows = self.memory.query(ledger, '''SELECT r.id,v.id AS revision_id,v.sequence,v.created_at
                FROM memory_records r LEFT JOIN note_records n ON r.id=n.record_id
                LEFT JOIN historical_notes h ON r.id=h.record_id
                JOIN memory_revisions v ON v.id=r.head_revision AND v.record_id=r.id
                WHERE '''+membership+''' AND r.bank_id=? AND r.validity='current'
                AND r.readable=1 AND v.readable=1 AND v.sequence<?
                ORDER BY v.sequence DESC LIMIT ?''', (snapshot.bank_id, snapshot.bank_id, before, limit + 1)).fetchall()
            items = []
            for item in rows[:limit]:
                row = self.memory._revision(snapshot, item['id'], item['revision_id'], ledger, manual=True)
                meta = self.memory._meta(snapshot, row['payload_id'], 'memory', ledger)
                preview = self.memory._page(meta, 0, 128, ledger)
                if preview['reason']:
                    raise Failure('notebook_unavailable', 'The exact note preview could not be supplied.')
                items.append(dict(note_id=row['record_id'], revision_id=row['id'],
                    created_at=row['created_at'], sha256=meta['sha256'], total_bytes=meta['bytes'],
                    preview=preview['content']))
                if row['author'] == 'user': items[-1]['author'] = 'user'
                details=self.store.notes.metadata_summary(snapshot,row['id'],ledger) if row['owned_note'] is not None else None
                if details: items[-1]['note']=details
                if row['historical_note'] is not None:
                    items[-1]['historical_note']=self.store.historical_notes.summary(snapshot,row['id'],ledger)
            return dict(items=items, view_token=self.memory._token(snapshot, bank['handle_key'], ['notebook_view']),
                next_cursor=self.memory._token(snapshot, bank['handle_key'], ['notebook:'+collection, rows[limit-1]['sequence']])
                    if len(rows) > limit else None)

    def read(self, conversation, note_id, revision_id, token, offset, maximum):
        if (not identity(note_id) or not identity(revision_id) or type(offset) is not int
                or not 0 <= offset <= 9007199254740991 or type(maximum) is not int or not 1 <= maximum <= 32768):
            raise Failure('notebook_page', 'Exact note identities and a bounded UTF-8 page are required.')
        snapshot, ledger = self._scope(conversation)
        with self.store.notes._transaction(ledger):
            bank = self.memory._check(snapshot, ledger)
            if self.memory._decode(snapshot, bank['handle_key'], token) != ['notebook_view']:
                raise Failure('notebook_cursor', 'Notebook view changed; refresh before reading.')
            row = self.memory._revision(snapshot, note_id, revision_id, ledger, manual=True)
            if (row['owned_note'] is None and row['historical_note'] is None and (row['author'],row['origin']) != ('user','manual_memory')) or row['validity'] != 'current':
                raise Failure('notebook_unavailable', 'This is not an available authored note.')
            meta = self.memory._meta(snapshot, row['payload_id'], 'memory', ledger)
            page = self.memory._page(meta, offset, maximum, ledger)
            if page['reason']:
                raise Failure('notebook_page', 'The requested exact UTF-8 page could not be supplied: ' + page['reason'])
            result=dict(note_id=note_id, revision_id=revision_id, sha256=meta['sha256'],
                        total_bytes=meta['bytes'], offset=offset, end=page['end'], content=page['content'],
                        complete=page['end'] == meta['bytes'])
            if row['author'] == 'user': result['author'] = 'user'
            if row['historical_note'] is not None:
                result['historical_note']=self.store.historical_notes.summary(snapshot,row['id'],ledger)
            details=self.store.notes.metadata(snapshot,revision_id,ledger) if row['owned_note'] is not None else None
            if details:
                result['note']=self.store.notes.metadata_summary(snapshot,revision_id,ledger)
                for field in ('attribution','correction'):
                    if field in details: result[field]=details[field]
            return result
