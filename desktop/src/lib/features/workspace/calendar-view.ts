import type { Event } from "./workspace";

export type Occurrence = Event & {
  id: string;
  event_id: string;
  occurrence_at: string;
  revision: number;
};
export type Deadline = { id: string; title: string; due: string };

export function dayInZone(value: string | Date, zone: string): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
export function dayLabel(day: string): string {
  return new Date(day + "T12:00:00Z").toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
export function clockTime(value: string, zone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}
export function onDay(
  items: Occurrence[],
  day: string,
  zone: string,
): Occurrence[] {
  // End instants are exclusive, including all-day/midnight and DST boundaries.
  return items
    .filter((item) =>
      item.kind === "deadline"
        ? dayInZone(item.starts_at, zone) === day
        : dayInZone(item.starts_at, zone) <= day &&
          dayInZone(new Date(Date.parse(item.ends_at) - 1), zone) >= day,
    )
    .sort(
      (a, b) =>
        Number(b.all_day) - Number(a.all_day) ||
        Date.parse(a.starts_at) - Date.parse(b.starts_at) ||
        a.id.localeCompare(b.id),
    );
}
export function timeLabel(item: Occurrence, day: string, zone: string): string {
  if (item.all_day) return "All day";
  if (dayInZone(item.starts_at, zone) < day) return "Continues";
  return clockTime(item.starts_at, zone);
}
