/** Nonmodal detail drawer: keyboard entry/Escape and return to its trigger. */
export function drawerFocus(
  node: HTMLElement,
  options: { close: () => void; busy?: boolean },
) {
  const previous =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  let current = options;
  queueMicrotask(() => {
    if (node.isConnected)
      (
        node.querySelector<HTMLElement>(
          "input:not(:disabled),select:not(:disabled),button:not(:disabled)",
        ) || node
      ).focus({ preventScroll: true });
  });
  function key(event: KeyboardEvent) {
    // Disabling an async read button can move browser focus to <body>. Escape
    // still closes the visible drawer; retained hidden workspaces never react.
    if (
      event.key === "Escape" &&
      !event.defaultPrevented &&
      !current.busy &&
      node.getClientRects().length
    ) {
      event.preventDefault();
      event.stopPropagation();
      current.close();
    }
  }
  document.addEventListener("keydown", key);
  return {
    update(next: typeof options) {
      current = next;
    },
    destroy() {
      document.removeEventListener("keydown", key);
      if (
        previous?.isConnected &&
        (node.contains(document.activeElement) ||
          document.activeElement === document.body)
      )
        previous.focus({ preventScroll: true });
    },
  };
}
