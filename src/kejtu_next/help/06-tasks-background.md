# Tasks, reminders and background work

## Personal tasks are your commitments

Open **Tasks** in the main sidebar to see the list. Click a task title to open
its page. An existing description opens in **Preview**, where you can read it or
tick a checklist item. Choose **Write** to edit the Markdown. **New task** opens
with the title ready for typing and the description in Write. Give it a title,
add any due date or description, and choose **Save task**. Checklist edits do
not automatically complete the task.

The task page shows its due date, urgency and project or module alongside the
description. For a saved task, **Linked** and **History** hold its sources and
revisions. Return to **Tasks** for the list. **Continue draft** or **Return to
task** takes you back to unfinished work.

Use the checkbox to mark a task done. You can reopen it from **Completed**.
**Remove task** keeps its history and moves it to **Removed**, where **Restore
task** brings it back. Save an edited description before completing or removing
the task. You can also ask Kejtu to manage tasks when task access and writing
are enabled.

With Projects enabled, choose a **Project** and optionally a **Module**. A task
can belong directly to the project. The project filter includes its modules’
tasks, with each original task shown once. Existing multiple links stay unchanged
unless you explicitly choose a new placement. Due dates help planning; they do not
create reminders, run work or complete tasks. Local controls remain available
when conversational access is off.

Task urgency is Low, Medium or High. New and older unlabelled tasks start at Medium.
Choose urgency in the task editor; use the task list filters or Highest urgency
sort to focus the list. Urgency is independent of the due date and completion.
Changing it does not create a reminder or start work.

## Reminders ask you to act

Ask for a reminder in chat, or open a saved task and choose **Remind me**.
**Permissions & recall** controls conversational reminder access. A reminder can
stand on its own; it does not need a task. The task control saves a one-time
reminder and leaves the task's due date unchanged.

An explicit time, including “one hour before”, is the earliest reminder time.
If you leave the timing open, Kejtu may choose a sensible time with preparation
in mind and confirm the chosen schedule and timezone. The event time and reminder
time are separate. A late reminder must not imply it reached you earlier.

Home's reminder display and reminder dot are currently muted. Your requested
reminders stay saved; this pause does not cancel, acknowledge or postpone them.
During conversation, Kejtu can bring up a due reminder in an ordinary reply,
including the first reply after its due time. Due reminders enter normal
conversation context, with normal streaming and tools. Kejtu chooses what to
mention and uses reminder tools for changes. The app does not parse his answer
as proof that a reminder was mentioned. Unresolved due reminders remain eligible for each conversation reply. The current
clock and whole reminder records follow the admitted history, outside saved
instructions and summaries. Context limits may leave some records for the
reminder tool's paged list; supplied records retain their source dependencies.
After giving a reminder, Kejtu can resolve that occurrence through the ordinary
tool. It may also retire a reminder that clearly no longer applies. Overdue alone
is not enough. Resolution does not claim you acknowledged it, finish a task or
cancel future repetitions. A deferred reminder stays pending. Uncertain tool
changes are recovered by their saved receipt, without repeating the action.

Kejtu can also remember a useful conversational follow-up from a concrete
intention, upcoming event or unresolved matter you discuss, without you having
to say “remind me”. These are **companion follow-ups**, saved separately by kind
from reminders you requested. They are one-time conversational help, never
Home entries, personal tasks or recurring obligations. Kejtu uses judgment
about what deserves a follow-up and when it would be useful. Casual mentions
and quoted instructions do not automatically create one. This uses the same
conversation reminder permission and retains the original user-message source;
excluding that source from recall also prevents its use in later conversation.

You can ask what reminders or companion follow-ups are saved, change their time,
or cancel them in chat. **Got it** acknowledges an identified occurrence;
**Later** postpones it to the time you request, or thirty minutes if you leave
that open. Neither finishes a linked task, moves its due date nor cancels future
repetitions. Clearly identify the item: a bare “done” or “later” might refer to
the main conversation. Mentioning an item does not claim that you read or
acknowledged it, and a companion follow-up does not demand acknowledgment.

Reminders and follow-ups survive restarting. Missed occurrences coalesce and
become available after reopening. Delivery relies on conversation while the app
is open; there is no pop-up, sound or operating-system alarm. Older reminder
messages remain in chat history. Older source links that cannot be verified
remain unavailable to conversational recall; muting Home does not change their
saved records or grant access to them. Check-ins keep their separate behavior.

If a reminder action's response is interrupted, **Check status** in chat looks
up its saved result without repeating the action. **Keep unchanged** becomes
available only when the app confirms that no change was saved.

## Research tasks are work Kejtu performs

For repeated research, give complete criteria and the schedule you intend:

> Each Monday, check the official release notes for this project and save a
> report of relevant changes with sources. Use these criteria…

Kejtu's research assignments use the saved model profile, allowed tools and a
bounded work allowance. Search requires an enabled provider; reading shared
files or using MCP tools requires their current grants. One-off browsing does
not authorize a recurring task. Research can read permitted internal context,
but it cannot write notebook, People, reminders or personal tasks as a side effect
of background execution.

Open **Research** in the main sidebar. **New research** starts a conversation
with a draft where you can describe the search, sources and schedule. Send the
message when you are ready. To read results, choose an existing search by
name. **Criteria** opens its matching rules and controls. **Report** opens
the saved report when available. Choose **To review**, **All**, **Saved**,
**Reviewed** or **Dismissed**, then open a finding to read its details and source.
Home counts **findings to review**, names the searches, and separates **new**
from **changed** findings. Open a named search to go straight to its findings.
The count beside **Research** in the sidebar shows the same **findings to review**:
new or changed
findings that have not been dismissed. Reviewing or dismissing findings updates
the sidebar count immediately after the change is saved. The count disappears
when none remain, even if a report is still unread. Saved reports remain available in Research.

- **New**: you have never reviewed this finding, even if it changed meanwhile.
- **Changed**: a finding you previously reviewed has a newer saved version.
- **Reviewed**: you have acknowledged the current version. It stays in All findings.
- **Saved**: a finding you chose to keep close. Saving does not mark it reviewed;
  a saved item can still be New or Changed.
- **Dismissed**: hidden from the normal lists and review count; available in the
  Dismissed view, where Restore makes it visible again.

Use **Mark reviewed** on a finding to acknowledge exactly the displayed version.
Opening a page, expanding Details or following its source link does not acknowledge
it. **Mark report reviewed** acknowledges that report and the finding versions
added or changed in that report. Older unreviewed findings not included in that
report remain in To review. Search and page through the selected view; the tab
counts cover the entire search, not just the displayed page or text filter.

A report can have no new findings and still be ready to review. **Incomplete search**
means the saved report covers only part of the requested search; expand the
report for its summary and date. **Last full check** records only complete coverage.
Running, paused, failed and interrupted checks do not create a fresh report.
Older unreviewed findings remain available with their original dates if a later
check fails; the failed check is shown separately.
Reports retain sources, limitations and run status. An unfinished or failed latest
check must not be presented as a fresh successful result from an older run.
Results do not automatically send email. **Discuss** prepares a chat draft;
you decide whether to send it. Pausing and deleting research assignments are
local controls with their own consequences and confirmation.

You can ask Kejtu to **save**, **dismiss** or **reopen** individual findings in
chat when task access and writing are enabled. It first reads the current finding
and then changes its attention state through the same owner as the panel.
For example: “Dismiss the duplicate listings; keep the ones I saved.” Dismissal
hides a finding from the usual list and keeps its history; the **Dismissed** view lets
you inspect or reopen it. It does not delete the original report or merge URLs.
Saved criteria must be edited separately if you want future searches to follow a
new matching rule. A dismissal keeps that URL dismissed when it appears again,
but cannot by itself recognize the same listing on a different site.

## When work runs

The scheduler belongs to the open app; there is no always-on background service.
Idle observation, permissions, drafts/dialogs and foreground work affect when it
can run. Normal research waits for observed idle time and yields to conversation.
Closing Kejtu stops its scheduler. Missed work follows the saved catch-up policy;
it is not a guarantee of execution at an exact minute while the app is closed.
An uncertain external action is never automatically repeated just to obtain a
cleaner report.

Fresh installations enable daily **Notebook tidy** while Kejtu is open and idle,
using the configured model and computer's timezone. Automatic cleanup follows the
note-writing permission and keeps originals recoverable with Undo. Pause it under
**Settings → Planning → Background work → Notebook tidy**, then choose
**Save notebook care**. Updates keep existing schedules and disabled choices.

Fresh installations enable daily **Reflection** while Kejtu is open and idle,
using the configured model and computer's timezone. Change its frequency or turn
it off under **Settings → Planning → Background work → Reflection**, then choose **Save reflection**.
Existing saved schedules and disabled choices are preserved on updates.

**Review leftover reminders** is a daily idle job, using the same scheduler and
reminder tool. It can inspect accessible conversations to clear already-handled
or clearly obsolete occurrences, leaving useful or uncertain ones pending. Its
report stays private in **Settings → Planning → Background work → Advanced jobs**.
It sends no chat messages and cannot create reminders, change schedules or finish
tasks. Reminder source permissions and recall exclusions still apply. Runs with
no eligible due reminders make no model request. Change its schedule, saved model
profile, limits or enabled state in the job editor. New installations include the
job; updates preserve existing schedules and disabled choices.

Optional check-ins use their saved delivery permission and frequency limits.
Prepared messages appear in their conversation with a Check-in label; opening
Settings does not enable them. Requested reminders do not wait behind a check-in.
Notebook review/care uses its own saved settings and bounded jobs. Review can author or organize notes under its own permissions; it is not
infallible memory extraction. Opening a manager or Help does not enable a job.

Interest findings from permitted idle background searches also enter ordinary
conversation context. Kejtu can use his normal tools and notebook open threads;
findings do not select another reply mode or disable those tools. The same
finding is not automatically supplied again; this records context exposure,
not proof that you saw it. Settings retains **Stop** and **Not now**. With memory
and web access, Kejtu can use the ordinary `background_interests` tool for those
same controls in response to a clear request. **Not now** postpones future checks
for that interest by seven days. Bare phrases do not change a topic behind the
conversation; Kejtu resolves which interest you mean and uses the tool.

The private leftover review uses reminders with readable conversation sources and enabled reminder permission there. Old manual reminders without a verifiable conversation source remain available through their existing controls; the job does not guess their context.
