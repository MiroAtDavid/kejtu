"""First-party procedures, delivered as ordinary exact SKILL.md packages.

These are application instructions, not installed user packages or permissions.
The generated frontmatter uses JSON strings, which are valid YAML; using these
shipped procedures therefore does not require the optional YAML import parser.
"""
from .skills import encode, sha


GUIDE = ('Project organization, study planning, tutoring and course research are built in. '
         'Discover with list_skills and load relevant procedures automatically; no user installation or selection is needed.')


PROCEDURES = {
    'kejtu-semester-organization': (
        'Organize projects, optional sections/modules and resources folders with readable names and useful structure. Use before creating or restructuring projects.',
        '''# Project organization
Read the relevant existing projects, sections/modules and permitted folders first. Reuse
matching records and useful existing structure. Use university describe for exact
fields and Files tools for actual folder/file operations; a section record alone
does not create directories. Do not promise work that the available tools cannot do.

The API retains semester for a Project and course for a Section/Module. A section
can have semester_id null; project dates starts_on and ends_on can be null. Never invent a project
or an end date to fill these optional fields. Repeating events still need their
own last date when there is no dated project end.

Use a human-readable project title; for a study section use the official course title. A download prefix, identifier or
filename is not the display title. Preserve source identifiers and provenance in
an appropriate course-info/source document when file writing is authorized; do not
invent record fields. If a title cannot be established from sources, ask rather
than guessing it. Distinguish source-confirmed facts from working assumptions.

For a new study section, a useful starting structure is materials/, notes/, assessment/
and course-info.md. Example: Programming 1, with its lecture PDFs in materials/,
the student's explanations in notes/ and assignment/exam information in assessment/.
Adapt to actual contents and reuse existing conventions. Never manufacture course
content to populate an empty folder. Do not rename, move or overwrite existing
student work merely to impose this example. Keep source bodies and references intact.

For study projects, read official semester dates, groups and timetable information where available.
Separate alternatives from chosen groups/exams. An exam option is not a booked
commitment: do not save every alternative as an ordinary blocking exam event.
Use existing semester/course/calendar owners; previews, current reads, quoted
user intent and existing permissions still apply. Folder access is a separate grant.
Report the actual records and folders created, plus any unresolved information.
'''),
    'kejtu-study-planning': (
        'Estimate sufficient study and distribute it around classes, employment, deadlines and chosen exams. Use before suggesting or saving study sessions.',
        '''# Study planning
Start from the student's goal, relevant course material, deadlines and chosen
exams. Read current Kejtu commitments, including work and existing study sessions.
Estimate the work still needed rather than filling every calendar gap. Use known
assignments, course workload, progress and available weeks. If using ECTS, establish
the institution's workload convention and remember it includes contact time;
never present a rough estimate as a measured requirement or double-count classes.
Show a concise, adjustable allocation per course. Ask only for missing information
that materially changes the plan; do not turn planning into a settings checklist.

Study hours come from the conversation. Use preferences stated there, such
as afternoons or no weekends, together with known work/class constraints. Pass
request-specific availability to calendar.suggest; this does not change settings.
Its exact object is timezone (IANA), buffer_minutes (0-240), windows (up to 28):
each window has weekday (Monday=0, Sunday=6), start and end (HH:MM, same day).
Use the calendar timezone and break/travel time. Saved weekly windows are retired;
do not reuse legacy windows as restrictions or ask the student to configure them.
Combine current requested hours with known commitments. Never silently fall back
to wider hours when explicit restrictions leave no suitable time.
Without conversational preferences, the tool tries provisional
09:00-18:00 hours each day. State that assumption, including possible weekend use,
and invite an ordinary correction. Empty calendar space does not prove free time.

calendar.suggest takes first/last (exclusive-end instants), minutes and optional
count/availability. Check dates and local timezone. It checks only Kejtu's timetable,
not external calendar widgets; disclose this when relevant. Suggestions are not
reservations. Preview conflicts and save sessions only when the current request
authorizes scheduling. Keep alternatives separate from chosen exams. Preserve
breaks, travel and realistic workload. If capacity is insufficient, show the gap
and offer a useful tradeoff instead of claiming the plan supplies enough study.
Study reservations, tasks, reminders and completed learning are different records.
'''),
    'kejtu-study-support': (
        'Explain course material, tutor, practise and assess attempts against exact sources. Use for Study Explain/Practise actions or learning requests.',
        '''# Learning and study support
Follow the student's actual request: explanation, one hint, practice, feedback or
a direct answer. Do not impose a quiz or withhold a requested solution. When the
student asks to practise, give one manageable source-linked question and let them
try before revealing its answer. Adapt to their demonstrated understanding.

Read the relevant exact course source before making source-specific claims.
For a retained source, use university materials.snapshot with its actual course_id
and artifact_id. Preserve original path, source hash and page references. Continue
exact pages when needed; a filename, summary or skill is not the source itself.
If access or complete relevant material is unavailable, say so. Clearly distinguish
source-backed teaching from additional general explanation. Never invent an excerpt.

Explain a useful small step, connect it to the student's question, and use examples
appropriate to the material. A hint should help the next step without leaking the
whole solution. Assess an attempt against the actual question and evidence, explain
what went wrong, and offer a specific next exercise where useful. The student's
reported confidence is separate from observed performance; one correct answer is
not evidence of mastery. Do not fabricate grades or progress.

Saved practice uses the existing practice fields and exact source references,
current user intent and writing permissions. Ordinary tutoring does not require
a saved practice record, new course, availability setting or skill installation.
For scheduling use the study-planning procedure; for external investigation use
study-research. These procedures do not grant file, web or university access.
'''),
    'kejtu-study-research': (
        'Find and verify official course information, study materials and supporting research. Use for university/course searches and source-based investigation.',
        '''# Course and study research
Establish the actual question, institution, course and semester from context.
Inspect relevant permitted local materials and existing sources before duplicating
work. For current dates, groups, exams, requirements or resources, use the available
web search/read tools and prefer official university/course sources. For academic
claims prefer original research and primary documentation. Do not treat search
snippets or a model's recollection as verified page contents.

Read the supporting source, record its URL/title and applicable semester/date,
and link claims to the evidence. Distinguish official facts, student choices,
inferences and missing information. Resolve conflicting dates where possible;
otherwise explain the specific uncertainty. Exam/group alternatives are choices,
not registrations or calendar commitments. Do not invent access, successful
downloads, enrollment or account synchronization.

Use current Files/Web/Study permissions. Public searching is not permission to
enter a student account. If material requires sign-in and no authorized connector
can reach it, explain the concrete gap and work with a user-provided document or
accessible official source. Never ask for passwords in chat or silently expand
tool permissions. Preserve exact retrieved bodies and provenance on authorized
imports; preserve student-authored notes and useful existing organization.

Return findings with relevant sources and practical next steps. A one-off research
request does not authorize a recurring background job. Reuse current research-task
controls only when the user actually asks for scheduled work. Use semester-
organization before creating course structures and study-planning before scheduling.
'''),
}


def packages():
    result = {}
    for name, (description, instructions) in PROCEDURES.items():
        body = ('---\nname: '+name+'\ndescription: '+encode(description)+'\n---\n'+instructions).encode()
        manifest = dict(name=name, metadata=dict(name=name, description=description),
                        origin=dict(kind='builtin'), files={'SKILL.md': dict(sha256=sha(body), bytes=len(body))})
        revision = sha(encode(manifest).encode())
        result[name] = dict(manifest=manifest, revision=revision, files={'SKILL.md': body})
    return result


BUNDLE_REVISION = sha(encode({name: package['revision'] for name, package in packages().items()}).encode())
