// Keep the file browser in its Material owner's lifetime while placing its DOM
// in the shared sidebar. Returning inline must not remount the reader or lose
// expanded folders when Focus, window width or the active course changes.
export function materialPortal(node: HTMLElement, target: HTMLElement | null) {
  const home = document.createComment("course material files");
  node.before(home);
  function place(destination: HTMLElement | null) {
    if (destination) destination.append(node);
    else home.after(node);
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
