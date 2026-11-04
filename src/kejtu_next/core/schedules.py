"""One pure recurrence owner for job schedules and independent reminders.

No wall-clock reads, event loop, storage or generation. The caller supplies an
aware instant. Calendar schedules retain the original civil anchor: missing
month days / local times are skipped, and future ambiguous times use the earlier
instant. Elapsed minute intervals deliberately do not claim wall-clock recurrence.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
FIRST_YEAR, LAST_YEAR = 1900, 2200
MAX_SEARCH = 64


def instant(value: str) -> datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('Use a bounded ISO timestamp including an explicit UTC offset.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Use an ISO timestamp including an explicit UTC offset.') from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('A schedule time must include its UTC offset.')
    result = result.astimezone(UTC)
    if not FIRST_YEAR <= result.year <= LAST_YEAR:
        raise ValueError('Schedule instants must be between 1900 and 2200.')
    return result


def utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Supply an aware clock; local machine timezone is not authority.')
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


@dataclass(frozen=True)
class Due:
    """One catch-up decision, not a fabricated list of all missed executions."""
    first_due: str
    covered_through: str
    next_at: str | None
    scheduled_at: str | None

    @property
    def materialize(self):
        return self.scheduled_at is not None


@dataclass(frozen=True)
class Schedule:
    starts_at: str
    timezone: str
    repeat: str = 'none'
    interval: int = 1
    catch_up: str = 'coalesce'
    grace_seconds: int = 60

    def __post_init__(self):
        start = instant(self.starts_at)
        if not isinstance(self.timezone, str) or not 1 <= len(self.timezone.encode()) <= 128:
            raise ValueError('Use an explicit IANA timezone.')
        try:
            zone = ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError('Use an available IANA timezone.') from None
        supplied = datetime.fromisoformat(self.starts_at.replace('Z', '+00:00'))
        # An offset must describe this zone at this instant, including a chosen
        # fold at the anchor; nonexistent civil anchors are never normalized.
        if (supplied.utcoffset() != start.astimezone(zone).utcoffset()
                or supplied.replace(tzinfo=None) != start.astimezone(zone).replace(tzinfo=None)):
            raise ValueError('The timestamp offset/local time does not match its timezone.')
        if self.repeat not in ('none', 'minutes', 'daily', 'weekly', 'monthly'):
            raise ValueError('Repeat must be none, minutes, daily, weekly or monthly.')
        maximum = 10080 if self.repeat == 'minutes' else 366
        if type(self.interval) is not int or not 1 <= self.interval <= maximum:
            raise ValueError('Recurrence interval is outside its bounded range.')
        if self.repeat == 'none' and self.interval != 1:
            raise ValueError('A one-shot schedule has no recurrence interval.')
        if self.catch_up not in ('coalesce', 'skip'):
            raise ValueError('Missed runs must explicitly coalesce or skip.')
        if type(self.grace_seconds) is not int or not 0 <= self.grace_seconds <= 3600:
            raise ValueError('Schedule grace must be 0–3600 whole seconds.')

    @property
    def start(self):
        return instant(self.starts_at)

    @property
    def local_start(self):
        return self.start.astimezone(ZoneInfo(self.timezone))

    @property
    def timing_key(self):
        """Recurrence identity, distinct from display and missed-run policy.

        Reformatting an equivalent instant or editing catch-up/grace is not a
        new trigger. A timezone affects civil recurrence, not elapsed minutes
        or a one-shot instant. Keep exact submitted fields in the definition.
        """
        zone = self.timezone if self.repeat in ('daily', 'weekly', 'monthly') else None
        return (utc(self.start), self.repeat, self.interval, zone)

    def between(self, first: str, last: str, *, limit: int = 1000):
        """Bounded occurrence instants, also used by the human calendar.

        This is observation, independent of job catch-up/materialization. Civil
        anchors and DST decisions still belong to this one recurrence owner.
        """
        start, end = instant(first), instant(last)
        if end <= start or end - start > timedelta(days=366):
            raise ValueError('Choose a calendar range of at most 366 days.')
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid calendar occurrence limit.')
        index = max(0, self._index(start) - 1)
        result = []
        for _ in range(20000):
            candidate = self._candidate(index)
            index += 1
            if candidate is not None:
                if candidate >= end:
                    return tuple(result)
                if candidate >= start:
                    if len(result) == limit:
                        raise ValueError('Calendar range contains too many occurrences; choose a smaller range.')
                    result.append(candidate)
            if self.repeat == 'none':
                return tuple(result)
        raise ValueError('Calendar recurrence work limit reached; choose a smaller range.')

    def _index(self, value):
        """Jump near the target, never replay centuries of missed heartbeats."""
        start = self.local_start
        local = value.astimezone(start.tzinfo)
        if self.repeat == 'none':
            return 0
        if self.repeat == 'minutes':
            return max(0, int((value - self.start).total_seconds() // (60 * self.interval)))
        if self.repeat == 'monthly':
            return max(0, ((local.year - start.year) * 12 + local.month - start.month) // self.interval)
        days = self.interval * (7 if self.repeat == 'weekly' else 1)
        return max(0, (local.date() - start.date()).days // days)

    def _candidate(self, index):
        if index == 0:
            return self.start
        if index < 0 or self.repeat == 'none':
            return None
        start = self.local_start
        try:
            if self.repeat == 'minutes':
                result = self.start + timedelta(minutes=self.interval * index)
                return result if result.year <= LAST_YEAR else None
            if self.repeat == 'monthly':
                month = start.year * 12 + start.month - 1 + self.interval * index
                year, month = divmod(month, 12)
                if year > LAST_YEAR:
                    return None
                day = date(year, month + 1, start.day)  # Missing date is skipped, never clipped.
            else:
                day = start.date() + timedelta(days=self.interval * index * (7 if self.repeat == 'weekly' else 1))
            if day.year > LAST_YEAR:
                return None
            naive = datetime.combine(day, start.timetz().replace(tzinfo=None))
            local = naive.replace(tzinfo=start.tzinfo, fold=0)
            result = local.astimezone(UTC)
            if result.astimezone(start.tzinfo).replace(tzinfo=None) != naive:
                return None  # Nonexistent local time, including whole skipped dates.
            return result
        except (OverflowError, ValueError):
            return None

    def next_after(self, value: str, *, inclusive=False) -> str | None:
        after = instant(value)
        if type(inclusive) is not bool:
            raise ValueError('inclusive must be boolean')
        if after < self.start or inclusive and after == self.start:
            return utc(self.start)
        if self.repeat == 'none':
            return None
        index = self._index(after)
        for offset in range(MAX_SEARCH):
            candidate = self._candidate(index + offset)
            if candidate is not None and (candidate > after or inclusive and candidate == after):
                return utc(candidate)
        # The supported calendar interval has ended, or the rules cannot find
        # a next occurrence within the fixed work bound. Never spin a scheduler.
        if self._past_end(index + MAX_SEARCH - 1):
            return None
        raise ValueError('No next calendar occurrence within the bounded search.')

    def _past_end(self, index):
        start = self.local_start
        if self.repeat == 'monthly':
            return start.year * 12 + start.month - 1 + self.interval * index >= (LAST_YEAR + 1) * 12
        if self.repeat == 'minutes':
            return self.start + timedelta(minutes=self.interval * index) >= datetime(LAST_YEAR + 1, 1, 1, tzinfo=UTC)
        return start.date().toordinal() + self.interval * index * (7 if self.repeat == 'weekly' else 1) > date(LAST_YEAR, 12, 31).toordinal()

    def latest_at(self, value: str) -> str | None:
        before = instant(value)
        if before < self.start:
            return None
        index = self._index(before)
        for offset in range(min(MAX_SEARCH, index + 1)):
            candidate = self._candidate(index - offset)
            if candidate is not None and candidate <= before:
                return utc(candidate)
        raise ValueError('No preceding calendar occurrence within the bounded search.')

    def advance(self, next_at: str | None, now: str) -> Due | None:
        """Consume due time once; storage must commit this with the occurrence.

        Coalesce preserves the first pending trigger and records the last covered
        trigger. Skip materializes only the latest trigger within explicit grace.
        Neither mode replays every missed period or acknowledges an existing notice.
        """
        clock = instant(now)
        if next_at is None:
            return None
        due = instant(next_at)
        if self.next_after(next_at, inclusive=True) != utc(due):
            raise ValueError('Stored next time is not aligned to the original schedule.')
        if due > clock:
            return None
        latest = self.latest_at(now)
        assert latest is not None
        recent = (clock - instant(latest)).total_seconds() <= self.grace_seconds
        selected = utc(due) if self.catch_up == 'coalesce' else latest if recent else None
        return Due(utc(due), latest, self.next_after(now), selected)
