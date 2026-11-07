/**
 * One soft highlight that slides to the current destination of a navigation
 * column. The current row keeps its own `aria-current`/class state; this only
 * draws the shared background so moving between destinations shows where you
 * went. It follows rows that appear, disappear, wrap or scroll, and it never
 * moves focus, changes layout or blocks input.
 */
export function navIndicator(node: HTMLElement, selector: string) {
  const marker = document.createElement("span");
  marker.className = "nav-indicator";
  marker.setAttribute("aria-hidden", "true");
  node.prepend(marker);
  node.classList.add("has-nav-indicator");
  if (getComputedStyle(node).position === "static")
    node.style.position = "relative";

  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const frame =
    window.requestAnimationFrame?.bind(window) ??
    ((callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 16));
  const cancelFrame =
    window.cancelAnimationFrame?.bind(window) ?? window.clearTimeout;
  let shown = false;
  let pending = 0;

  function place() {
    pending = 0;
    const target = node.querySelector<HTMLElement>(selector);
    // A hidden row (collapsed sidebar, compact layout) has no box to follow.
    if (!target || !target.getClientRects().length) {
      marker.style.opacity = "0";
      shown = false;
      return;
    }
    const host = node.getBoundingClientRect();
    const box = target.getBoundingClientRect();
    // Appearing (first paint, or after Home/Settings had no current row) jumps
    // into place and only fades in; moving between rows slides.
    const instant = !shown || reducedMotion?.matches;
    if (instant)
      marker.style.transition = reducedMotion?.matches
        ? "none"
        : "opacity var(--ke-motion-base) var(--ke-motion-ease)";
    marker.style.transform = `translate(${box.left - host.left + node.scrollLeft}px, ${box.top - host.top + node.scrollTop}px)`;
    marker.style.width = `${box.width}px`;
    marker.style.height = `${box.height}px`;
    marker.style.borderRadius = getComputedStyle(target).borderRadius;
    marker.style.opacity = "1";
    if (instant) {
      void marker.offsetWidth;
      marker.style.transition = "";
    }
    shown = true;
  }
  function schedule() {
    if (pending) return;
    pending = frame(place);
  }

  // Only state and structure changes matter; the marker's own style writes are
  // not observed, so placing it never schedules another placement.
  const mutations = new MutationObserver(schedule);
  mutations.observe(node, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["class", "aria-current", "hidden", "disabled"],
  });
  const sizes =
    typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
  sizes?.observe(node);
  window.addEventListener("resize", schedule);
  place();

  return {
    update(next: string) {
      selector = next;
      schedule();
    },
    destroy() {
      if (pending) cancelFrame(pending);
      mutations.disconnect();
      sizes?.disconnect();
      window.removeEventListener("resize", schedule);
      marker.remove();
      node.classList.remove("has-nav-indicator");
    },
  };
}
