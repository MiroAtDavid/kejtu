import type { Source } from "./workspace";

export type OpenDocument = {
  id: number;
  file: string;
  lookupFile?: string;
  kind: "reader" | "md" | "docx";
  label?: string;
  initialContent?: string;
  source?: Source;
  dirty: boolean;
};
export type DocumentLayout = {
  active: number | null;
  left: number | null;
  right: number | null;
};

export function reorderDocuments(
  ids: number[],
  id: number,
  target: number,
  after: boolean,
): number[] {
  if (id === target || !ids.includes(id) || !ids.includes(target)) return ids;
  const remaining = ids.filter((item) => item !== id);
  remaining.splice(remaining.indexOf(target) + Number(after), 0, id);
  return remaining;
}

export function placeDocument(
  layout: DocumentLayout,
  id: number,
  side: "left" | "right",
  ids: number[],
): DocumentLayout {
  if (!ids.includes(id)) return layout;
  if (layout.right === null) {
    const other =
      layout.active !== id ? layout.active : ids.find((item) => item !== id);
    if (other == null) return layout;
    return side === "left"
      ? { active: id, left: id, right: other }
      : { active: id, left: other, right: id };
  }
  const opposite = side === "left" ? "right" : "left";
  // Moving an already-visible document swaps the pair, never duplicates a
  // mounted editor or discards the document that occupied the target pane.
  return {
    ...layout,
    active: id,
    [side]: id,
    [opposite]: layout[opposite] === id ? layout[side] : layout[opposite],
  };
}

// Selecting a visible tab only changes focus. Selecting another replaces the
// focused pane, while every document session stays mounted in its keyed slot.
export function showDocument(
  layout: DocumentLayout,
  id: number,
): DocumentLayout {
  if (layout.right === null) return { active: id, left: id, right: null };
  if (id === layout.left || id === layout.right)
    return { ...layout, active: id };
  return layout.active === layout.right
    ? { ...layout, active: id, right: id }
    : { ...layout, active: id, left: id };
}
export function splitDocuments(
  layout: DocumentLayout,
  ids: number[],
): DocumentLayout {
  if (layout.right !== null)
    return { active: layout.active, left: layout.active, right: null };
  const other = ids.findLast((id) => id !== layout.active);
  return other === undefined
    ? layout
    : { ...layout, left: layout.active, right: other };
}
export function closeDocument(
  layout: DocumentLayout,
  id: number,
  ids: number[],
): DocumentLayout {
  const remaining = ids.filter((item) => item !== id);
  if (id !== layout.left && id !== layout.right) return layout;
  if (layout.right !== null) {
    const survivor = id === layout.left ? layout.right : layout.left;
    return { active: survivor, left: survivor, right: null };
  }
  const next =
    remaining[Math.min(ids.indexOf(id), remaining.length - 1)] ?? null;
  return { active: next, left: next, right: null };
}
