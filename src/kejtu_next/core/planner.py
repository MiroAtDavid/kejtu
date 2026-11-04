"""Human commitments and study intervals; scheduler jobs keep their own owner."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import re
from zoneinfo import ZoneInfo

from .schedules import Schedule, instant, utc

ID = re.compile(r'[0-9a-f]{32}')
KINDS = ('class', 'study', 'work', 'appointment', 'exam', 'deadline')


def text(value, name, maximum=500, *, empty=False):
    if (not isinstance(value, str) or '\x00' in value or len(value.encode()) > maximum
            or not empty and not value.strip()):
        raise ValueError(f'{name} must be text within {maximum} UTF-8 bytes.')
    return value


def day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Use a complete YYYY-MM-DD date.')
    return date.fromisoformat(value)


def identity(value, *, optional=False):
    if optional and value is None:
        return value
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Use an existing record identity.')
    return value


@dataclass(frozen=True)
class Semester:
    title: str
    starts_on: str | None
    ends_on: str | None
    timezone: str
    breaks: list = field(default_factory=list)

    def __post_init__(self):
        text(self.title, 'Project title')
        start = day(self.starts_on) if self.starts_on is not None else date(1900, 1, 1)
        end = day(self.ends_on) if self.ends_on is not None else None
        ZoneInfo(self.timezone)
        if not date(1900, 1, 1) <= start <= date(2200, 12, 31):
            raise ValueError('Project dates must be within 1900–2200.')
        if end is not None and not start <= end <= date(2200, 12, 31):
            raise ValueError('A project must end on or after its first day, within 1900–2200.')
        if not isinstance(self.breaks, list) or len(self.breaks) > 64:
            raise ValueError('Use at most 64 breaks.')
        for item in self.breaks:
            if not isinstance(item, dict) or set(item) != {'starts_on', 'ends_on'}:
                raise ValueError('A break needs its first and last date.')
            if not start <= day(item['starts_on']) <= day(item['ends_on']) <= (end or date(2200, 12, 31)):
                raise ValueError('Breaks must lie inside the project dates.')

    def contains(self, value):
        # An absent end is open, never a fabricated date saved into the record.
        return (day(self.starts_on) if self.starts_on else date(1900, 1, 1)) <= value <= date(2200, 12, 31) and (
            self.ends_on is None or value <= day(self.ends_on))

    def teaching(self, value):
        return (self.contains(value)
                and not any(day(item['starts_on']) <= value <= day(item['ends_on']) for item in self.breaks))


@dataclass(frozen=True)
class Course:
    title: str
    semester_id: str | None = None
    folder: str | None = None

    def __post_init__(self):
        text(self.title, 'Section title')
        identity(self.semester_id, optional=True)
        if self.folder is not None:
            text(self.folder, 'Resources folder', 4096)
            # Records retain cross-platform path syntax; actual access and
            # canonical identity remain the shared-folder adapter's authority.
            if not self.folder.startswith('/') and not re.match(r'^[A-Za-z]:[\\/]', self.folder):
                raise ValueError('Choose an absolute shared folder path.')


@dataclass(frozen=True)
class Event:
    title: str
    starts_at: str
    ends_at: str
    timezone: str
    kind: str = 'appointment'
    repeat: str = 'none'
    interval: int = 1
    until: str | None = None
    course_id: str | None = None
    task_id: str | None = None
    location: str = ''
    meeting_type: str = ''
    all_day: bool = False
    material: list = field(default_factory=list)

    def __post_init__(self):
        text(self.title, 'Event title')
        text(self.location, 'Location', 2048, empty=True)
        identity(self.course_id, optional=True)
        identity(self.task_id, optional=True)
        if self.kind not in KINDS or self.repeat not in ('none', 'daily', 'weekly'):
            raise ValueError('Choose a supported event kind and recurrence.')
        if self.meeting_type not in ('', 'lecture', 'tutorial', 'seminar', 'lab'):
            raise ValueError('Choose lecture, tutorial, seminar or lab.')
        if type(self.all_day) is not bool:
            raise ValueError('All-day must be a boolean.')
        # Both endpoints must be real local instants in the declared zone.
        Schedule(self.starts_at, self.timezone, self.repeat, self.interval)
        Schedule(self.ends_at, self.timezone)
        start, end = instant(self.starts_at), instant(self.ends_at)
        if not start < end <= start + timedelta(days=7):
            raise ValueError('Events must have positive duration of at most seven days.')
        local_start = start.astimezone(ZoneInfo(self.timezone))
        local_end = end.astimezone(ZoneInfo(self.timezone))
        if self.repeat != 'none':
            period = self.interval * (7 if self.repeat == 'weekly' else 1)
            wall_duration = local_end.replace(tzinfo=None)-local_start.replace(tzinfo=None)
            if wall_duration > timedelta(days=period):
                raise ValueError('A repeating event cannot overlap its next occurrence.')
        if self.until is not None and day(self.until) < local_start.date():
            raise ValueError('Recurrence end must not precede the first event.')
        if not isinstance(self.material, list) or len(self.material) > 16:
            raise ValueError('Link at most 16 material references.')
        for source in self.material:
            if not isinstance(source, dict) or set(source) - {'path', 'sha256', 'page', 'offset', 'artifact_id'}:
                raise ValueError('Material references need exact source fields.')
            text(source.get('path'), 'Source path', 4096)
            if 'sha256' in source and (not isinstance(source['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',source['sha256'])):
                raise ValueError('Material SHA-256 must identify the exact original.')
            if 'artifact_id' in source:identity(source['artifact_id'])
            for name in ('page','offset'):
                if name in source and (type(source[name]) is not int or not 0<=source[name]<2**63):
                    raise ValueError('Material page/offset must be nonnegative integers.')

    @property
    def schedule(self):
        return Schedule(self.starts_at, self.timezone, self.repeat, self.interval)


def local_instant(value, zone):
    """A UI-local timestamp must resolve to an actual civil time, never normalize."""
    result = value.replace(tzinfo=zone, fold=0)
    if result.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value:
        return None
    return result


def _end(event, start):
    original_start = datetime.fromisoformat(event.starts_at)
    original_end = datetime.fromisoformat(event.ends_at)
    if instant(event.starts_at) == start:
        return original_end
    zone = ZoneInfo(event.timezone)
    wall_days = (original_end.date()-original_start.date()).days
    target = datetime.combine(start.astimezone(zone).date()+timedelta(days=wall_days),
                              original_end.timetz().replace(tzinfo=None))
    return local_instant(target, zone)


def expand(ident, event, first, last, *, semester=None, exceptions=(), revision=1, limit=1000):
    """Return visible intervals, including occurrences moved into this window."""
    first, last = instant(first), instant(last)
    if last <= first or last-first > timedelta(days=92):
        raise ValueError('Choose a calendar view of at most 92 days.')
    zone = ZoneInfo(event.timezone)
    changes = {instant(item['occurrence_at']): item for item in exceptions}
    # Look back for overnight intervals without stepping outside Schedule's
    # supported lower boundary when the first displayed week is in January1900.
    earliest = datetime(1900,1,1,tzinfo=timezone.utc)
    candidates = set(event.schedule.between(utc(max(earliest,first-timedelta(days=7))), utc(last), limit=limit))
    candidates.update(changes)
    result = []
    for original in sorted(candidates):
        if original in changes and original not in event.schedule.between(
                utc(max(earliest,original-timedelta(seconds=1))),utc(original+timedelta(seconds=1)),limit=1):
            raise ValueError('An exception must identify an actual occurrence of this event.')
        local_day = original.astimezone(zone).date()
        if event.until and local_day > day(event.until):
            continue
        if semester and event.kind == 'class' and not semester.teaching(local_day):
            continue
        end = _end(event, original)
        if end is None or instant(end.isoformat()) <= original:
            continue  # A future nonexistent civil endpoint is skipped, like its anchor.
        start = original
        change = changes.get(original)
        if change:
            if change['cancelled']:
                continue
            start = instant(change['starts_at'])
            end = datetime.fromisoformat(change['ends_at'])
        if start < last and instant(end.isoformat()) > first:
            result.append(dict(id=hashlib.sha256((ident+utc(original)).encode()).hexdigest()[:32],
                event_id=ident, revision=revision, occurrence_at=utc(original),
                starts_at=start.astimezone(zone).isoformat(), ends_at=end.astimezone(zone).isoformat(),
                **{name: getattr(event, name) for name in ('title','kind','course_id','task_id','location','meeting_type','all_day','material')}))
            if len(result) > limit:
                raise ValueError('Too many calendar occurrences; choose a smaller range.')
    return result


def conflicts(candidate, occurrences, *, buffer_minutes=0):
    if type(buffer_minutes) is not int or not 0 <= buffer_minutes <= 240:
        raise ValueError('Buffer must be 0–240 minutes.')
    before = timedelta(minutes=buffer_minutes)
    start, end = instant(candidate['starts_at']), instant(candidate['ends_at'])
    return [item for item in occurrences if item['kind'] != 'deadline'
            and (candidate.get('id') is None or item.get('id') != candidate['id'])
            and start < instant(item['ends_at'])+before and end > instant(item['starts_at'])-before]


def availability(value):
    if not isinstance(value, dict) or set(value) != {'timezone','windows','buffer_minutes'}:
        raise ValueError('Availability needs timezone, windows and buffer_minutes.')
    zone = ZoneInfo(value['timezone'])
    if type(value['buffer_minutes']) is not int or not 0 <= value['buffer_minutes'] <= 240:
        raise ValueError('Buffer must be 0–240 minutes.')
    if not isinstance(value['windows'], list) or len(value['windows']) > 28:
        raise ValueError('Choose at most 28 weekly availability windows.')
    for item in value['windows']:
        if not isinstance(item, dict) or set(item) != {'weekday','start','end'}:
            raise ValueError('Each window needs weekday, start and end.')
        if type(item['weekday']) is not int or not 0 <= item['weekday'] <= 6:
            raise ValueError('Weekdays run from Monday 0 to Sunday 6.')
        for key in ('start','end'):
            if not isinstance(item[key], str) or not re.fullmatch(r'\d{2}:\d{2}',item[key]):
                raise ValueError('Use HH:MM availability times.')
        if not time.fromisoformat(item['start']) < time.fromisoformat(item['end']):
            raise ValueError('Availability windows need an end after their start.')
    return zone


def study_preferences(preferences):
    """Empty saved preferences permit provisional suggestions, never bookings."""
    availability(preferences)
    if preferences['windows']:return preferences
    return dict(preferences,windows=[dict(weekday=day,start='09:00',end='18:00') for day in range(7)])


def suggestions(first, last, preferences, occurrences, *, minutes, count=2):
    """Deterministic free intervals; choosing a suggestion is a separate write."""
    preferences = study_preferences(preferences)
    zone = availability(preferences)
    first, last = instant(first), instant(last)
    if not first < last <= first+timedelta(days=92):
        raise ValueError('Find study slots within a range of at most 92 days.')
    if type(minutes) is not int or not 5 <= minutes <= 480 or type(count) is not int or not 1 <= count <= 20:
        raise ValueError('Choose 1–20 sessions of 5–480 minutes.')
    result = []
    today = first.astimezone(zone).date()
    while today <= last.astimezone(zone).date():
        for window in preferences['windows']:
            if window['weekday'] != today.weekday():
                continue
            cursor = datetime.combine(today,time.fromisoformat(window['start']))
            finish = datetime.combine(today,time.fromisoformat(window['end']))
            while cursor+timedelta(minutes=minutes) <= finish:
                start = local_instant(cursor,zone)
                end = local_instant(cursor+timedelta(minutes=minutes),zone)
                if start and end and first <= instant(start.isoformat()) < instant(end.isoformat()) <= last:
                    slot = dict(starts_at=start.isoformat(),ends_at=end.isoformat(),kind='study',timezone=preferences['timezone'])
                    if not conflicts(slot, [*occurrences,*result],buffer_minutes=preferences['buffer_minutes']):
                        result.append(slot)
                        if len(result) == count:
                            return result
                cursor += timedelta(minutes=15)
        today += timedelta(days=1)
    return result
