"""One bounded foreground display buffer; durable bodies retain final authority."""
import hashlib
import uuid

from ..core.records import Failure, Reply


class ForegroundStream:
    def __init__(self):
        self.turn_id = None
        self.conversation_id = None
        self.epoch = None
        self.revision = 0
        self.call = 0
        self.phase = 'preparing'
        self.cap = 0
        self.body = bytearray()
        self.closed = False
        self.narration = False
        self.partial_valid = False

    def start(self, turn_id, conversation_id):
        # Native admission permits one foreground owner. Retain only this turn,
        # never a second conversation archive or an unbounded event history.
        if self.turn_id == turn_id and not self.closed:
            return
        self.turn_id, self.conversation_id = turn_id, conversation_id
        self.closed = False
        self.call = 0
        self._reset('preparing')

    def _reset(self, phase, *, retain=False):
        self.epoch = uuid.uuid4().hex
        if not retain:
            self.content_epoch = self.epoch
            self.body.clear()
            self.narration = False
        self.partial_valid = False
        self.revision += 1
        self.phase = phase

    def begin(self, turn_id, call, request, *, visible=True):
        if self.turn_id != turn_id or self.closed:
            raise Failure('stream_owner', 'Progress does not belong to the active turn.')
        self.cap, self.call = request.response_bytes, call
        # A validated tool-call preamble remains readable during tool IO and
        # the next thinking/private-summary gap. Only new visible text replaces
        # it. This stays one bounded buffer, never an accumulated reasoning log.
        self._reset('preparing', retain=self.narration)
        epoch = self.epoch

        def publish(event):
            # A late callback from an old attempt can neither append nor retract
            # the next attempt. Tool dispatch still requires the complete Reply.
            if self.closed or self.turn_id != turn_id or self.epoch != epoch or not visible:
                return
            if event.kind == 'text':
                raw = event.text.encode('utf-8')
                if raw and self.narration:
                    self.body.clear()
                    self.content_epoch = uuid.uuid4().hex
                    self.narration = False
                if len(self.body) + len(raw) > self.cap:
                    raise Failure('response_size', 'Provisional answer exceeded the inherited response bound.')
                self.body.extend(raw)
                if raw:
                    self.partial_valid = True
                    self.phase = 'writing'
                    self.revision += 1
            elif event.kind == 'reset':
                # Keep this callback's attempt epoch; issue a new revision and
                # a separate content epoch so readers cannot append to old text.
                self.body.clear()
                self.narration = False
                self.partial_valid = False
                self.revision += 1
                self.content_epoch = uuid.uuid4().hex
            elif event.kind == 'phase' and event.text in ('thinking', 'tools', 'finishing'):
                self.phase = event.text
                if event.text == 'tools':
                    self.partial_valid = False
                self.revision += 1
            else:
                raise Failure('invalid_response', 'Unknown provider progress event.')
        return publish

    def accept(self, turn_id, reply, *, visible=True):
        if self.turn_id != turn_id or self.closed or not visible:
            return
        raw = reply.text.encode('utf-8')
        if len(raw) > self.cap:
            raise Failure('response_size', 'Answer exceeded the inherited response bound.')
        if reply.tool_calls and not raw and self.narration:
            # A silent intervening tool call does not erase the last visible
            # explanation, or turn it into a saved partial/final answer.
            self.phase = 'tools'
            self.revision += 1
            return
        if raw != self.body:
            # Fallback complete-only models and validated final corrections share
            # the same exact text contract, with explicit replacement identity.
            self.content_epoch = uuid.uuid4().hex
            self.body = bytearray(raw)
            self.revision += 1
        self.phase = 'tools' if reply.tool_calls else 'finishing'
        self.narration = bool(reply.tool_calls and raw)
        self.partial_valid = bool(raw and not reply.tool_calls)
        self.revision += 1

    def activity(self, turn_id, phase):
        if self.turn_id == turn_id and not self.closed and phase in ('searching','reading','connecting'):
            self.phase = phase
            self.revision += 1

    def partial(self, turn_id):
        if self.turn_id == turn_id and self.body and self.partial_valid and not self.narration:
            return Reply(self.body.decode('utf-8'), 'interrupted')
        return None

    def reject(self, turn_id):
        if self.turn_id == turn_id:
            self.body.clear()
            self.narration = False
            self.partial_valid = False
            self.content_epoch = uuid.uuid4().hex
            self.revision += 1

    def terminal(self, turn_id, status):
        if self.turn_id == turn_id:
            self.closed = True
            self.phase = {'completed': 'complete', 'cancelled': 'stopped',
                          'interrupted': 'interrupted'}.get(status, 'failed')
            self.revision += 1

    def page(self, store, row, epoch, offset, maximum):
        body_id = row.get('answer_id') or row.get('partial_id')
        final = None
        if body_id:
            final = store.body_page(row['conversation_id'], row['id'], body_id, 0, 4)
        owned = self.turn_id == row['id']
        if owned and final and hashlib.sha256(self.body).hexdigest() != final['sha256']:
            owned = False
        if owned and (row['status'] == 'running' or final):
            current = getattr(self, 'content_epoch', self.epoch)
            reset = epoch != current
            offset = 0 if reset else offset
            if not 0 <= offset <= len(self.body):
                raise Failure('invalid_offset', 'Stream position is outside the current revision.')
            try:
                self.body[:offset].decode('utf-8')
                content = self.body[offset:offset + maximum].decode('utf-8', errors='ignore')
            except UnicodeError:
                raise Failure('invalid_offset', 'Stream position splits a Unicode character.') from None
            end = offset + len(content.encode('utf-8'))
            return {'turn_id': row['id'], 'conversation_id': row['conversation_id'],
                    'epoch': current, 'revision': self.revision, 'reset': reset,
                    'mode': 'live', 'phase': self.phase, 'status': row['status'],
                    'offset': offset, 'end': end, 'total_bytes': len(self.body),
                    'content': content, 'caught_up': end == len(self.body),
                    'body_id': body_id, 'sha256': final['sha256'] if final else None}
        if final:
            reset = epoch != body_id
            page = store.body_page(row['conversation_id'], row['id'], body_id, 0 if reset else offset, maximum)
            return {'turn_id': row['id'], 'conversation_id': row['conversation_id'],
                    'epoch': body_id, 'revision': 0, 'reset': reset, 'mode': 'stored',
                    'phase': 'complete' if row['status'] == 'completed' else 'stopped' if row['status'] == 'cancelled' else 'failed',
                    'status': row['status'], 'offset': page['offset'], 'end': page['end'],
                    'total_bytes': page['total_bytes'], 'content': page['content'],
                    'caught_up': page['complete'], 'body_id': body_id, 'sha256': page['sha256']}
        return {'turn_id': row['id'], 'conversation_id': row['conversation_id'],
                'epoch': row['id'], 'revision': 0, 'reset': epoch != row['id'],
                'mode': 'none', 'phase': 'preparing' if row['status'] == 'running' else 'failed',
                'status': row['status'], 'offset': 0, 'end': 0, 'total_bytes': 0,
                'content': '', 'caught_up': True, 'body_id': None, 'sha256': None}
