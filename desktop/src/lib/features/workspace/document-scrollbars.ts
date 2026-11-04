import { quietScrollbar } from "$lib/design/quiet-scrollbar";

/** Rich/source modes mount their own scrollers. Keep one quiet treatment through
 * mode changes, without replacing native scrolling, selection or keyboard input. */
export function documentScrollbars(node: HTMLElement) {
  const attached = new Map<HTMLElement, () => void>();
  function refresh() {
    for (const [element, cleanup] of attached) {
      if (!node.contains(element)) {
        cleanup();
        attached.delete(element);
      }
    }
    for (const element of node.querySelectorAll<HTMLElement>(
      ".cm-scroller, .mdxeditor-root-contenteditable, .preview",
    )) {
      if (attached.has(element)) continue;
      const quiet = quietScrollbar(element);
      attached.set(element, () => quiet.destroy());
    }
  }
  const observer = new MutationObserver(refresh);
  observer.observe(node, { childList: true, subtree: true });
  refresh();
  return {
    destroy() {
      observer.disconnect();
      for (const cleanup of attached.values()) cleanup();
      attached.clear();
    },
  };
}
