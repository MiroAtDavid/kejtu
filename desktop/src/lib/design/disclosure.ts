import { PAGE_TIMING } from "./page-fade";

/**
 * A block that opens and closes in place, such as the conversation list under
 * its menu row: its height and opacity follow the menu's motion, so the rows
 * below glide instead of jumping. The action owns the element's `hidden`
 * state. While closing, the contents stay in place but inert until the motion
 * ends; a reversal mid-way continues from where it is. Reduced motion and
 * engines without Web Animations switch at once.
 */
export function disclosure(node: HTMLElement, open: boolean) {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let running: Animation | undefined;
  node.hidden = !open;

  function settle() {
    running = undefined;
    node.style.removeProperty("overflow");
    node.style.removeProperty("box-sizing");
    node.inert = false;
    node.hidden = !open;
  }

  return {
    update(next: boolean) {
      if (next === open) return;
      open = next;
      if (reducedMotion?.matches || !node.animate) {
        running?.cancel();
        settle();
        return;
      }
      // Start from what is on screen now: closed, open, or part-way.
      const wasHidden = node.hidden;
      const current = getComputedStyle(node);
      const from: Keyframe = wasHidden
        ? {}
        : {
            height: `${node.getBoundingClientRect().height}px`,
            paddingTop: current.paddingTop,
            paddingBottom: current.paddingBottom,
            marginTop: current.marginTop,
            marginBottom: current.marginBottom,
            opacity: current.opacity,
          };
      running?.cancel();
      running = undefined;
      node.hidden = false;
      node.inert = !open;
      node.style.boxSizing = "border-box";
      node.style.overflow = "hidden";
      const settled = getComputedStyle(node);
      // A flex or grid parent adds its gap only while the block is shown; a
      // matching negative margin keeps the closed block from taking space.
      const parent = node.parentElement
        ? getComputedStyle(node.parentElement)
        : null;
      const gap =
        parent && /flex|grid/.test(parent.display) ? parent.rowGap : "0px";
      const closed: Keyframe = {
        height: "0px",
        paddingTop: "0px",
        paddingBottom: "0px",
        marginTop: gap === "normal" ? "0px" : `-${gap}`,
        marginBottom: "0px",
        opacity: 0,
      };
      const opened: Keyframe = {
        height: `${node.getBoundingClientRect().height}px`,
        paddingTop: settled.paddingTop,
        paddingBottom: settled.paddingBottom,
        marginTop: settled.marginTop,
        marginBottom: settled.marginBottom,
        opacity: 1,
      };
      const start = wasHidden ? closed : from;
      const animation = node.animate(
        [start, open ? opened : closed],
        PAGE_TIMING,
      );
      running = animation;
      animation.onfinish = () => {
        if (running === animation) settle();
      };
    },
    destroy() {
      running?.cancel();
    },
  };
}
