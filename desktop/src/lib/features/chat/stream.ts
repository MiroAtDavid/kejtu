import type { Results, Turn } from "$lib/api/wire.generated";

export type Stream = Results["turn.stream"];
/** One current-turn buffer; final body storage remains authoritative. */
export function appendPage(
  previous: Stream | null,
  page: Stream,
  turnId: string,
): Stream {
  const reset = !previous || previous.epoch !== page.epoch;
  const offset = reset ? 0 : previous.end;
  const bytes = new TextEncoder().encode(page.content).length;
  if (
    page.turn_id !== turnId ||
    (previous && page.conversation_id !== previous.conversation_id) ||
    page.reset !== reset ||
    page.offset !== offset ||
    page.end !== offset + bytes ||
    page.end > page.total_bytes ||
    page.caught_up !== (page.end === page.total_bytes) ||
    (!page.caught_up && !bytes) ||
    (!reset && page.revision < previous!.revision) ||
    (!reset && page.total_bytes < previous!.total_bytes)
  )
    throw new Error(
      "Answer progress changed identity or skipped text. Refresh to recover.",
    );
  return { ...page, content: (reset ? "" : previous!.content) + page.content };
}

export async function savedBody(
  stream: Stream,
  turn: Turn,
): Promise<Results["body.read"] | null> {
  if (
    stream.status === "running" ||
    !stream.caught_up ||
    !stream.body_id ||
    !stream.sha256
  )
    return null;
  const ref = turn.bodies.find(
    (body) => body.role !== "user" && body.body_id === stream.body_id,
  );
  if (
    !ref ||
    turn.id !== stream.turn_id ||
    turn.conversation_id !== stream.conversation_id
  )
    throw new Error("Saved answer identity differs from its progress.");
  const bytes = new TextEncoder().encode(stream.content);
  const digest = [
    ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  ]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  if (digest !== stream.sha256 || bytes.length !== stream.total_bytes)
    throw new Error(
      "Saved answer differs from its progress. Refresh to recover.",
    );
  return {
    body_id: stream.body_id,
    kind: ref.role === "partial" ? "partial_response" : "assistant_message",
    sha256: digest,
    offset: 0,
    end: bytes.length,
    total_bytes: bytes.length,
    content: stream.content,
    complete: true,
  };
}

export function phaseLabel(phase?: Stream["phase"]): string {
  if (phase === "searching") return "Searching the web…";
  if (phase === "connecting") return "Using a connected tool…";
  if (phase === "reading") return "Reading a page…";
  return phase === "thinking"
    ? "Thinking…"
    : phase === "tools"
      ? "Using tools…"
      : phase === "writing"
        ? "Writing…"
        : phase === "finishing"
          ? "Finishing…"
          : "Preparing a response…";
}
