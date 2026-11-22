/**
 * An item's own page takes the place of a list that may have been scrolled far
 * down: start the new page at the top of whatever scrolls it, where every
 * collection page starts.
 */
export function toPageTop(node: HTMLElement | undefined) {
  if (!node) return;
  let scroller = node.parentElement;
  while (
    scroller &&
    !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)
  )
    scroller = scroller.parentElement;
  (scroller ?? node.ownerDocument.scrollingElement)?.scrollTo?.({ top: 0 });
}
