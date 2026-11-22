import { writable, type Readable } from "svelte/store";
import {
  draftPassage,
  withPassage,
  type StudyPassage,
} from "$lib/features/chat/study-requests";

/**
 * The chat draft owns a marked PDF passage as its "Marked passage" card. A PDF
 * reader shows the card's passage and asks the draft owner to replace or clear
 * it; it keeps no mark of its own, so sending or removing the card unmarks it.
 */
export const MATERIAL_MARK = Symbol("material-mark");
export type MaterialMark = {
  passage: Readable<StudyPassage | null>;
  mark: (value: StudyPassage | null) => void;
};
export function draftMarking(
  read: () => string,
  write: (draft: string) => void,
  blocked: () => boolean,
  beforeMark: () => void = () => {},
) {
  const passage = writable<StudyPassage | null>(null);
  let key = "";
  return {
    context: {
      passage: { subscribe: passage.subscribe },
      mark(value) {
        if (blocked()) return;
        if (value) beforeMark();
        write(withPassage(read(), value));
      },
    } satisfies MaterialMark,
    /** Call with every draft change; readers repaint only when the card does. */
    sync(draft: string) {
      const next = draftPassage(draft);
      const nextKey = next ? JSON.stringify(next) : "";
      if (nextKey === key) return;
      key = nextKey;
      passage.set(next);
    },
  };
}
