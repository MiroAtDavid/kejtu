import type { Turn } from "$lib/api/wire.generated";
import type { VisibleBody } from "$lib/features/chat/images";
import {
  editSummary,
  replySummary,
  type DocumentReference,
  type DocumentReply,
  type DocumentSummary,
  type DocumentOutcome,
} from "./document-assist";

export type DocumentExchange = {
  turnId: string;
  at: string | null;
  request: DocumentSummary;
  reply: DocumentReply | null;
  complete: boolean;
  status: string;
  detail: string;
  outcome?: DocumentOutcome;
};
export type DocumentHistory = {
  conversationId: string;
  entries: DocumentExchange[];
  canEarlier: boolean;
  loading: boolean;
};
export function documentHistory(
  turns: Turn[],
  bodies: Record<string, VisibleBody>,
  outcomes: Record<string, DocumentOutcome> = {},
): DocumentExchange[] {
  return turns.flatMap((turn) => {
    const user = turn.bodies.find((ref) => ref.role === "user");
    const request = user
      ? editSummary(bodies[user.body_id]?.content ?? "")
      : null;
    if (!request) return [];
    const answer = turn.bodies.find(
      (ref) => ref.role === "answer" && ref.state === "ready",
    );
    const body = answer ? bodies[answer.body_id] : undefined;
    const reply = body?.complete ? replySummary(body.content) : null;
    return [
      {
        turnId: turn.id,
        at: turn.created_at,
        request,
        reply: reply?.request_id === request.request_id ? reply : null,
        complete: !!body?.complete,
        status: turn.status,
        detail: turn.detail ?? "",
        outcome: outcomes[request.request_id],
      },
    ];
  });
}
export function belongsToDocument(
  entry: DocumentExchange,
  ref: DocumentReference,
) {
  const document = entry.request.document;
  // Names alone never establish file identity; old requests remain in Journal details.
  return (
    !!document &&
    (document.session_id === ref.session_id ||
      (!!ref.file &&
        document.course_id === ref.course_id &&
        document.file === ref.file))
  );
}
export function outcomeLabel(entry: DocumentExchange): string {
  if (entry.status === "running") return "Working…";
  if (entry.status !== "completed")
    return entry.status === "cancelled" ? "Stopped" : "Couldn’t complete";
  if (entry.outcome === "applied") return "Applied to draft";
  if (entry.outcome === "undone") return "Undone";
  if (entry.outcome === "unchanged") return "No change";
  if (entry.outcome === "conflict") return "Not applied · review proposal";
  if (entry.reply?.action === "reply") return "Reply · document unchanged";
  // A generated answer is not evidence that it reached the editor or was saved.
  return entry.reply?.action === "edit"
    ? "Edit proposed"
    : entry.complete
      ? "Response unavailable"
      : "Answer not loaded";
}
export type DocumentSession = {
  key: string;
  head: string;
  ids: string[];
  name: string;
  count: number;
  reference?: DocumentReference;
  running: boolean;
  failed: boolean;
};
export function groupDocumentTurns<
  T extends { turn: Turn; day: string; document: DocumentSummary | null },
>(views: T[]): Record<string, DocumentSession> {
  const groups: Record<string, DocumentSession> = {};
  let previous = "",
    last: DocumentSession | null = null;
  for (const view of views) {
    const d = view.document;
    if (!d) {
      previous = "";
      last = null;
      continue;
    }
    const sequence = view.turn.sequence;
    const batch = /^[1-9]\d*$/.test(sequence)
      ? `${(BigInt(sequence) - 1n) / 20n}`
      : view.turn.id;
    const identity = d.document
      ? `${d.document.course_id}:${d.document.session_id}`
      : `legacy:${d.name}`;
    const key = `${batch}:${view.day}:${identity}`;
    if (!last || previous !== key)
      last = {
        key: view.turn.id,
        head: view.turn.id,
        ids: [],
        name: d.name,
        count: 0,
        reference: d.document,
        running: false,
        failed: false,
      };
    last.ids.push(view.turn.id);
    last.count++;
    last.name = d.name;
    last.running ||= view.turn.status === "running";
    last.failed ||= ["failed", "cancelled", "interrupted"].includes(
      view.turn.status,
    );
    // Use the latest known file name/reference after Save a copy within a session.
    if (d.document) last.reference = d.document;
    groups[view.turn.id] = last;
    previous = key;
  }
  return groups;
}
