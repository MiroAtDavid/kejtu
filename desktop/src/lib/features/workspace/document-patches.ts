/** Edits refer to exact passages in the draft sent with this request. The model
 * chooses locations; the editor owns matching, concurrent-change checks and Undo. */
export type DocumentPatch = { old_text: string; new_text: string };
export type LocatedPatch = { from: number; to: number; insert: string };
export const normalizeLines = (text: string) => text.replace(/\r\n?/g, "\n");
export function locatePatches(
  snapshot: string,
  patches: DocumentPatch[],
): LocatedPatch[] {
  const text = normalizeLines(snapshot);
  if (!patches.length)
    throw new Error("The response contained no document changes.");
  const located = patches
    .map(({ old_text, new_text }) => {
      const old = normalizeLines(old_text),
        insert = normalizeLines(new_text);
      if (old.includes("\0") || insert.includes("\0"))
        throw new Error("The proposed edit contains binary text.");
      // Empty anchors are meaningful only in a genuinely empty draft. Insertions
      // elsewhere quote neighbouring text and include it in the replacement.
      if (!old && text)
        throw new Error(
          "Kejtu supplied an empty location for a non-empty document.",
        );
      const from = text.indexOf(old);
      if (from < 0)
        throw new Error(
          "A passage Kejtu wanted to change does not exactly match the draft sent with this request.",
        );
      if (old && text.indexOf(old, from + 1) >= 0)
        throw new Error(
          "A passage Kejtu wanted to change occurs more than once. Describe the section or give it a unique marker.",
        );
      return { from, to: from + old.length, insert };
    })
    .sort((a, b) => a.from - b.from);
  for (let i = 1; i < located.length; i++) {
    if (
      located[i].from < located[i - 1].to ||
      located[i].from === located[i - 1].from
    )
      throw new Error("The proposed changes overlap. Nothing was applied.");
  }
  return located;
}
