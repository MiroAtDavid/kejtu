import type {
  Arguments,
  ResearchCounts,
  ResearchSearch,
} from "$lib/api/wire.generated";
import { localZone, runLabel } from "../background/editor";
/** Compact display only; exact timestamps and schedule zones remain authoritative. */
export function researchTime(timestamp: string, zone = localZone()): string {
  try {
    const date = new Date(timestamp);
    return new Intl.DateTimeFormat(undefined, {
      timeZone: zone,
      day: "numeric",
      month: "short",
      year:
        date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  } catch {
    return timestamp;
  }
}
export type FindingView = NonNullable<Arguments["research.browse"]["view"]>;
export type ResearchTarget = {
  jobId?: string;
  view?: FindingView;
  token: number;
};
export const emptyCounts = (): ResearchCounts => ({
  total: 0,
  new: 0,
  changed: 0,
  reviewed: 0,
  saved: 0,
  dismissed: 0,
});
export const unread = (counts: ResearchCounts) => counts.new + counts.changed;
export function changes(counts: ResearchCounts) {
  return [
    counts.new ? `${counts.new} new` : "",
    counts.changed ? `${counts.changed} changed` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
export function searchStatus(item: ResearchSearch) {
  if (item.last_run && item.last_run.status !== "completed")
    return runLabel(item.last_run.status, item.last_run.error_code);
  if (!item.enabled)
    return item.new_report ? "Paused · Report ready" : "Paused";
  if (item.new_report) return "Report ready";
  return item.last_run ? "Up to date" : "Waiting for first check";
}
