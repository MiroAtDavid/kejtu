"""Explicit immutable background definitions; text never supplies authority."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from .records import Profile
from .execution import DEFAULT_BACKGROUND_SECONDS, MAX_FOREGROUND_OPERATIONS, PROCEDURE_LIMIT_BOUNDS
from .schedules import Schedule
from .research import ResearchSpec

REVIEW_KINDS = ('idle_review', 'notebook_maintenance', 'notebook_care')
KINDS = ('check_in', *REVIEW_KINDS, 'reminder_delivery', 'research')
QUIET_KINDS = (*REVIEW_KINDS, 'research')
RUN_TERMINAL = ('completed', 'deferred', 'failed', 'cancelled', 'interrupted', 'skipped')


@dataclass(frozen=True)
class JobPermissions:
    read_memory: bool = False
    write_notes: bool = False
    deliver: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError('Background capabilities must be explicit booleans.')
        if (self.write_notes or self.deliver) and not self.read_memory:
            raise ValueError('Background writing/delivery requires explicit source-read permission.')


@dataclass(frozen=True)
class JobLimits:
    calls: int = 2
    operations: int = 0
    seconds: int = DEFAULT_BACKGROUND_SECONDS
    source_items: int = 12
    runs_per_day: int = 8
    cooldown_seconds: int = 180

    def __post_init__(self):
        for name, low, high in (*PROCEDURE_LIMIT_BOUNDS, ('source_items', 1, 32),
                                ('runs_per_day', 1, 48), ('cooldown_seconds', 60, 604800)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'Background {name} is outside its supported bound.')


def default_job_limits(kind):
    """New-job presets only; saved definitions always supply every field."""
    if kind not in KINDS:
        raise ValueError('Choose an installed background procedure.')
    if kind in QUIET_KINDS:
        # Care returns structured results per group. Reflection and maintenance
        # reserve one additional model response for their final private report.
        return JobLimits(calls=MAX_FOREGROUND_OPERATIONS + (kind != 'notebook_care'),
                         operations=MAX_FOREGROUND_OPERATIONS)
    return JobLimits()


DEFAULT_JOB_IDLE_SECONDS = 60


@dataclass(frozen=True)
class JobDefinition:
    kind: str
    title: str
    conversation_id: str | None
    schedule: Schedule
    profile: Profile
    permissions: JobPermissions = JobPermissions()
    limits: JobLimits | None = None
    enabled: bool = False
    idle_seconds: int = DEFAULT_JOB_IDLE_SECONDS
    delivery: str = 'quiet'
    research: ResearchSpec | None = None

    def __post_init__(self):
        if self.limits is None:
            object.__setattr__(self, 'limits', default_job_limits(self.kind))
        if self.kind not in KINDS:
            raise ValueError('Choose an installed bounded background procedure.')
        if (self.kind == 'research') != (type(self.research) is ResearchSpec):
            raise ValueError('Only research jobs have an explicit research assignment.')
        if self.kind == 'research' and (self.limits.operations == 0 or self.permissions.write_notes):
            raise ValueError('Research requires web operations and cannot write notebook notes.')
        if self.kind == 'notebook_care' and (self.conversation_id is not None or self.limits.operations == 0):
            raise ValueError('Notebook care requires independent notebook scope and at least one group operation.')
        if (not isinstance(self.title, str) or not self.title.strip()
                or len(self.title.encode()) > 500 or '\x00' in self.title):
            raise ValueError('Job title must be nonempty bounded text.')
        # A null focus is an independent notebook review, never an implicit chat.
        if self.conversation_id is None:
            if self.kind not in REVIEW_KINDS:
                raise ValueError('Only notebook reviews may omit a source conversation.')
        elif not isinstance(self.conversation_id, str) or re.fullmatch('[0-9a-f]{32}', self.conversation_id) is None:
            raise ValueError('Use the exact source-conversation identity.')
        if (type(self.schedule) is not Schedule or type(self.profile) is not Profile
                or type(self.permissions) is not JobPermissions or type(self.limits) is not JobLimits):
            raise ValueError('A job needs validated schedule, saved profile, permissions and limits.')
        if type(self.enabled) is not bool:
            raise ValueError('Job activation must be an explicit boolean.')
        if type(self.idle_seconds) is not int or not 30 <= self.idle_seconds <= 86400:
            raise ValueError('Background idle threshold must be 30–86400 whole seconds.')
        if self.delivery not in ('quiet', 'message'):
            raise ValueError('Delivery must explicitly be quiet or a conversational message.')
        if self.kind not in REVIEW_KINDS and self.permissions.write_notes:
            raise ValueError('Check-in/reminder decisions do not have notebook-writing authority.')
        if self.kind in QUIET_KINDS and (self.permissions.deliver or self.delivery != 'quiet'):
            raise ValueError('Private notebook review is not an already-sent message.')
        if self.kind not in QUIET_KINDS and self.limits.operations != 0:
            raise ValueError('Check-in/reminder decisions have no tool operations.')
        if self.enabled and (not self.permissions.read_memory
                or self.kind not in QUIET_KINDS and not self.permissions.deliver):
            raise ValueError('An enabled job requires its explicit procedure capabilities.')
        # No new context/output setting: the explicit saved background profile
        # owns the full reply reserve and effort; the existing Ledger will apply
        # these execution limits without changing a foreground profile.

    def record(self):
        value = asdict(self)
        # Existing immutable definitions retain their exact canonical digest.
        if self.research is None:
            value.pop('research')
        return value

    @classmethod
    def from_record(cls, value):
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) not in (fields, fields - {'research'}):
            raise ValueError('Job definition must contain exactly its declared fields.')
        value = dict(value)
        try:
            if 'research' in value:
                if not isinstance(value['research'], dict) or set(value['research']) != set(ResearchSpec.__dataclass_fields__):
                    raise ValueError('Research assignment needs its complete fields.')
                value['research'] = ResearchSpec(**value['research'])
            for field, kind in (('schedule', Schedule), ('profile', Profile),
                                ('permissions', JobPermissions), ('limits', JobLimits)):
                if not isinstance(value[field], dict) or set(value[field]) != set(kind.__dataclass_fields__):
                    raise ValueError('Nested job definitions must contain exactly their declared fields.')
                value[field] = kind(**value[field])
            return cls(**value)
        except TypeError:
            raise ValueError('Invalid job definition fields.') from None

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.record(), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
