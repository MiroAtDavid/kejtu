/** Fade only a changed destination; keep its controllers, focus and scroll owner. */
export function pageFade(node: HTMLElement, destination: string | null) {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let animation: Animation | undefined;
  function cancel() {
    animation?.cancel();
    animation = undefined;
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
      animation = node.animate([{ opacity: 0.45 }, { opacity: 1 }], {
        duration: 220,
        easing: "ease-out",
      });
    },
    destroy() {
      cancel();
      reducedMotion?.removeEventListener("change", cancel);
    },
  };
}
