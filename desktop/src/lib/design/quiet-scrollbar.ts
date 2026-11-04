/**
 * Reveal a scrollbar while the surface is actually being scrolled, then hide it
 * without changing the gutter.
 *
 * Diverges from the old desktop shell in one respect: the old list also revealed
 * on pointer entry and movement. Hover is not scrolling, so those are removed
 * here and the bar appears only for scroll, wheel and keyboard activity.
 *
 * The bar itself is the platform's, styled by the `::-webkit-scrollbar` and
 * `[data-quiet-scrollbar]` rules in `design/chat.css`. Drawing our own was tried
 * and reverted: it could control the length, but it also had to reimplement
 * placement and never looked like the rest of the desktop.
 */
export function quietScrollbar(node: HTMLElement) {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  node.dataset.quietScrollbar = "";

  function reveal() {
    clearTimeout(timeout);
    // Automatic following emits a scroll event each frame. Only change the
    // DOM on the visibility edge, not on every streamed movement.
    if (!("scrollbarVisible" in node.dataset))
      node.dataset.scrollbarVisible = "";
    timeout = setTimeout(() => {
      delete node.dataset.scrollbarVisible;
    }, 1000);
  }

  // Real scroll activity only.
  const events = ["scroll", "wheel", "keydown"] as const;
  for (const event of events)
    node.addEventListener(event, reveal, { passive: true });

  return {
    destroy() {
      clearTimeout(timeout);
      for (const event of events) node.removeEventListener(event, reveal);
      delete node.dataset.quietScrollbar;
      delete node.dataset.scrollbarVisible;
    },
  };
}
