"""App-owned background reservations, bounded decisions and joined cancellation.

No service installation, synthetic user turn or claim that a draft was delivered.
An absent/stale activity observation is unknown, never permission to infer idle.
"""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import re
import time

from ..core.background import accepted, decision, messages
from ..core.budget import Ledger
from ..core.context import RequestAccounting, request_schema
from ..core.execution import ExecutionLimits
from ..core.jobs import REVIEW_KINDS, QUIET_KINDS, JobDefinition
from ..core.records import Failure, Message, message_record
from ..ports.background import BackgroundStore
from ..ports.model import Model
from .context import model_context_capability
from .review import IdleReview, observed as review_observed
from .care import NotebookCare
from .research import ResearchRun, GUIDANCE as RESEARCH_GUIDANCE
from .research_pause import ResearchPause
from .web import WebSession


class Activity:
    """One native-session observation lease, to be fed by its owned UI bridge."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_input = clock()
        self.seen = None
        self.state = None
        self.epoch = 0

    def touch(self):
        self.last_input = self.clock()
        self.epoch += 1

    def reset(self):
        """A new/disconnected observer has no inherited idle authority."""
        self.seen = self.state = None
        self.touch()

    def observe(self, *, conversation_id, visible, focused, draft, dialog, interacting=False):
        if (conversation_id is not None and (not isinstance(conversation_id, str) or re.fullmatch('[0-9a-f]{32}', conversation_id) is None)
                or any(type(value) is not bool for value in (visible, focused, draft, dialog, interacting))):
            raise Failure('job_activity', 'Use an exact native-session activity observation.')
        now = self.clock()
        state = (conversation_id, visible, focused, draft, dialog)
        if self.state != state or interacting or draft or dialog:
            self.touch()
        self.state, self.seen = state, now

    def idle_for(self, definition):
        now = self.clock()
        if self.seen is None or not 0 <= now - self.seen <= 15 or self.state[3] or self.state[4]:
            return None
        if definition.kind not in QUIET_KINDS and (not self.state[1] or self.state[0] != definition.conversation_id):
            return None
        return max(0, min(31536000, int(now - self.last_input)))


class BackgroundRunner:
    def __init__(self, jobs: BackgroundStore, model: Model, *, provider,
                 reminders=None, memory=None, notes=None, research=None, web_backends=None, mcp_host=None, before_run=None, clock=time.monotonic, wall=None, blocked=None):
        self.jobs, self.model, self.provider = jobs, model, provider
        self.reminders, self.before_run = reminders, before_run
        self.memory, self.notes = memory, notes
        self.research, self.web_backends = research, web_backends
        self.mcp_host = mcp_host
        self.blocked = blocked or (lambda: False)
        self.clock = clock
        self.wall = wall or (lambda: datetime.now(timezone.utc).isoformat())
        self.activity = Activity(clock)
        self.loop = self.active = None
        self.run_id = None
        self.preparing = False
        self.closed = False
        self.error_code = None
        self.cancel_reasons = {}
        self.interrupt_lock = asyncio.Lock()
        self.research_pause = None

    def start(self):
        if self.closed or self.loop is not None:
            return
        async def run():
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Failure as error:
                    self.error_code = error.code
                except Exception:
                    self.error_code = 'job_scheduler_failed'
                await asyncio.sleep(5)
        self.loop = asyncio.create_task(run(), name='app-background-scheduler')

    def owns_provider(self, provider):
        # A settings default is not authority to rewrite a scheduled job's
        # explicit profile. Multi-provider sessions route that profile unchanged.
        return provider in getattr(self.model, 'providers', (self.provider,))

    async def observe(self, **values):
        previous = self.activity.epoch
        self.activity.observe(**values)
        if self.activity.epoch != previous:
            # A web-only assignment can continue while the user browses or
            # drafts. Actual foreground admission still joins cancellation.
            if self.active is not None and self.jobs.definition(self.jobs.run(self.run_id)['job_id']).kind == 'research':
                self.jobs.cancel_unseen('job_activity', self.wall())
            else:
                await self.interrupt('job_activity')

    async def foreground(self):
        self.activity.touch()
        await self.interrupt('job_foreground')

    async def interrupt(self, reason='job_cancelled', *, cancel_deliveries=True):
        # Concurrent native controls must join the same stop before a caller
        # can remove the settled run. A later join must not read a deleted run.
        async with self.interrupt_lock:
            await self._interrupt(reason, cancel_deliveries=cancel_deliveries)

    async def _interrupt(self, reason, *, cancel_deliveries):
        # Stopping generation alone cannot revoke an already-prepared message.
        if cancel_deliveries:
            self.jobs.cancel_unseen(reason, self.wall())
        task, ident = self.active, self.run_id
        if task is None:
            return
        if self.research_pause is not None and reason in ('job_foreground', 'job_activity') and not task.done():
            await self.research_pause.request(task)
            return
        self.cancel_reasons[ident] = reason
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.cancel_reasons.pop(ident, None)
        # Cancellation before coroutine entry cannot reach _execute's handler.
        if self.jobs.run(ident)['status'] in ('running', 'cancel_requested'):
            self.jobs.finish(ident, status='cancelled', error_code=reason, now=self.wall())
        if self.active is task:
            self.active = self.run_id = None
            self.research_pause = None

    def _eligible(self, definition, epoch=None):
        if definition.kind == 'research' and epoch is not None:
            # Keep the native-session lease, live grants and foreground checks;
            # idle is an admission condition, not cancellation on every click.
            if self.closed or self.activity.seen is None or not 0 <= self.clock() - self.activity.seen <= 15:
                raise Failure('job_activity', 'The native-session observation expired.')
            return 0
        idle = self.activity.idle_for(definition)
        if self.closed or idle is None or idle < definition.idle_seconds or epoch is not None and epoch != self.activity.epoch:
            raise Failure('job_activity', 'Fresh idle eligibility changed; background work must stop.')
        return idle

    async def tick(self):
        if self.blocked():
            return
        if self.closed:
            return
        self.jobs.sweep_deliveries(self._eligible, self.wall())
        now = self.wall()
        # Quiet reminder availability is independent of a long model job.
        # Polling coalesces due occurrences; it neither presents nor consumes them.
        if self.reminders is not None:
            self.reminders.poll(now)
        if self.active is not None:
            if self.active.done():
                try:
                    self.active.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self.error_code = 'job_run_failed'
                self.active = self.run_id = None
                self.research_pause = None
            else:
                try:
                    definition = self.jobs.check_run(self.run_id)
                    if self.research_pause is not None and self.research_pause.requested:
                        idle = self.activity.idle_for(definition)
                        if idle is not None and idle >= 60:
                            self.research_pause.resume()
                        return
                    self._eligible(definition, self.run_epoch)
                except Failure as error:
                    if error.code == 'job_foreground' and self.research_pause is not None and self.research_pause.requested:
                        return
                    await self.interrupt(error.code)
                return
        self.jobs.poll(now)
        cursor = 0
        # At most 64 active job definitions; bounded pages avoid head-of-line
        # blocking by a job waiting for another chat, cooldown or source grant.
        for _ in range(4):
            page = self.jobs.pending(after=cursor, limit=20)
            for row in page['items']:
                definition = self.jobs.definition(row['job_id'], row['revision'])
                try:
                    idle = self._eligible(definition)
                except Failure:
                    continue
                self.preparing = True
                try:
                    if self.before_run is not None:
                        await self.before_run()
                    # Any foreground activity while joining index work wins.
                    try:
                        idle = self._eligible(definition)
                        reservation = self.jobs.claim(row['id'], now=self.wall(), idle_for=idle)
                    except Failure as error:
                        if error.code in ('job_forbidden', 'job_foreground', 'job_activity', 'research_forbidden', 'research_source'):
                            continue
                        raise
                    if reservation is not None:
                        self.error_code = None
                        self.run_id, self.run_epoch = row['id'], self.activity.epoch
                        self.research_pause = (ResearchPause(self.clock,
                            lambda waiting, ident=row['id']: self.jobs.research_waiting(ident, waiting))
                            if definition.kind == 'research' else None)
                        self.active = asyncio.create_task(self._execute(reservation, self.run_epoch), name='background-run-' + self.run_id)
                        return
                finally:
                    self.preparing = False
            if page['next_cursor'] is None:
                return
            cursor = page['next_cursor']

    async def _execute(self, reservation, epoch):
        ident = reservation['id']
        definition = JobDefinition.from_record(reservation['definition'])
        started = self.clock()
        reviewing = definition.kind in REVIEW_KINDS
        researching = definition.kind == 'research'
        # Still inside _execute's owned task: failures below are settled by the
        # same terminal handler, including missing review ports before any IO.
        ledger = Ledger(definition.profile, started, execution_limits=ExecutionLimits(**reservation['execution_limits']) if 'execution_limits' in reservation else ExecutionLimits(
            definition.limits.calls, definition.limits.operations, definition.limits.seconds))
        ledger.accounting = RequestAccounting(definition.profile, request_schema(),
            counter=getattr(self.model, 'count_request_tokens', None))
        snapshot = review = procedure = None
        pause = self.research_pause if researching else None
        def event(kind, value):
            return self.jobs.event(ident, kind, value, now=self.wall())
        def fresh(snapshot=None):
            self._eligible(self.jobs.check_run(ident), epoch)
            if snapshot is not None:
                self.jobs.check_snapshot(ident, snapshot, ledger)
        async def complete(request, phase):
            fresh(snapshot)
            if procedure is not None:procedure.check_operations()
            if review is not None:
                review.memory.check()
            inputs = request.messages
            attempt = dict(ordinal=ledger.calls, phase=phase, profile=asdict(request.profile),
                accounting=ledger.accounting.receipt(inputs), tools_allowed=request.allow_tool_calls,
                full_generation_reserve=request.profile.generation_limit, deadline_seconds=max(0, ledger.deadline - self.clock()))
            # A note tool's exact bodies already have source/revision owners.
            # Do not turn its transient model transcript into undeletable copies.
            if reviewing or researching:
                attempt['transcript'] = dict(messages=len(inputs), exact_request_fingerprint=attempt['accounting']['request_fingerprint'])
            else:
                attempt['messages'] = [message_record(m) for m in inputs]
            event('model_started', attempt)
            try:
                reply = await pause.complete(self.model, request, ledger) if pause else await self.model.complete(request)
            except Failure as error:
                if error.code == 'provider_context_limit':
                    ledger.rejected_context()
                    event('context_rejected', dict(ordinal=ledger.calls))
                raise
            if self.clock() >= ledger.deadline:
                raise Failure('timeout', 'Background provider exceeded its shared deadline.')
            ledger.account(reply)
            event('model_observed', dict(ordinal=ledger.calls, reply=review_observed(reply) if reviewing or researching else asdict(reply)))
            ledger.accounting.observe(inputs, reply)
            if (reply.input_tokens is not None and reply.input_tokens + definition.profile.generation_limit
                    + definition.profile.safety_margin + 64 > definition.profile.context_limit):
                raise Failure('observed_context_limit', 'Observed input cannot preserve the saved full reply reserve.')
            fresh(snapshot)
            if procedure is not None:procedure.check_operations()
            if review is not None:
                review.memory.check()
            return reply
        async def call(snapshot, phase, proposed=None):
            inputs = messages(snapshot, definition.kind, self.wall(), proposed=proposed)
            request = ledger.admit(inputs, self.clock()) if not ledger.calls else ledger.procedure_request(inputs, self.clock())
            return await complete(request, phase)
        try:
            async with asyncio.timeout_at(ledger.deadline) as timeout:
                fresh()
                if not self.owns_provider(definition.profile.provider):
                    raise Failure('job_provider', 'This native session does not own the explicitly selected job provider.')
                if researching:
                    if self.research is None:
                        raise Failure('research_unavailable','Background assignment adapters are unavailable.')
                    store=self.research.store
                    conversation=store.conversation(definition.conversation_id)
                    settings=store.web.settings()
                    manifest=self.mcp_host.manifest() if self.mcp_host else dict(revision=0,servers=[])
                    manifest['servers']=[row for row in manifest['servers'] if not row['requires_web'] or settings['enabled']]
                    ledger = Ledger(definition.profile, started, with_history=True, web_only=True,
                        with_web=settings['enabled'] and self.web_backends is not None, web_provider=settings['provider'],
                        with_memory=bool(conversation.memory_bank_id),hybrid_memory=conversation.hybrid_memory,
                        with_tasks=conversation.tasks_enabled,with_people=conversation.people_mode!='off',
                        with_reminders=conversation.reminders_enabled,with_mcp=bool(manifest['servers']),
                        with_skills=bool(store.skills.status(enabled=True)['items']),execution_limits=ledger.execution_limits)
                    ledger.accounting = RequestAccounting(definition.profile,
                        request_schema(history=True,web_only=True,web=ledger.with_web,web_provider=ledger.web_provider,
                            memory=ledger.with_memory,hybrid=ledger.hybrid_memory,tasks=ledger.with_tasks,
                            people=ledger.with_people,reminders=ledger.with_reminders,mcp=ledger.with_mcp,skills=ledger.with_skills),
                        counter=getattr(self.model,'count_request_tokens',None))
                    async def checkpoint():
                        if pause:
                            await pause.checkpoint(ledger, timeout)
                        fresh()
                    await checkpoint()
                    ledger.accounting.provider_first = await model_context_capability(self.model,definition.profile,ledger.deadline)
                    sources = self.research.web_sources(ident)
                    web = WebSession(sources,self.web_backends,definition.conversation_id,ident,ledger) if ledger.with_web else None
                    procedure=ResearchRun(ident,ledger,web,event=event,fresh=fresh,store=store,conversation=conversation,
                        mcp_host=self.mcp_host,mcp_receipts=self.research.mcp_receipts(),checkpoint=checkpoint,
                        suspended=lambda: bool(pause and pause.suspended))
                    prior = self.research.findings(reservation['job_id'],limit=20)
                    inputs = dict(instructions=definition.research.instructions,checked_at=self.wall(),
                        previous_findings=prior['items'],previous_findings_omitted=prior['next_offset'] is not None,connected_tools=manifest,
                        skills=procedure.skills.catalogue(4096) if procedure.skills else None)
                    def input_messages():
                        from ..core.mcp import GUIDE as MCP_GUIDE
                        from ..core.skills import GUIDE as SKILL_GUIDE
                        guidance=RESEARCH_GUIDANCE+('\n'+MCP_GUIDE if ledger.with_mcp else '')+('\n'+SKILL_GUIDE if ledger.with_skills else '')
                        return (Message('system',guidance),Message('user',json.dumps(inputs,ensure_ascii=False,separators=(',',':'))))
                    while inputs['previous_findings'] and ledger.remaining_input(input_messages()) < 0:
                        inputs['previous_findings'].pop();inputs['previous_findings_omitted']=True
                    event('inputs',inputs)
                    result = await procedure.run(ledger.admit(input_messages(),self.clock()),complete)
                    procedure.check_operations()
                    event('procedure_result',dict(coverage=result['coverage'],findings=len(result['findings'])))
                    self.jobs.finish(ident,status='completed',now=self.wall(),result=json.dumps(result,ensure_ascii=False,separators=(',',':')))
                    return
                if reviewing:
                    if self.memory is None or self.notes is None:
                        raise Failure('job_procedure_unavailable', 'The private review requires the shared memory and note owners.')
                    conversation = self.jobs.review_conversation(ident)
                    ledger = Ledger(definition.profile, started, with_history=True, with_memory=True,
                        with_notes=True, notes_write=definition.permissions.write_notes,
                        hybrid_memory=conversation.hybrid_memory if conversation else True,
                        history_policy=conversation.history_policy if conversation else None,
                        notebook_only=True, execution_limits=ledger.execution_limits)
                    ledger.accounting = RequestAccounting(definition.profile, request_schema(history=True,
                        memory=True, notes=True, notes_write=ledger.notes_write,
                        hybrid=ledger.hybrid_memory, notebook_only=True), counter=getattr(self.model, 'count_request_tokens', None))
                    if definition.kind == 'notebook_care':
                        ledger.accounting = RequestAccounting(definition.profile, request_schema(),
                            counter=getattr(self.model, 'count_request_tokens', None))
                if definition.kind == 'reminder_delivery' and ledger.max_calls < 2:
                    raise Failure('job_call_limit', 'Reminder decision and factual review require two reserved calls.')
                snapshot = self.jobs.source_snapshot(ident, ledger)
                event('inputs', snapshot)
                if not reviewing and snapshot['latest_sequence'] is None:
                    # A conversational delivery needs a genuine history anchor.
                    # Independent quiet reminders need no chat and use their
                    # direct notice API, never a fabricated user exchange.
                    self.jobs.finish(ident, status='deferred', now=self.wall(), error_code='job_no_conversation_anchor')
                    return
                absent = not snapshot['reminders'] if definition.kind == 'reminder_delivery' else not snapshot['conversation']['selected_complete_exchanges']
                if not reviewing and (absent or self.jobs.repeated(ident, snapshot['source_key'])):
                    self.jobs.finish(ident, status='deferred', now=self.wall(), error_code='job_no_new_sources')
                    return
                ledger.accounting.provider_first = await model_context_capability(self.model, definition.profile, ledger.deadline)
                fresh(snapshot)
                if reviewing:
                    if definition.kind == 'notebook_care':
                        review = NotebookCare(self.jobs.care, ident, ledger, self.memory, self.notes,
                            fresh=lambda: self._eligible(self.jobs.check_run(ident), epoch), wall=self.wall, clock=self.clock)
                        result = await review.run(complete)
                        fresh(snapshot)
                        event('procedure_result', result)
                        self.jobs.finish(ident, status='completed', now=self.wall(),
                            result=json.dumps(result, ensure_ascii=False, separators=(',', ':')))
                        return
                    review = IdleReview(self.jobs, ident, conversation, ledger,
                        memory=self.memory, notes=self.notes, event=event,
                        # Original-input revalidation surrounds each provider
                        # call and publication. Each write also checks its exact
                        # origin inside the note transaction. Between operations,
                        # check live job/activity and memory views without rereading
                        # the full unchanged configuration for every read receipt.
                        fresh=lambda: self._eligible(self.jobs.check_run(ident), epoch))
                    inputs = messages(snapshot, definition.kind, self.wall())
                    if ledger.max_calls <= 1 or ledger.max_operations == 0:
                        inputs = review.closing_messages(inputs, 'call_limit' if ledger.max_calls <= 1 else 'tool_limit')
                    request = ledger.admit(inputs, self.clock())
                    result = await review.run(request, complete)
                    fresh(snapshot)
                    event('procedure_result', {k: v for k, v in result.items() if k != 'text'})
                    self.jobs.finish(ident, status='completed', now=self.wall(),
                        error_code='job_review_limited' if result['completion'] == 'limited' else None,
                        result=json.dumps(result, ensure_ascii=False, separators=(',', ':')))
                    return
                reply = await call(snapshot, 'decision')
                chosen = decision(reply, occurrence_ids=[n['occurrence_id'] for n in snapshot['reminders']],
                                  reminder=definition.kind == 'reminder_delivery')
                result = dict(origin='background_decision_not_delivered', **asdict(chosen), source_key=snapshot['source_key'],
                    occurrence_receipts=[dict(occurrence_id=n['occurrence_id'], receipt=n['receipt'])
                        for n in snapshot['reminders'] if n['occurrence_id'] in chosen.occurrence_ids])
                if chosen.decision == 'send' and definition.kind == 'reminder_delivery':
                    checked = await call(snapshot, 'factual_review', proposed=result)
                    if not accepted(checked):
                        event('procedure_result', dict(decision='defer', reason='factual_review_rejected'))
                        self.jobs.finish(ident, status='deferred', now=self.wall(), error_code='job_review_rejected')
                        return
                fresh(snapshot)
                event('procedure_result', result)
                self.jobs.finish(ident, status='completed' if chosen.decision == 'send' else 'deferred',
                    now=self.wall(), result=json.dumps(result, ensure_ascii=False, separators=(',', ':')),
                    delivery_snapshot=snapshot if chosen.decision == 'send' else None,
                    delivery_ledger=ledger if chosen.decision == 'send' else None)
        except asyncio.CancelledError:
            code = self.cancel_reasons.get(ident, 'job_interrupted')
            if researching:
                event('cancelled', dict(reason=code, active_seconds=math.ceil(max(0, self.clock()-started-ledger._paused_seconds))))
            self.jobs.finish(ident, status='cancelled', error_code=code, now=self.wall())
            raise
        except (Failure, TimeoutError) as error:
            code = error.code if isinstance(error, Failure) else 'timeout'
            if researching:
                event('error',dict(reason=code,detail=error.detail if isinstance(error,Failure)
                    else 'The shared run deadline ended before a report could be published. Saved evidence and any draft remain available.'))
            # Changed memory invalidates this transcript, not the saved task.
            # Retry admission rechecks its source/permissions and refuses replay
            # after any completed or uncertain MCP effect. Never refresh handles
            # in place or send the old evidence under a new memory snapshot.
            memory_restart = researching and code == 'memory_source_changed'
            if researching and (code in ('job_activity', 'job_foreground') or memory_restart):
                event('cancelled', dict(reason=code, active_seconds=math.ceil(max(0, self.clock()-started-ledger._paused_seconds))))
            self.jobs.finish(ident, status='cancelled' if memory_restart or code in ('job_activity', 'job_cancelled', 'job_foreground', 'job_source_changed', 'job_forbidden') else 'failed',
                error_code=code, now=self.wall())
        except Exception:
            self.jobs.finish(ident, status='failed', error_code='job_execution_failed', now=self.wall())
        finally:
            if procedure is not None and procedure.mcp:
                await procedure.mcp.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.loop is not None:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None
        await self.interrupt('job_owner_closed')
