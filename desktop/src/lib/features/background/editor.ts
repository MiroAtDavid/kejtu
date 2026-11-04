import {
  DEFAULT_JOB_LIMITS,
  DEFAULT_JOB_IDLE_SECONDS,
} from "$lib/api/wire.generated";
import type { JobDefinition, JobRun, Profile } from "$lib/api/wire.generated";

export function isReview(kind: JobDefinition["kind"]): boolean {
  return (
    kind === "idle_review" ||
    kind === "notebook_maintenance" ||
    kind === "notebook_care"
  );
}

export const procedures = {
  research: {
    title: "Research assignment",
    description: "Created through chat; results and controls are in Tasks.",
  },
  notebook_care: {
    title: "Keep notebook tidy",
    description:
      "Review complete note groups, save progress and continue during idle time. Automatic cleanup can merge, split and retire notes with Undo.",
  },
  notebook_maintenance: {
    title: "Notebook maintenance",
    description:
      "Check existing notes for duplication, unnecessary detail, organisation and possible corrections. Keep findings in a private report. Proposed merges and removals need review.",
  },
  idle_review: {
    title: "Reflection",
    description:
      "Develop useful observations, ideas and connections from accessible notes and optional conversation context. With permission, this job may add new notes.",
  },
  check_in: {
    title: "Prepare a check-in",
    description:
      "Prepare a useful follow-up to a conversation, or decide no message is needed. Automatic display in chat is not available yet.",
  },
  reminder_delivery: {
    title: "Prepare a reminder message",
    description:
      "Prepare a message about existing reminders. This job does not create reminders. Automatic display in chat is not available yet.",
  },
} as const;

export function localZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function parts(date: Date, zone: string) {
  return Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );
}

export function dateInput(timestamp: string, zone: string): string {
  try {
    const p = parts(new Date(timestamp), zone);
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  } catch {
    return "";
  }
}

function offsetAt(date: Date, zone: string): number {
  const p = parts(date, zone);
  return (
    Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) -
    date.getTime()
  );
}

function timestamp(date: Date, zone: string): string {
  const offset = offsetAt(date, zone) / 60000;
  const sign = offset < 0 ? "-" : "+";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${dateInput(date.toISOString(), zone)}:00${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
}

/** UI conversion only. The backend remains the recurrence and timezone authority.
 * A DST gap or repeated time must never be silently moved to another instant.
 * Existing exact timestamps are left untouched until the user edits the input.
 */
export function scheduleTime(
  value: string,
  zone: string,
  savedZone = zone,
): string {
  value = value.replace(" ", "T");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error("Use YYYY-MM-DD HH:mm, with a 24-hour time.");
  const wall = new Date(`${value}:00Z`).getTime();
  if (!Number.isFinite(wall))
    throw new Error("Choose a valid start date and time.");
  const offsets = new Set<number>();
  for (let hours = -36; hours <= 36; hours += 6)
    offsets.add(offsetAt(new Date(wall + hours * 3600000), zone));
  const matches = [...offsets]
    .map((offset) => new Date(wall - offset))
    .filter((date) => dateInput(date.toISOString(), zone) === value);
  if (matches.length !== 1)
    throw new Error(
      "This time is missing or occurs twice in this time zone. Choose another time, or set the exact UTC offset in Advanced.",
    );
  // Editing in local time must retain the saved recurrence zone and its
  // matching start offset. Merely displaying a job never rewrites its timestamp.
  return timestamp(matches[0], savedZone);
}

export function newJob(profile: Profile, now = new Date()): JobDefinition {
  const zone = localZone();
  const start = new Date(Math.floor(now.getTime() / 60000) * 60000);
  return {
    kind: "notebook_maintenance",
    title: procedures.notebook_maintenance.title,
    conversation_id: null,
    enabled: false,
    idle_seconds: DEFAULT_JOB_IDLE_SECONDS,
    delivery: "quiet",
    schedule: {
      starts_at: timestamp(start, zone),
      timezone: zone,
      repeat: "none",
      interval: 1,
      catch_up: "coalesce",
      grace_seconds: 60,
    },
    // Visible read-only choice becomes authority only after an explicit save.
    permissions: { read_memory: true, write_notes: false, deliver: false },
    limits: { ...DEFAULT_JOB_LIMITS.notebook_maintenance },
    profile: structuredClone(profile),
  };
}

export function changeProcedure(
  job: JobDefinition,
  kind: JobDefinition["kind"],
  conversation = "",
): void {
  if (kind === job.kind) return;
  if (job.title === procedures[job.kind].title)
    job.title = procedures[kind].title;
  job.kind = kind;
  if (!isReview(kind) && job.conversation_id === null)
    job.conversation_id = conversation;
  job.permissions.write_notes = false;
  job.permissions.deliver = !isReview(kind) && job.permissions.read_memory;
  // Procedure changes select its model/tool allowance. Preserve independently
  // chosen timing, source count and frequency; reading a saved job changes none.
  job.limits.calls = DEFAULT_JOB_LIMITS[kind].calls;
  job.limits.operations = DEFAULT_JOB_LIMITS[kind].operations;
  if (isReview(kind)) job.delivery = "quiet";
}

export function scheduleChoice(job: JobDefinition): string {
  const { repeat, interval } = job.schedule;
  return interval === 1 && repeat !== "minutes" ? repeat : "custom";
}

/** Explicit year-first dates avoid native locale-dependent month/day order. */
export function startInput(timestamp: string, zone: string): string {
  return dateInput(timestamp, zone).replace("T", " ");
}

export function when(timestamp: string, zone = localZone()): string {
  const formatted = startInput(timestamp, zone);
  return formatted ? `${formatted} · ${zone}` : timestamp;
}

export function scheduleSummary(
  job: JobDefinition,
  zone = localZone(),
): string {
  const { repeat, interval, starts_at } = job.schedule;
  const unit = {
    minutes: "minute",
    daily: "day",
    weekly: "week",
    monthly: "month",
  };
  const timing =
    repeat === "none"
      ? "Once"
      : `Every ${interval === 1 ? "" : `${interval} `}${unit[repeat]}${interval === 1 ? "" : "s"}`;
  return `${timing} · ${repeat === "none" ? "from" : "starting"} ${when(starts_at, zone)}`;
}

export function runLabel(
  status: JobRun["status"],
  reason?: string | null,
): string {
  if (status === "running" && reason === "research_paused")
    return "Waiting for idle";
  if (status === "completed" && reason === "job_review_limited")
    return "Partial review";
  return {
    pending: "Waiting to start",
    running: "In progress",
    cancel_requested: "Stopping",
    completed: "Completed",
    deferred: "No message needed",
    failed: "Failed",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
    skipped: "Skipped",
  }[status];
}

export function privateResult(
  body: {
    status: string;
    content: string;
    offset: number;
    next_offset: number | null;
  } | null,
): string | null {
  // Partial JSON is not a complete result. Keep the existing exact paging path.
  if (
    !body ||
    body.status !== "complete" ||
    body.offset !== 0 ||
    body.next_offset !== null
  )
    return null;
  try {
    const value = JSON.parse(body.content);
    return typeof value.text === "string" && value.text.trim()
      ? value.text
      : null;
  } catch {
    return null;
  }
}

export function hasReviewToolMarkup(text: string | null): boolean {
  // Older immutable results predate the backend closing-account check. This
  // identifies control markup only; it never interprets or executes commands.
  // Shared acceptance cases: tests/fixtures/review-output.json at project root.
  return text !== null && /<\/?[|｜]+DSML[|｜]+/.test(text);
}
