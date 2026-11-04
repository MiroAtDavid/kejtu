"""Shared note operations for a turn or private review; no separate writer."""
import json

from ..core.records import Failure
from ..core.notes import operation_key
from ..ports.notes import NotesPort


class NoteSession:
    def __init__(self, port: NotesPort | None, memory, turn_id, ledger, *, job_run_id=None, existing_only=False):
        if port is None or memory is None:
            raise Failure('notes_unavailable', 'The granted note adapter is unavailable; no model call was sent.')
        self.port, self.memory, self.turn_id, self.ledger = port, memory, turn_id, ledger
        if (turn_id is None) == (job_run_id is None):
            raise Failure('note_origin_invalid', 'A note session needs exactly one turn or review origin.')
        self.job_run_id = job_run_id
        self.existing_only = existing_only
        # Only delivery metadata, not another note body/cache or authority owner.
        # Bounded by this turn's existing operation allowance; never survives it.
        self.reads = {}

    def history_actions(self, turn_id):
        return self.port.history_actions(self.memory.snapshot, turn_id, self.ledger)

    def observe(self, args, result):
        """Record only the exact pages admitted into the stable model transcript."""
        if (result.get('record_kind') != 'authored_note' or result.get('kind') != 'memory'
                or result.get('mode') != 'current' or not result.get('revision_current')
                or result.get('validity') != 'current' or result.get('status') not in ('complete', 'page')):
            return
        ident = (result['record_id'], result['revision_id'], result['sha256'], result['total_bytes'])
        ref = args['read_ref']
        previous = self.reads.get(ref)
        if previous is None:
            previous = self.reads[ref] = {'identity': ident, 'ranges': []}
        if previous['identity'] != ident:
            raise Failure('memory_source_changed', 'A read reference changed identity during this turn.')
        previous['ranges'].append((result['offset'], result['end']))

    def target(self, ref):
        selected = self.reads.get(ref)
        if selected is None:
            return None
        end = 0
        for start, stop in sorted(selected['ranges']):
            if start > end:
                return None
            end = max(end, stop)
        return selected['identity'][:2] if end == selected['identity'][3] else None

    def check(self):
        # A refusal continuation must not outlive write/read revocation either.
        # Scope checks authority only; no new snapshot, write or argument filling.
        self._scope()

    def _scope(self):
        if self.job_run_id is not None:
            return self.port.scope_job(self.memory.snapshot, self.job_run_id, self.ledger)
        return self.port.scope(self.memory.snapshot, self.turn_id, self.ledger)

    def _recovery(self, outcome, maximum, budget, *, authored=False):
        if outcome.get('status') != 'rejected' or outcome.get('reason') not in {
                'note_span_conflict', 'note_revision_conflict', 'note_thread_status_invalid'}:
            return outcome
        # Application-owned navigation, not source content or a durable effect.
        # Never rewrite/retry the call or replace a truthful refusal when this
        # optional explanation cannot fit the SAME result allowance.
        if outcome['reason'] == 'note_thread_status_invalid':
            guidance = ('No note changed. thread_status is only for purpose open_thread, including an existing note. '
                        'Keep the intended purpose; for other purposes omit thread_status (not null) and submit the corrected call.')
            if not authored:
                guidance += ' Changed intent needs a new operation_key.'
        else:
            guidance = ('Read the complete current note with search_memory/read_memory, then save revised text and its read_ref.'
                        if authored else 'Read exact note: search_memory(current) -> read_memory(read_ref). '
                        'Correct bytes/revision; changed intent needs a new operation_key.')
        guided = dict(outcome, recovery=guidance)
        if (len(json.dumps(guided, ensure_ascii=False, separators=(',', ':')).encode()) <= maximum
                and (budget is None or budget.fits(guided))):
            return guided
        return outcome

    def operation(self, call, args, maximum, budget):
        key = operation_key(self.turn_id, args, job_run_id=self.job_run_id)
        try:
            if call.name == 'note_outcome':
                outcome = self.port.outcome(self.memory.snapshot, call, self.ledger, maximum, budget=budget)
                return self._recovery(outcome, maximum, budget, authored=key.startswith('save:'))
            scope = self._scope()
            if self.existing_only:
                if 'flags' in args:
                    return dict(status='rejected', operation_key=key, reason='maintenance_flags_preserved',
                        recovery='Maintenance preserves rejection and coverage flags. Report any proposed change.')
                if 'operation' in args or not args.get('read_ref'):
                    return dict(status='rejected', operation_key=key, reason='maintenance_existing_note_required',
                        recovery='Update an existing completely read note with its read_ref. Report proposed merges or removal.')
                if self.target(args['read_ref']) is None:
                    return dict(status='rejected', operation_key=key, reason='note_read_required',
                        recovery='Read the complete current note before changing its text or metadata.')
            if 'operation' not in args:
                target = self.target(args['read_ref']) if 'read_ref' in args else None
                if 'text' in args and 'read_ref' in args and target is None:
                    return {'status': 'rejected', 'operation_key': key, 'reason': 'note_read_required',
                        'recovery': 'Read the complete current authored note with read_memory first; follow next_offset. '
                                    'Then write_note(text, read_ref). Previews and historical/source reads cannot replace a note.'}
                result = self.port.save(scope, call, target, self.ledger, maximum, budget=budget)
            else:
                result = self.port.write(scope, call, self.ledger, maximum, budget=budget)
            self.memory.advance(result)
            return self._recovery(result.outcome, maximum, budget, authored='operation' not in args)
        except Failure as error:
            if error.code in {'history_work_limit', 'timeout', 'memory_source_changed'}:
                raise
            # Unavailable/corrupt storage cannot establish a failed save. A known
            # commit followed by interruption remains visible via durable lookup.
            uncertain = error.code in {'note_outcome_unknown', 'note_store_unavailable', 'memory_integrity'}
            return {'status': 'unknown' if uncertain else 'rejected',
                    'operation_key': key, 'reason': error.code}
