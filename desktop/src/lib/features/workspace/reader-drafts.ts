import { writable } from "svelte/store";
export const readerDrafts = writable(false);
/** Reader instances stay mounted across Study/Journal navigation. */
const readers = new Map<
  string,
  { check: () => Promise<boolean>; dirty: () => boolean }
>();
export function registerReader(
  id: string,
  value: { check: () => Promise<boolean>; dirty: () => boolean },
) {
  readers.set(id, value);
  return () => {
    readers.delete(id);
    refreshReaderDrafts();
  };
}
export function hasReaderDrafts() {
  return [...readers.values()].some((value) => value.dirty());
}
export async function confirmReaderClose() {
  for (const value of readers.values())
    if (!(await value.check())) return false;
  return true;
}

export function refreshReaderDrafts() {
  readerDrafts.set(hasReaderDrafts());
}
