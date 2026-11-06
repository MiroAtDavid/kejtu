/** Viewport coordinates supplied by the source, formatted or preview editor. */
export type ActivityAnchor = {
  left: number;
  top: number;
  height?: number;
  bottom?: number;
};
type Rect = { left: number; right: number; top: number; bottom: number };
/** The activity button's dimensions and separation from document text. */
export const ACTIVITY_SIZE = { width: 23, height: 11, gap: 6 };

/** Find clear same-line space; never move a clipped target onto another passage. */
export function activityPoint(
  target: Rect,
  bounds: Rect,
  occupied: Rect[],
  direction = "ltr",
): { x: number; y: number } | null {
  const { width, height, gap } = ACTIVITY_SIZE;
  if (
    target.top < bounds.top ||
    target.bottom > bounds.bottom ||
    target.left < bounds.left ||
    target.left >= bounds.right
  )
    return null;
  const y = (target.top + target.bottom - height) / 2;
  const line = occupied.filter(
    (r) =>
      r.top < y + height &&
      r.bottom > y &&
      r.left < bounds.right &&
      r.right > bounds.left,
  );
  const before = target.left - gap - width;
  const after = target.right + gap;
  const edges = [
    line.reduce((left, r) => Math.min(left, r.left), target.left) - gap - width,
    line.reduce((right, r) => Math.max(right, r.right), target.right) + gap,
  ].sort(
    (a, b) =>
      Math.abs(a + width / 2 - (target.left + target.right) / 2) -
      Math.abs(b + width / 2 - (target.left + target.right) / 2),
  );
  const candidates = direction === "rtl" ? [before, after] : [after, before];
  for (const x of [...candidates, ...edges]) {
    if (
      x < bounds.left ||
      x + width > bounds.right ||
      y < bounds.top ||
      y + height > bounds.bottom ||
      line.some((r) => r.left < x + width && r.right > x)
    )
      continue;
    return { x, y };
  }
  return null;
}

function intersect(a: Rect, b: Rect): Rect {
  return {
    left: Math.max(a.left, b.left),
    right: Math.min(a.right, b.right),
    top: Math.max(a.top, b.top),
    bottom: Math.min(a.bottom, b.bottom),
  };
}

/** Measure text without changing selections, editor contents, or scroll position. */
export function measureActivity(
  anchor: ActivityAnchor | null,
  layer: HTMLElement,
  surface: HTMLElement,
): { x: number; y: number } | null {
  if (!anchor) return null;
  const origin = layer.getBoundingClientRect();
  const height = anchor.height ?? (anchor.bottom ?? anchor.top) - anchor.top;
  if (
    anchor.left < origin.left ||
    anchor.left >= origin.right ||
    anchor.top < origin.top ||
    anchor.top >= origin.bottom ||
    height <= 0
  )
    return null;
  const caret = document.caretRangeFromPoint?.(
    anchor.left + 1,
    anchor.top + height / 2,
  );
  const node = caret?.startContainer;
  if (!caret || !node || !layer.parentElement?.contains(node)) return null;
  const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
  if (!(element instanceof HTMLElement)) return null;
  const pane = element.closest<HTMLElement>(
    ".cm-scroller, .mdxeditor-root-contenteditable, .preview",
  );
  if (!pane || !surface.contains(pane)) return null;
  const paneRect = pane.getBoundingClientRect();
  let bounds = intersect(origin, {
    left: paneRect.left + pane.clientLeft,
    right: paneRect.left + pane.clientLeft + pane.clientWidth,
    top: paneRect.top + pane.clientTop,
    bottom: paneRect.top + pane.clientTop + pane.clientHeight,
  });
  // A table cell or nested scroll area is not spare space in the next column.
  for (
    let parent: HTMLElement | null = element;
    parent && parent !== pane;
    parent = parent.parentElement
  ) {
    const style = getComputedStyle(parent);
    if (
      parent.matches("td,th") ||
      [style.overflowX, style.overflowY].some((value) =>
        ["auto", "scroll", "hidden", "clip"].includes(value),
      )
    )
      bounds = intersect(bounds, parent.getBoundingClientRect());
  }
  let target: Rect = {
    left: anchor.left,
    right: anchor.left,
    top: anchor.top,
    bottom: anchor.top + height,
  };
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent ?? "";
    let from = caret.startOffset;
    if (from === text.length || /\s/u.test(text[from])) from--;
    if (from >= 0 && !/\s/u.test(text[from])) {
      let to = from + 1;
      while (from > 0 && !/\s/u.test(text[from - 1])) from--;
      while (to < text.length && !/\s/u.test(text[to])) to++;
      const word = document.createRange();
      word.setStart(node, from);
      word.setEnd(node, to);
      const rect = Array.from(word.getClientRects()).find(
        (r) => r.top < target.bottom && r.bottom > target.top,
      );
      if (rect) target = rect;
    }
  }
  const block =
    element.closest<HTMLElement>(".cm-line,p,li,pre,td,th,h1,h2,h3,h4,h5,h6") ??
    element;
  const occupied: Rect[] = [];
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = walker.currentNode.textContent ?? "";
    const range = document.createRange();
    range.selectNodeContents(walker.currentNode);
    if (
      !Array.from(range.getClientRects()).some(
        (r) => r.top < target.bottom && r.bottom > target.top,
      )
    )
      continue;
    // Ordinary inter-word spaces cannot hold the button. Wider whitespace gaps
    // remain available, including indentation and deliberate spacing in Source.
    for (const run of text.matchAll(/\S+(?:[^\S\r\n]\S+)*/gu)) {
      range.setStart(walker.currentNode, run.index);
      range.setEnd(walker.currentNode, run.index + run[0].length);
      for (const rect of range.getClientRects()) occupied.push(rect);
    }
  }
  for (const object of block.querySelectorAll(
    "img,svg,canvas,video,button,input,textarea,select",
  ))
    occupied.push(object.getBoundingClientRect());
  for (const rail of surface.querySelectorAll(".mode-rail,.document-rail"))
    occupied.push(rail.getBoundingClientRect());
  const point = activityPoint(
    target,
    bounds,
    occupied,
    getComputedStyle(element).direction,
  );
  return point ? { x: point.x - origin.left, y: point.y - origin.top } : null;
}
