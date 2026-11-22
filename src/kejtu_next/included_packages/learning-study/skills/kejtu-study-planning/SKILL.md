---
name: kejtu-study-planning
description: "Plan learning and study time around coursework, exams and commitments. Use for study schedules, not ordinary project scheduling or calendar checks."
---
# Study planning
Use when the user is planning learning or study. A project alone does not imply study.
For general project structure, use kejtu-project-management.

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
