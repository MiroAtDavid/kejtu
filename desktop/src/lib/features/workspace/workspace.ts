import { get, writable } from "svelte/store";
import { identity, NativeFailure, request } from "$lib/api/client";
import type { Arguments } from "$lib/api/wire.generated";

export type Source = {
  path: string;
  sha256: string;
  page: number;
  artifact_id: string;
};
export type Semester = {
  title: string;
  starts_on: string | null;
  ends_on: string | null;
  timezone: string;
  breaks: { starts_on: string; ends_on: string }[];
};
export type Course = {
  title: string;
  semester_id: string | null;
  folder: string | null;
};
export type Event = {
  title: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  kind: "class" | "study" | "work" | "appointment" | "exam" | "deadline";
  repeat: "none" | "daily" | "weekly";
  interval: number;
  until: string | null;
  course_id: string | null;
  task_id: string | null;
  location: string;
  meeting_type: string;
  all_day: boolean;
  material: Source[];
};
export type RecordRow<T = Record<string, unknown>> = {
  id: string;
  kind: string;
  revision: number;
  cancelled: boolean;
  data: T;
  created_at: string;
  updated_at: string;
};
export type Widget = {
  id: string;
  kind: "radio" | "note" | "calendar" | "bookmarks" | "university" | "tasks";
  side: "left" | "right";
  size: "compact" | "comfortable";
  collapsed: boolean;
  config: { note_id?: string | null; links?: { title: string; url: string }[] };
};
export type Availability = {
  timezone: string;
  buffer_minutes: number;
  windows: { weekday: number; start: string; end: string }[];
};
export type Settings = {
  university_enabled: boolean;
  model_read: boolean;
  model_write: boolean;
  radio_enabled: boolean;
  video_enabled: boolean;
  calendar_enabled: boolean;
  calendars: string[];
  widgets: Widget[];
  availability: Availability;
};
export type Snapshot = {
  revision: number;
  layout_revision: number;
  settings: Settings;
  semesters: RecordRow<Semester>[];
  courses: RecordRow<Course>[];
};
export type Receipt = {
  status: string;
  id?: string;
  revision?: number;
  mutation_key?: string;
  warnings?: string[];
  replayed?: boolean;
  reason?: string;
};
export type View =
  | "home"
  | "journal"
  | "overview"
  | "course"
  | "semesters"
  | "tasks"
  | "research"
  | "media"
  | "workspace-settings";
export type WorkspaceAction = Arguments["workspace"]["action"];

/** Native validates the finite action/field contract again before any effect. */
export async function workspaceRequest<T>(
  action: WorkspaceAction,
  payload: object = {},
): Promise<T> {
  const response = await request("workspace", {
    action,
    payload_json: JSON.stringify(payload),
  });
  const value: unknown = JSON.parse(response.result_json);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid workspace response.");
  return value as T;
}

type Pending = {
  action: "record.save" | "settings.save" | "layout.save" | "media.command";
  payload: Record<string, unknown>;
  key: string;
};
type State = {
  snapshot: Snapshot | null;
  pending: Pending | null;
  busy: boolean;
  error: string;
  notice: string;
  noticeTransient: boolean;
};

/** Media intents belong to this durable store, not the page being viewed.
 * Browsing may continue; mutate still blocks every new effect until recovery.
 */
export function workspaceNavigationLocked(
  state: Pick<State, "busy" | "pending">,
): boolean {
  return (
    (state.busy || !!state.pending) && state.pending?.action !== "media.command"
  );
}

/** One in-flight intent for this archive, persisted before crossing the bridge.
 * Reload/recovery only looks up outcomes. It never resubmits an uncertain effect.
 */
export function createWorkspace() {
  const state = writable<State>({
    snapshot: null,
    pending: null,
    busy: false,
    error: "",
    notice: "",
    noticeTransient: false,
  });
  let archive = "";
  const key = () => `kejtu-next.workspace.pending.v1.${archive}`;
  let noticeTimer: ReturnType<typeof setTimeout> | undefined;
  const update = (value: Partial<State>) => {
    if ("notice" in value) {
      clearTimeout(noticeTimer);
      value = { ...value, noticeTransient: value.noticeTransient ?? false };
      // Only confirmed, routine outcomes expire. Recovery instructions and
      // warnings stay visible; clearing a notice never clears an error/intent.
      if (value.notice && value.noticeTransient)
        noticeTimer = setTimeout(() => update({ notice: "" }), 6000);
    }
    state.update((s) => ({ ...s, ...value }));
  };
  async function refresh() {
    update({ snapshot: await workspaceRequest<Snapshot>("snapshot") });
  }
  async function resolve() {
    const pending = get(state).pending;
    if (!pending || get(state).busy) return;
    update({ busy: true, error: "" });
    try {
      const receipt = await workspaceRequest<Receipt>("operation.lookup", {
        mutation_key: pending.key,
      });
      if (receipt.status === "absent") {
        update({
          notice:
            "No saved outcome was found. Your draft is retained. Clear this pending request before making a new explicit change.",
        });
      } else {
        if (receipt.mutation_key && receipt.mutation_key !== pending.key)
          throw new Error("The saved outcome belongs to another request.");
        localStorage.removeItem(key());
        update({
          pending: null,
          notice:
            receipt.status === "unknown"
              ? "The player command may have run. Observe status before another command."
              : receipt.warnings?.join(" ") ||
                "Saved outcome recovered; nothing was repeated.",
          noticeTransient:
            receipt.status !== "unknown" && !receipt.warnings?.length,
        });
        await refresh();
      }
    } catch (error) {
      update({ error: String(error) });
    } finally {
      update({ busy: false });
    }
  }
  async function mutate<T extends Receipt>(
    action: Pending["action"],
    payload: Record<string, unknown>,
  ): Promise<T | null> {
    if (!archive || get(state).busy || get(state).pending) return null;
    const id = identity();
    const pending: Pending = {
      action,
      payload: {
        ...payload,
        [action === "media.command" ? "intent_key" : "mutation_key"]: id,
      },
      key: action === "media.command" ? `media:${id}` : id,
    };
    // If local storage is unavailable, fail before the native effect.
    localStorage.setItem(key(), JSON.stringify(pending));
    update({ pending, busy: true, error: "", notice: "" });
    try {
      const result = await workspaceRequest<T>(action, pending.payload);
      if (result.mutation_key && result.mutation_key !== pending.key)
        throw new Error("Workspace outcome identity mismatch.");
      localStorage.removeItem(key());
      update({ pending: null, notice: result.warnings?.join(" ") || "" });
      try {
        await refresh();
      } catch (error) {
        update({
          error:
            "The change is saved, but refreshing this view failed: " +
            String(error),
        });
      }
      return result;
    } catch (error) {
      // Planner validation rejects a record before writing it. Only this
      // confirmed refusal releases the draft; lost or invalid replies still
      // require receipt lookup before another effect can be submitted.
      if (
        action === "record.save" &&
        error instanceof NativeFailure &&
        error.code === "planner_arguments"
      ) {
        localStorage.removeItem(key());
        update({ pending: null });
      }
      update({ error: String(error) });
      return null;
    } finally {
      update({ busy: false });
    }
  }
  return {
    subscribe: state.subscribe,
    refresh,
    mutate,
    resolve,
    async open(stateId: string) {
      archive = stateId;
      try {
        const raw = localStorage.getItem(key());
        if (raw) {
          const saved = JSON.parse(raw) as Pending;
          if (
            ![
              "record.save",
              "settings.save",
              "layout.save",
              "media.command",
            ].includes(saved.action) ||
            typeof saved.key !== "string" ||
            !saved.payload ||
            typeof saved.payload !== "object"
          )
            throw new Error(
              "The retained workspace request is malformed; it was not replayed.",
            );
          update({ pending: saved });
          await resolve();
        }
        await refresh();
      } catch (error) {
        update({ error: String(error) });
      }
    },
    async clearPending() {
      if (get(state).busy) return;
      const pending = get(state).pending;
      if (!pending) return;
      update({ busy: true, error: "" });
      let recover = false;
      try {
        const result = await workspaceRequest<Receipt>("operation.lookup", {
          mutation_key: pending.key,
        });
        if (result.status !== "absent") recover = true;
        else {
          localStorage.removeItem(key());
          update({
            pending: null,
            error: "",
            notice:
              "No saved effect; request cleared. Your form draft remains.",
            noticeTransient: true,
          });
        }
      } catch (error) {
        // Failed lookup keeps the intent: dismissal cannot authorize a replay.
        update({ error: String(error) });
      } finally {
        update({ busy: false });
      }
      if (recover) await resolve();
    },
    clearNotice() {
      update({ notice: "" });
    },
  };
}
export type Workspace = ReturnType<typeof createWorkspace>;

export function localDate(value = new Date()): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

/** Civil input requires an explicit zone/offset. The backend owns DST validation. */
export function civilInstant(
  value: string,
  zone: string,
  fold: "earlier" | "later" = "earlier",
): string {
  const desired = new Date(`${value}:00Z`);
  if (!Number.isFinite(desired.getTime()))
    throw new Error("Choose a valid date and time.");
  const formatter = new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const candidates: Date[] = [];
  for (let minutes = -840; minutes <= 840; minutes += 15) {
    const candidate = new Date(desired.getTime() + minutes * 60000);
    if (formatter.format(candidate).replace(" ", "T") === value)
      candidates.push(candidate);
  }
  if (!candidates.length)
    throw new Error(
      "That local time does not exist in this timezone (daylight-saving change).",
    );
  const candidate = candidates[fold === "later" ? candidates.length - 1 : 0];
  const offset = Math.round((desired.getTime() - candidate.getTime()) / 60000);
  const absolute = Math.abs(offset);
  return `${value}:00${offset < 0 ? "-" : "+"}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
}

export function civilValue(value: string, zone: string): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  })
    .format(new Date(value))
    .replace(" ", "T");
}
