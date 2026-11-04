"""Small application-owned clock snapshots, outside the reusable prompt prefix."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfoNotFoundError

from tzlocal import reload_localzone

from ..core.records import Message


class ClockContext:
    def __init__(self, started, monotonic):
        self.started, self.monotonic = started, monotonic
        try:
            # The library handles Linux, macOS and Windows. Reload per turn so
            # travel/settings changes do not retain a process-cached timezone.
            self.zone = reload_localzone()
        except (OSError, ValueError, ZoneInfoNotFoundError):
            self.zone = None
        self.zone_name = getattr(self.zone, 'key', None)
        if self.zone_name == 'local':
            self.zone_name = None

    def snapshot(self):
        local = datetime.now(timezone.utc).astimezone(self.zone)
        elapsed = max(0, int(self.monotonic() - self.started))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        return dict(local_time=local.isoformat(timespec='seconds'),
                    timezone=self.zone_name or 'name unavailable; UTC offset is in local_time',
                    elapsed=f'{hours:02}:{minutes:02}:{seconds:02}')

    def message(self):
        return Message('system', 'Application clock snapshot for this message '
            '(local date/time; elapsed is HH:MM:SS since this turn started). '
            'Later tool results may supply a newer application_clock snapshot. '
            'These are clock readings, not a continuously running clock.\n'
            + json.dumps(self.snapshot(), ensure_ascii=False, separators=(',', ':')))

    def update(self, message, result, ledger, surrounding, *, headroom=0):
        # Add metadata only to a NEW native result, never rewrite a prior request
        # or source page. The existing ledger charges its complete wire text.
        # Optional time must yield to exact pages/committed receipts at tight fit.
        envelope = dict(result, application_clock=self.snapshot())
        candidate = replace(message, content=json.dumps(envelope, ensure_ascii=False, separators=(',', ':')))
        return candidate if ledger.remaining_input(surrounding + (candidate,)) >= headroom else message
