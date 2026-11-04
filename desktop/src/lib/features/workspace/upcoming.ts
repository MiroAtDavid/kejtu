import { clockTime, dayInZone } from "./calendar-view";

export type Appointment = {
  id: string;
  recurrence_id: string | null;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  calendar_name: string;
};
export type Agenda = {
  events: Appointment[];
  timezone?: string;
  next_offset: number | null;
  incomplete?: boolean;
  errors?: { error: string }[];
};

export function agendaZone(
  preferred: string | undefined,
  fallback: string,
): string {
  for (const zone of [preferred, fallback, "UTC"]) {
    if (!zone) continue;
    try {
      new Intl.DateTimeFormat("en-GB", { timeZone: zone }).format(0);
      return zone;
    } catch {
      /* An unavailable zone must not break the widget. */
    }
  }
  return "UTC";
}
function civilLabel(day: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(new Date(day + "T12:00:00Z"));
}
export function appointmentDisplay(item: Appointment, zone: string) {
  try {
    // All-day values are civil dates. Never shift them into the device timezone.
    const day = item.all_day
      ? item.start.slice(0, 10)
      : dayInZone(item.start, zone);
    const label = civilLabel(day);
    return {
      day,
      label,
      time: item.all_day ? "All day" : clockTime(item.start, zone),
      detail: item.all_day
        ? ""
        : item.end && item.end !== item.start
          ? `Until ${dayInZone(item.end, zone) === day ? "" : civilLabel(dayInZone(item.end, zone)) + ", "}${clockTime(item.end, zone)}`
          : "",
    };
  } catch {
    return {
      day: "unknown",
      label: "Date unavailable",
      time: "—",
      detail: "",
    };
  }
}
