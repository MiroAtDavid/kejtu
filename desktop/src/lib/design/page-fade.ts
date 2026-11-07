/**
 * Page change: the destination fades in and its content rises a few pixels
 * while every page title stays exactly where it was, so the eye never has to
 * find the title again. Only a changed destination animates; controllers,
 * focus and the scroll owner are kept.
 */
const TITLES = "h1, .settings-title, .planning-title";
const RISE = 6;

export function pageFade(node: HTMLElement, destination: string | null) {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let running: Animation[] = [];
  function cancel() {
    for (const animation of running) animation.cancel();
    running = [];
  }
  reducedMotion?.addEventListener("change", cancel);

  return {
    update(next: string | null) {
      if (next === destination) return;
      destination = next;
      cancel();
      // Hidden surfaces and first mount stay quiet. Navigation never waits for
      // an outro, remounts children, or disables input while the fade runs.
      if (next === null || reducedMotion?.matches || !node.animate) return;
      const timing: KeyframeAnimationOptions = {
        duration: 260,
        easing: "cubic-bezier(0.2, 0, 0, 1)",
      };
      running.push(
        node.animate(
          [
            { opacity: 0, translate: `0 ${RISE}px` },
            { opacity: 1, translate: "0 0" },
          ],
          timing,
        ),
      );
      // Titles take the opposite offset, so only what is below them rises.
      for (const title of node.querySelectorAll<HTMLElement>(TITLES)) {
        if (!title.getClientRects().length || !title.animate) continue;
        running.push(
          title.animate(
            [{ translate: `0 ${-RISE}px` }, { translate: "0 0" }],
            timing,
          ),
        );
      }
    },
    destroy() {
      cancel();
      reducedMotion?.removeEventListener("change", cancel);
    },
  };
}
