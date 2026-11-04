/** A reading bookmark contains identities/offsets, never conversation text. */
export type ReadingPosition = {
  top: number;
  following: boolean;
  anchorId: string;
  anchorOffset: number;
  anchorSequence?: string;
  bodyEnds?: Record<string, number>;
};

export type ReadingBookmark = {
  conversationId: string;
  position: ReadingPosition;
};

const identity = /^[0-9a-f]{32}$/;
const key = (stateId: string) => `kejtu-next:reading:v1:${stateId}`;

export function readBookmark(stateId: string): ReadingBookmark | null {
  try {
    const value = JSON.parse(localStorage.getItem(key(stateId)) ?? "null");
    const p = value?.position;
    if (
      !identity.test(value?.conversationId ?? "") ||
      !p ||
      typeof p.following !== "boolean" ||
      !Number.isFinite(p.top) ||
      p.top < 0 ||
      !Number.isFinite(p.anchorOffset) ||
      !(p.anchorId === "" || identity.test(p.anchorId)) ||
      !(
        p.anchorSequence === undefined ||
        (typeof p.anchorSequence === "string" &&
          /^[1-9][0-9]{0,18}$/.test(p.anchorSequence))
      ) ||
      !(
        p.bodyEnds === undefined ||
        (p.bodyEnds &&
          typeof p.bodyEnds === "object" &&
          !Array.isArray(p.bodyEnds) &&
          Object.entries(p.bodyEnds).every(
            ([id, end]) =>
              identity.test(id) &&
              Number.isSafeInteger(end) &&
              (end as number) >= 0,
          ))
      )
    )
      return null;
    return {
      conversationId: value.conversationId,
      position: {
        top: p.top,
        following: p.following,
        anchorId: p.anchorId,
        anchorOffset: p.anchorOffset,
        anchorSequence: p.anchorSequence,
        bodyEnds: p.bodyEnds,
      },
    };
  } catch {
    return null;
  }
}

export function writeBookmark(stateId: string, value: ReadingBookmark | null) {
  if (!stateId) return;
  try {
    if (value) localStorage.setItem(key(stateId), JSON.stringify(value));
    else localStorage.removeItem(key(stateId));
  } catch {
    // Optional navigation memory must never prevent reading or sending.
  }
}
