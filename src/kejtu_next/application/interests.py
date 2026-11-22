"""One optional check on the ordinary background ledger, with no model tools."""
from dataclasses import replace
import json
import time

from ..core import interests
from ..core.budget import SearchBudget
from ..core.records import Message
from .web import WebSession


class InterestRun:
    mcp = None
    def __init__(self, store, run_id, ledger, backends, *, fresh, wall, clock=time.monotonic):
        self.store, self.run_id, self.ledger = store, run_id, ledger
        self.fresh, self.wall, self.clock = fresh, wall, clock
        self.snapshot = store.store.memory.job_snapshot(run_id, ledger)
        self.web = WebSession(store.web_sources(run_id), backends, None, run_id, ledger)

    def check_operations(self):
        self.fresh()
        self.store.store.memory.check(self.snapshot, self.ledger)
        self.web.check()

    def request(self, messages):
        ledger = self.ledger
        request = ledger.admit(messages, self.clock()) if not ledger.calls else ledger.context_request(messages, self.clock())
        # The application chooses the three bounded read operations. The model
        # cannot access mail, files, tools, private URLs or arbitrary queries.
        return replace(request, tools=False, allow_tool_calls=False, history=False,
            memory=False, hybrid_memory=False, web=False, web_only=False)

    async def read(self, name, args, maximum=8192):
        self.check_operations()
        self.ledger.reserve_history_operation(self.clock())
        result = await self.web.operation(name, args, maximum, SearchBudget(maximum, Message('user', '')))
        self.check_operations()
        return result

    async def run(self, complete):
        ledger = self.ledger
        now = self.wall()
        candidates = self.store.candidates(self.run_id, self.snapshot, ledger, now)
        def messages():
            return (Message('system', interests.PLAN), Message('user', json.dumps(dict(checked_at=now, notes=candidates, stopped_topics=self.store.stopped_topics()), ensure_ascii=False)))
        # Omit whole candidates if necessary; never truncate source evidence.
        while candidates and ledger.remaining_input(messages()) < 0:
            candidates.pop()
        if not candidates:
            return dict(status='quiet', reason='no_supported_interest_fits', candidates=0)
        reply = await complete(self.request(messages()), 'interest_plan')
        selected = interests.plan(interests.object_reply(reply), candidates)
        if selected is None:
            self.store.reviewed(candidates, now, pause_existing=True)
            return dict(status='quiet', reason='no_sustained_public_interest', candidates=len(candidates))
        self.check_operations()
        ident = self.store.plan(self.run_id, self.snapshot, ledger, selected, now)
        if ident is None:
            self.store.reviewed([c for c in candidates if c['note_id'] == selected['note_id']], now, pause_existing=True)
            return dict(status='quiet', reason='topic_stopped_or_not_due')
        search = await self.read('web_search', dict(query=interests.query(selected, now), limit=5))
        pages = []
        seen = set()
        for hit in search.get('results', []):
            url = hit.get('url')
            if not isinstance(url, str) or url in seen:
                continue
            if len(seen) >= 2:
                break
            seen.add(url)
            page = await self.read('web_read', dict(url=url, max_bytes=8192))
            if page.get('source_status') != 'ok' or not page.get('content') or not page.get('url'):
                continue
            # Exact first page, explicitly retaining paging/coverage information.
            pages.append({key: page.get(key) for key in ('source_id', 'url', 'content', 'next_offset', 'total_bytes')})
        if not pages:
            return dict(status='quiet', reason='no_page_evidence', interest_id=ident)
        inputs = (Message('system', interests.FIND), Message('user', json.dumps(
            dict(topic=selected['topic'], checked_at=now, pages=pages), ensure_ascii=False)))
        # Public evidence still uses the same finite input/output allowance.
        # If it does not fit, report a quiet outcome rather than another call.
        if ledger.remaining_input(inputs) < 0:
            return dict(status='quiet', reason='page_evidence_does_not_fit', interest_id=ident)
        reply = await complete(self.request(inputs), 'interest_finding')
        result = interests.finding(interests.object_reply(reply), pages, now)
        self.check_operations()
        self.store.publish(self.run_id, ident, self.snapshot, ledger, result, now)
        return dict(status='saved' if result['finding'] else 'quiet', interest_id=ident,
            next_at=result['next_at'], calls=ledger.calls, operations=ledger.reads)
