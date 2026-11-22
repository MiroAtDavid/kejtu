// Keep the file browser in its Material owner's lifetime while placing its DOM
// in the shared sidebar. Keep its expanded folders when switching modules or
// opening the conversation.
export function materialPortal(node: HTMLElement, target: HTMLElement | null) {
  const home = document.createComment("course material files");
  node.before(home);
  function place(destination: HTMLElement | null) {
    // Re-inserting a node that is already in place still removes it first,
    // which drops focus and WebKit scroll offsets. Only move it when needed.
    if (destination) {
      if (node.parentNode !== destination) destination.append(node);
    } else if (home.nextSibling !== node) home.after(node);
  }
  place(target);
  return {
    update: place,
    destroy() {
      node.remove();
      home.remove();
    },
  };
}
