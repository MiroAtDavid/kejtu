/** Presentation bounds only. Documents, drafts and edits retain their owners. */
export type DocumentDockBounds = {
  left: number;
  top: number;
  width: number;
  height: number;
};
export function documentDockWidth(available: number, fraction: number) {
  const width = Math.max(0, available);
  const minimum = Math.min(280, width / 2);
  return Math.min(width - minimum, Math.max(minimum, width * fraction));
}

/** Fit the existing reader to its dock without adding a second scroll surface. */
export function documentViewportHeight(
  node: HTMLElement,
  top = node.getBoundingClientRect().top,
) {
  const dock = node.closest<HTMLElement>(".workspace.docked");
  return Math.max(
    dock ? 0 : 260,
    (dock?.getBoundingClientRect().bottom ?? innerHeight) - top - 12,
  );
}
