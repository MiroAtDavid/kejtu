import type { TimelineItem } from "$lib/design/Timeline.svelte";
import type { Event, RecordRow } from "./workspace";
import { dayInZone, clockTime } from "./calendar-view";

function civilLabel(day: string): string {
  // A recurrence's until value is a civil date, never an instant in the host zone.
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(day + "T12:00:00Z"));
}
function offset(value: string, zone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    timeZoneName: "shortOffset",
  })
    .formatToParts(new Date(value))
    .find((part) => part.type === "timeZoneName")!.value;
}

/** A course lists saved events/series, not expanded calendar occurrences. */
export function courseTimeline(
  rows: RecordRow<Event>[],
  courseId: string,
): TimelineItem[] {
  return rows
    .filter((row) => row.data.course_id === courseId)
    .sort(
      (a, b) =>
        Date.parse(a.data.starts_at) - Date.parse(b.data.starts_at) ||
        a.id.localeCompare(b.id),
    )
    .map((row) => {
      const event = row.data,
        zone = event.timezone;
      const startDay = dayInZone(event.starts_at, zone);
      // All-day end instants are exclusive, including across daylight-saving changes.
      const endDay = dayInZone(
        event.all_day ? new Date(Date.parse(event.ends_at) - 1) : event.ends_at,
        zone,
      );
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: zone,
        day: "2-digit",
        month: "short",
        year: "numeric",
        weekday: "short",
      }).formatToParts(new Date(event.starts_at));
      const part = (type: string) => parts.find((p) => p.type === type)!.value;
      const startOffset = offset(event.starts_at, zone),
        endOffset = offset(event.ends_at, zone);
      // Preserve ambiguous clock times at DST folds instead of implying zero duration.
      const clock = (value: string, suffix: string) =>
        clockTime(value, zone) +
        (startOffset !== endOffset ? ` (${suffix})` : "");
      const time = event.all_day
        ? "All day"
        : event.starts_at === event.ends_at
          ? clock(event.starts_at, startOffset)
          : `${clock(event.starts_at, startOffset)}–${startDay === endDay ? "" : civilLabel(endDay) + ", "}${clock(event.ends_at, endOffset)}`;
      const details: string[] = [];
      if (event.all_day && startDay !== endDay)
        details.push(`Through ${civilLabel(endDay)}`);
      if (event.repeat !== "none") {
        const every =
          event.repeat === "weekly"
            ? event.interval === 1
              ? "Every week"
              : `Every ${event.interval} weeks`
            : event.interval === 1
              ? "Every day"
              : `Every ${event.interval} days`;
        details.push(
          every + (event.until ? ` until ${civilLabel(event.until)}` : ""),
        );
      }
      if (event.location) details.push(event.location);
      details.push(zone);
      const category = event.meeting_type || event.kind;
      return {
        id: row.id,
        title: event.title,
        date: {
          datetime: event.starts_at,
          label: `${part("weekday")}, ${civilLabel(startDay)}`,
          weekday: part("weekday"),
          day: part("day"),
          month: part("month"),
          year: part("year"),
        },
        time,
        category:
          category === "study"
            ? "Work session"
            : category.charAt(0).toUpperCase() + category.slice(1),
        details,
        tone:
          event.kind === "study"
            ? "sage"
            : event.kind === "work"
              ? "blue"
              : ["exam", "deadline"].includes(event.kind)
                ? "rose"
                : "sand",
      };
    });
}
