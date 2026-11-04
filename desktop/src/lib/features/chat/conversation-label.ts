/**
 * Timestamp fallback for a host without optional backend title metadata.
 * The normal native conversation list now supplies durable titles or first-
 * question labels. These helpers keep older fixtures and metadata-only hosts
 * readable without using transcript pagination to invent a name.
 */

function parseStamp(value: string): Date | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Visible, human-scaled creation label, e.g. `4 Oct 2026, 15:17`. */
export function visibleConversationLabel(createdAt: string): string {
  const date = parseStamp(createdAt);
  if (!date) return createdAt;
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

/**
 * Accessible name: the visible label plus the exact stored timestamp and the
 * full stored id. The id is what selection actually uses, so exposing it keeps
 * the control unambiguous for assistive technology and for deterministic checks.
 * `Conversation A`-style labels are not produced here — tests that need a stable
 * name pass their own fixture value, and the component must not depend on a
 * test-only label in product code.
 */
export function conversationAccessibleName(
  createdAt: string,
  conversationId: string,
): string {
  const exact = parseStamp(createdAt) ? createdAt : `${createdAt} (unparsed)`;
  return `${visibleConversationLabel(createdAt)} — created ${exact} — id ${conversationId}`;
}
