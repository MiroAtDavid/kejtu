import type { Source } from "./workspace";

export type OpenDocument = {
  id: number;
  file: string;
  lookupFile?: string;
  kind: "reader" | "md" | "docx" | "code";
  label?: string;
  source?: Source;
  dirty: boolean;
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
