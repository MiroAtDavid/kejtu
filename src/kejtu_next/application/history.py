"""Whole-history admission and sequential native operations under one ledger."""
import json
import re
from dataclasses import replace

from ..core.budget import message_cost
from ..core.history import POLICY
from ..core.records import Failure, Message
from ..ports.store import Store
from .context import summary_fits
from .operations import OperationLoop


class HistoryTurn(OperationLoop):
    history_tools = True

    @property
    def origin_id(self):
        return self.snapshot.turn_id

    @property
    def reference_id(self):
        return self.conversation.reference_id

    def argument_failure(self, kind, call_number, position, call, reason):
        recorder = {'memory': self.store.memory_argument_failure,
                    'history': self.store.history_argument_failure,
                    'note': self.store.note_argument_failure}[kind]
        recorder(self.origin_id, call_number, position, call, reason)

    def start_operation(self, ordinal, call, **kwargs):
        self.store.start_operation(self.snapshot, ordinal, call, **kwargs)

    def prepare_operation(self, ordinal, result):
        self.store.prepare_operation(self.snapshot, ordinal, result)

    def submit_operation(self, ordinal):
        self.store.submit_operation(self.snapshot, ordinal)

    def source_operation(self, name, args, ledger, allowance, *, budget=None):
        return self.store.history_operation(self.snapshot, name, args, ledger, allowance, budget=budget)

    def __init__(self, store: Store, conversation, turn_id, ledger, *, memory=None, notes=None, working=None, tasks=None, research=None, reminders=None, people=None, web=None, mcp=None, skills=None, workspace=None, progress=None, clock_context=None):
        self.store = store
        self.conversation = conversation
        self.ledger = ledger
        self.snapshot = store.history_snapshot(turn_id, ledger)
        self.call_ids = set()
        self.memory = memory
        self.notes = notes
        self.tasks = tasks
        self.research = research
        self.reminders = reminders
        self.people = people
        self.web, self.progress = web, progress
        self.mcp = mcp
        self.skills = skills
        self.workspace = workspace
        self.context_ranges = []
        self.initial_length = 0
        self.checkpoint = None
        self.working = working
        self.clock_context = clock_context

    def _adaptive_admit(self, prefix, latest, include_turns):
        ledger, snapshot = self.ledger, self.snapshot
        protected = set(include_turns)
        selected, loaded = {}, {}
        if snapshot.total:
            previous = self._unit(ordinal=snapshot.total)
            protected.add(previous.turn_id)
            selected[previous.turn_id] = previous
        for ident in protected:
            if ident not in selected:
                selected[ident] = self._unit(turn_id=ident)
        def compose(*, prune=False):
            ordered = sorted(selected.values(), key=lambda unit: unit.ordinal)
            suffix = snapshot.total + 1
            ordinals = {u.ordinal for u in ordered}
            while suffix - 1 in ordinals:
                suffix -= 1
            notice = snapshot.notice(admitted=len(ordered),
                suffix_start=suffix if suffix <= snapshot.total else None, pins=len(set(include_turns)))
            messages = prefix + (notice,)
            ranges = []
            for unit in ordered:
                if unit.turn_id not in loaded:
                    loaded[unit.turn_id] = self._load(unit)
                original = loaded[unit.turn_id]
                # Exact identity and verified historical action metadata remain
                # outside summaries, as do explicit pins and the latest exchange.
                if unit.turn_id not in protected:
                    ranges.append((len(messages) + 1, len(messages) + len(original)))
                messages += original
            messages += latest
            if self.working is not None:
                messages, ranges, events = self.working.apply(messages, ranges, prune=prune)
                if prune:
                    for event in events:
                        self.store.context_event(snapshot.turn_id, event)
            self.context_ranges = ranges
            return messages
        record = dict(policy=POLICY, snapshot=snapshot.turn_id,
            execution_policy=ledger.history_policy.value, operation_limit=ledger.max_operations,
            call_limit=ledger.max_calls, earlier_turns=snapshot.total, working_reserve=0,
            schema_estimate=ledger.schema_cost, status='failed')
        try:
            # Bound source materialization independently of any token estimate.
            if sum(self._cost(u) for u in selected.values()) > 4 * ledger.profile.context_limit:
                raise Failure('context_limit', 'Protected history exceeds the bounded request; originals remain readable.')
            messages = compose()
            if ledger.remaining_input(messages) < 0 and not ledger.can_probe(messages):
                raise Failure('context_limit', 'Protected instructions/current input/recent history do not fit.')
            for ordinal in range(snapshot.total, 0, -1):
                unit = self._unit(ordinal=ordinal)
                if unit.turn_id in selected:
                    continue
                if sum(self._cost(u) for u in selected.values()) + self._cost(unit) > 4 * ledger.profile.context_limit:
                    break
                selected[unit.turn_id] = unit
                candidate = compose()
                if ledger.remaining_input(candidate) >= 0 or ledger.can_probe(candidate):
                    messages = candidate
                    continue
                # Keep one whole overflowing optional exchange for a bounded
                # between-turn summary. Never infer token pressure from an
                # unmeasured provider-first estimate alone.
                if ((ledger.accounting.counter is not None or not ledger.accounting.provider_first)
                        and summary_fits(ledger, loaded[unit.turn_id][1:])):
                    messages = candidate
                    break
                del selected[unit.turn_id]
                messages = compose()
                break
            messages = compose(prune=True)
            self.initial_length = len(messages)
            record.update(status='admitted', admitted_ids=[u.turn_id for u in sorted(selected.values(), key=lambda u:u.ordinal)],
                protected_ids=sorted(protected), omitted_count=snapshot.total-len(selected),
                input_estimate=ledger.request_cost(messages), notice_estimate=message_cost(messages[len(prefix)]))
            return messages
        except Failure as error:
            record['reason'] = error.code
            raise
        finally:
            record.update(sql_steps=ledger.sql_steps, payload_bytes=ledger.payload_bytes)
            self.store.save_admission(snapshot, record)

    def compression_ranges(self, messages):
        ranges = list(self.context_ranges)
        # Pending/current batches are never eligible. DeepSeek thinking chains
        # keep all native state; only older completed conversation units may go.
        if not self.conversation.needs_reasoning_history:
            start = self.initial_length
            while start < len(messages):
                head = messages[start]
                if head.role != 'assistant' or not head.tool_calls:
                    break
                end = start + 1 + len(head.tool_calls)
                paired = messages[start+1:end]
                if len(paired) != len(head.tool_calls) or any(m.role != 'tool' or m.tool_call_id != c.id
                        for m, c in zip(paired, head.tool_calls)):
                    break
                # Supplied skill procedures and exact resource pages remain
                # current task inputs. A summary is never a substitute for them.
                if not any(m.tool_name == 'read_skill' for m in paired):
                    ranges.append((start, end))
                start = end
        return ranges

    def _unit(self, **selection):
        unit = self.store.history_unit(self.snapshot, self.ledger, **selection)
        if self.notes or self.tasks or self.reminders or self.people or self.web:
            actions = self.notes.history_actions(unit.turn_id) if self.notes else {}
            if self.tasks:
                actions['tasks_current'] = self.tasks.history_actions(unit.turn_id)
            if self.reminders:
                actions['reminder_outcomes'] = self.reminders.history_actions(unit.turn_id)
            if self.web:
                actions['web_sources'] = self.web.history_actions(unit.turn_id)
            if self.people:
                actions['people_effects'] = self.people.history_actions(unit.turn_id)
            def record(value):
                return Message('system', unit.cue().content + '\nPast actions: ' +
                    json.dumps(value, ensure_ascii=True, separators=(',', ':')))
            counts = {key: len(value) if isinstance(value, list) else value for key, value in actions.items()}
            compact = record(dict(counts, ids='not_loaded')) if counts != actions else None
            unit = replace(unit, actions=record(actions), actions_without_ids=compact)
        return unit

    @staticmethod
    def _cost(unit):
        return (unit.cost + (message_cost(unit.actions) - message_cost(unit.cue()) if unit.actions else 0)
                + sum(message_cost(message) for message in unit.deliveries))

    def _load(self, unit):
        messages = self.store.history_load(self.snapshot, unit, self.ledger)
        # Original bytes and their stored cost stay exact. The current eligible
        # outcome projection is charged with its whole exchange, never squeezed
        # out after admission or copied into durable conversation/source bodies.
        return ((unit.actions,) + messages[1:] if unit.actions else messages) + unit.deliveries

    @staticmethod
    def _compact(unit):
        # Typed counts from the SAME verified outcomes, not a generated summary
        # or a shortened original. Explicitly disclose unloaded identities.
        return replace(unit, actions=unit.actions_without_ids, actions_without_ids=None,
            action_ids_omitted=True) if unit.actions_without_ids else unit

    def recall_allowance(self, prefix, latest, include_turns):
        """Optional recall cannot displace protected original exchanges."""
        protected = {ident: self._unit(turn_id=ident) for ident in set(include_turns)}
        if self.snapshot.total:
            previous = self._unit(ordinal=self.snapshot.total)
            protected[previous.turn_id] = previous
        notice = message_cost(self.snapshot.notice(suffix_start=self.snapshot.total)) + 64
        return (self.ledger.remaining_input(prefix + latest) - self.ledger.working_room
                - notice - sum(self._cost(unit) for unit in protected.values()))

    def admit(self, prefix, latest, include_turns):
        if self.ledger.accounting:
            return self._adaptive_admit(prefix, latest, include_turns)
        ledger, snapshot = self.ledger, self.snapshot
        selected = {}
        suffix_start = None
        record = {'policy': POLICY, 'snapshot': snapshot.turn_id,
                  'execution_policy': ledger.history_policy.value,
                  'operation_limit': ledger.max_operations, 'call_limit': ledger.max_calls,
                  'earlier_turns': snapshot.total, 'working_reserve': ledger.working_room,
                  'schema_estimate': ledger.schema_cost, 'status': 'failed'}
        try:
            # SQLite ordinals/counts are signed 64-bit. Reserve bounded decimal
            # variation, not an archive-sized identity list in the initial notice.
            notice_cost = message_cost(snapshot.notice(suffix_start=snapshot.total)) + 64
            room = ledger.remaining_input(prefix + latest) - notice_cost - ledger.working_room
            if room < 0:
                raise Failure('context_limit', 'Required prompt, current input and working/final room do not fit; no call was sent.')
            protected = set(include_turns)
            if snapshot.total:
                previous = self._unit(ordinal=snapshot.total)
                protected.add(previous.turn_id)
                selected[previous.turn_id] = previous
            for ident in protected:
                if ident not in selected:
                    selected[ident] = self._unit(turn_id=ident)
            room -= sum(self._cost(unit) for unit in selected.values())
            if room < 0:
                for ident, unit in sorted(selected.items(), key=lambda item: item[1].ordinal, reverse=True):
                    compact = self._compact(unit)
                    room += self._cost(unit) - self._cost(compact)
                    selected[ident] = compact
                    if room >= 0:
                        break
            if room < 0:
                raise Failure('context_limit', 'Required whole history and working/final room do not fit; originals remain readable. No call was sent.')
            for ordinal in range(snapshot.total, 0, -1):
                unit = self._unit(ordinal=ordinal)
                if unit.turn_id not in selected:
                    if self._cost(unit) > room:
                        unit = self._compact(unit)
                    if self._cost(unit) > room:
                        break  # Contiguous suffix: never skip large newer units.
                    selected[unit.turn_id] = unit
                    room -= self._cost(unit)
                suffix_start = ordinal
            ordered = sorted(selected.values(), key=lambda unit: unit.ordinal)
            history = tuple(message for unit in ordered for message in self._load(unit))
            notice = snapshot.notice(admitted=len(ordered), suffix_start=suffix_start, pins=len(set(include_turns)))
            if message_cost(notice) > notice_cost:
                raise Failure('context_limit', 'History notice exceeded its reserved framing.')
            messages = prefix + (notice,) + history + latest
            if ledger.remaining_input(messages) < ledger.working_room:
                raise Failure('context_limit', 'Exact admitted history does not leave its working reserve.')
            record.update(status='admitted', admitted_ids=[u.turn_id for u in ordered],
                protected_ids=sorted(protected), omitted_count=snapshot.total - len(ordered),
                recent_suffix_start=suffix_start,
                omitted_region_end=(suffix_start - 1 if suffix_start else snapshot.total),
                input_estimate=ledger.input_allowance - ledger.remaining_input(messages),
                notice_estimate=message_cost(notice))
            if self.notes:
                record['action_identity_omissions'] = [u.turn_id for u in ordered if u.action_ids_omitted]
            return messages
        except Failure as error:
            record['reason'] = error.code
            raise
        finally:
            record.update(sql_steps=ledger.sql_steps, payload_bytes=ledger.payload_bytes)
            self.store.save_admission(snapshot, record)


def validate_selections(include_turns, context_limit):
    if (not isinstance(include_turns, (tuple, list)) or len(include_turns) > context_limit // 64
            or any(not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident) for ident in include_turns)):
        raise Failure('invalid_input', 'Explicit history selections exceed the bounded identity envelope; nothing was saved.')
