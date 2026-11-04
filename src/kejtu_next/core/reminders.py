"""Explicit human reminders, independent of notes, commitments and executable jobs."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from .schedules import Schedule


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
        if len(encoded(self.record()).encode()) > 12000:
            raise ValueError('The complete reminder definition exceeds its control-record bound.')

    def record(self):
        value = asdict(self)
        value['support'] = list(value['support'])
        return value

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
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
