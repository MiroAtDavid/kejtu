"""User reminders and conversation-only follow-ups with exact source support."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from .schedules import Schedule, instant


def identity(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class ReminderSupport:
    conversation_id: str
    user_id: str
    sha256: str
    quote: str

    def __post_init__(self):
        if not identity(self.conversation_id) or not identity(self.user_id):
            raise ValueError('Reminder support needs exact conversation/user-message identities.')
        if not isinstance(self.sha256, str) or re.fullmatch('[0-9a-f]{64}', self.sha256) is None:
            raise ValueError('Reminder support needs the exact original digest.')
        if (not isinstance(self.quote, str) or not self.quote.strip() or '\x00' in self.quote
                or len(self.quote.encode()) > 2048):
            raise ValueError('Use a bounded exact quotation from the user, not note instructions.')


@dataclass(frozen=True)
class ReminderDefinition:
    title: str
    text: str
    schedule: Schedule
    enabled: bool = False
    support: tuple[ReminderSupport, ...] = ()
    note_id: str | None = None
    task_id: str | None = None
    event_at: str | None = None
    timing: str = 'explicit'
    task_revision: int | None = None
    origin: str = 'requested'

    def __post_init__(self):
        for name, maximum in (('title', 500), ('text', 4096)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value.encode()) > maximum:
                raise ValueError(f'Reminder {name} must be exact nonempty text within its bound.')
        if type(self.schedule) is not Schedule or self.schedule.catch_up != 'coalesce':
            raise ValueError('Human reminder occurrences coalesce after downtime; they are not skipped jobs.')
        if type(self.enabled) is not bool:
            raise ValueError('Reminder scheduling must be explicitly enabled.')
        if (type(self.support) is not tuple or len(self.support) > 8
                or any(type(item) is not ReminderSupport for item in self.support)
                or len(set(self.support)) != len(self.support)):
            raise ValueError('Use at most eight distinct validated supporting user quotations.')
        if any(value is not None and not identity(value) for value in (self.note_id, self.task_id)):
            raise ValueError('Optional note/task links need exact identities, not executable text.')
        if self.timing not in ('explicit', 'flexible'):
            raise ValueError('Reminder timing is explicit or flexible.')
        if self.origin not in ('requested', 'companion'):
            raise ValueError('Use requested reminders or companion follow-ups.')
        if self.origin == 'companion' and (self.note_id or self.task_id or self.schedule.repeat != 'none'):
            raise ValueError('A companion follow-up has no task/note link and uses a one-time schedule.')
        if self.event_at is not None:
            instant(self.event_at)
        if self.task_revision is not None and (self.task_id is None or type(self.task_revision) is not int or not 1 <= self.task_revision < 2**53):
            raise ValueError('A linked task revision needs an exact task and positive revision.')
        if len(encoded(self.record()).encode()) > 12000:
            raise ValueError('The complete reminder definition exceeds its control-record bound.')

    def record(self):
        value = asdict(self)
        value['support'] = list(value['support'])
        # Old immutable definitions retain their exact canonical digest.
        if self.task_revision is None:
            value.pop('task_revision')
        if self.event_at is None:
            value.pop('event_at')
        if self.timing == 'explicit':
            value.pop('timing')
        if self.origin == 'requested':
            value.pop('origin')
        return value

    @classmethod
    def from_record(cls, value):
        optional = {'event_at', 'timing', 'task_revision', 'origin'}
        if (not isinstance(value, dict) or set(value)-set(cls.__dataclass_fields__)
                or not set(cls.__dataclass_fields__)-optional <= set(value)):
            raise ValueError('Reminder definitions require exactly their declared fields.')
        result = dict(value)
        try:
            if not isinstance(result['schedule'], dict) or set(result['schedule']) != set(Schedule.__dataclass_fields__):
                raise ValueError('Use a complete explicit reminder schedule.')
            result['schedule'] = Schedule(**result['schedule'])
            if not isinstance(result['support'], list) or len(result['support']) > 8:
                raise ValueError('Invalid reminder support list.')
            result['support'] = tuple(ReminderSupport(**item) for item in result['support'])
            return cls(**result)
        except TypeError:
            raise ValueError('Invalid reminder definition field types.') from None

    @property
    def digest(self):
        return hashlib.sha256(encoded(self.record()).encode()).hexdigest()
