import type { ReminderAttention } from "$lib/api/wire.generated";
import { dayInZone } from "../workspace/calendar-view";
import { civilInstant } from "../workspace/workspace";

// Presentation pause only. Keep user-requested reminders and their controls for
// a future Home design. Companion follow-ups are excluded by the API itself.
export const HOME_REMINDERS_MUTED = true;

// Calm points of a day for "Later". Each slot needs a real gap from now, so
// "Later" never comes back within minutes. Times are in the reminder's own zone.
const LATER_SLOTS = ["09:00", "14:00", "19:00"];
const LATER_GAP = 45 * 60000;
const NAMED = 3;

// Step civil calendar dates, never elapsed 24-hour periods: a 23-hour
// spring-forward day would otherwise skip the next morning entirely.
const nextDay = (day: string) =>
  new Date(Date.parse(day + "T12:00:00Z") + 86400000)
    .toISOString()
    .slice(0, 10);

export function laterUntil(now: Date, zone: string): string {
  let day = dayInZone(now, zone);
  for (let offset = 0; offset < 3; offset++, day = nextDay(day)) {
    for (const slot of LATER_SLOTS) {
      let at: number;
      try {
        at = Date.parse(civilInstant(`${day}T${slot}`, zone));
      } catch {
        continue; // A slot skipped by a daylight-saving change.
      }
      if (at - now.getTime() >= LATER_GAP) return new Date(at).toISOString();
    }
  }
  return new Date(now.getTime() + 3 * 3600000).toISOString();
}

/** Shown beside Later, so the chosen time is visible before it is used. */
export function laterLabel(until: string, now: Date, zone: string): string {
  if (!until) return "";
  const tomorrow = dayInZone(until, zone) !== dayInZone(now, zone);
  return `until ${tomorrow ? "tomorrow, " : ""}${clock(until, zone)}`;
}

export const clock = (value: string, zone: string) =>
  new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
  }).format(new Date(value));

function dayWord(value: string, zone: string, now: Date): string {
  const day = dayInZone(value, zone),
    today = dayInZone(now, zone);
  if (day === today) return "";
  const gap = Math.round(
    (Date.parse(today + "T12:00:00Z") - Date.parse(day + "T12:00:00Z")) /
      86400000,
  );
  if (gap === 1) return "yesterday";
  if (gap > 1 && gap < 7)
    return new Intl.DateTimeFormat(undefined, {
      weekday: "long",
      timeZone: "UTC",
    }).format(new Date(day + "T12:00:00Z"));
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    ...(day.slice(0, 4) !== today.slice(0, 4)
      ? { year: "numeric" as const }
      : {}),
    timeZone: "UTC",
  }).format(new Date(day + "T12:00:00Z"));
}

export type Entry = { item: ReminderAttention; note: string };
export type Arrangement = {
  named: Entry[];
  rest: Entry[];
  restLabel: string;
};

/** The Home line names today's reminders and folds older ones calmly away. */
export function arrange(
  items: ReminderAttention[],
  now: Date,
  more = false,
): Arrangement {
  const earlier = (item: ReminderAttention) =>
    dayWord(item.due_at, item.timezone, now) !== "";
  const today = items.filter((item) => !earlier(item));
  const older = items.filter(earlier);
  const note = (item: ReminderAttention): string => {
    const from = dayWord(item.due_at, item.timezone, now);
    if (from) return `from ${from}`;
    if (
      item.event_at &&
      Date.parse(item.event_at) > now.getTime() &&
      dayInZone(item.event_at, item.timezone) === dayInZone(now, item.timezone)
    )
      return `at ${clock(item.event_at, item.timezone)}`;
    return "";
  };
  const entry = (item: ReminderAttention) => ({ item, note: note(item) });
  const source = today.length ? today : older;
  const named = source.slice(0, NAMED).map(entry);
  const rest = [...source.slice(NAMED), ...(today.length ? older : [])].map(
    entry,
  );
  const onlyOlder = today.length > 0 && rest.length === older.length;
  const restLabel = more
    ? "more"
    : !rest.length
      ? ""
      : onlyOlder
        ? `${rest.length} from earlier`
        : `${rest.length} more`;
  return { named, rest, restLabel };
}

/** Quiet detail line for one reminder; the timezone is said only if different. */
export function detail(
  item: ReminderAttention,
  now: Date,
  homeZone: string,
): string {
  const from = dayWord(item.due_at, item.timezone, now);
  const parts = [
    `Set for ${from ? from[0].toUpperCase() + from.slice(1) + ", " : ""}${clock(item.due_at, item.timezone)}`,
  ];
  if (item.event_at) {
    const day = dayWord(item.event_at, item.timezone, now);
    parts.push(
      `event ${day && Date.parse(item.event_at) > now.getTime() ? "on " : ""}${day ? day + ", " : "at "}${clock(item.event_at, item.timezone)}`,
    );
  }
  if (homeZone && homeZone !== item.timezone)
    parts.push(item.timezone.replaceAll("_", " "));
  return parts.join(" · ");
}
