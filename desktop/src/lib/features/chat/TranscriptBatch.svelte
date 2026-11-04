<script lang="ts">
  import { onMount, tick } from "svelte";

  export let scrollElement: HTMLElement | undefined;
  export let pinned = false;
  export let batchId: string;
  let node: HTMLDivElement;
  let awake = true;
  let near = true;
  let height = 0;
  let width = 0;
  let mounted = false;
  let destroyed = false;
  let resize: ResizeObserver | undefined;
  let intersection: IntersectionObserver | undefined;
  let interaction: MutationObserver | undefined;
  let sleepTimer: ReturnType<typeof setTimeout> | undefined;
  let observedRoot: HTMLElement | undefined;

  // The saved height includes child margins (flow-root). No estimates accumulate
  // as old batches leave the DOM, and the stream's newest batch never unmounts.
  function measure() {
    if (!awake || !node) return;
    const rect = node.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      width = rect.width;
      height = rect.height;
    }
  }

  function inUse() {
    if (node.contains(document.activeElement)) return true;
    if (
      node.querySelector(
        'details[open], dialog[open], [role="dialog"], form, .message-actions [aria-expanded="true"]',
      )
    )
      return true;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) {
      for (let i = 0; i < selection.rangeCount; i++) {
        if (selection.getRangeAt(i).intersectsNode(node)) return true;
      }
    }
    return false;
  }

  function reconcile() {
    if (!mounted || destroyed || !intersection) return;
    clearTimeout(sleepTimer);
    interaction?.disconnect();
    if (near || pinned) {
      awake = true;
      return;
    }
    if (!awake) return;
    measure();
    // Never replace an unmeasured/hidden batch with a zero-height placeholder.
    if (!height || !node.getBoundingClientRect().width) return;
    if (inUse()) {
      // Watch only an offscreen batch held open by interaction. Ordinary streams
      // do not pay for a subtree mutation observer on every token.
      interaction?.observe(node, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["open", "aria-expanded"],
      });
      return;
    }
    // Initial markdown, fonts and media can settle after the observer callback.
    // Measure after a quiet layout interval, not halfway through that first draw.
    sleepTimer = setTimeout(() => {
      if (destroyed || near || pinned || inUse()) return;
      measure();
      if (node.getBoundingClientRect().width > 0) awake = false;
    }, 120);
  }

  function observe(root: HTMLElement) {
    if (observedRoot === root) return;
    observedRoot = root;
    intersection?.disconnect();
    near = true;
    awake = true;
    intersection = new IntersectionObserver(
      (entries) => {
        near = entries[entries.length - 1]?.isIntersecting ?? true;
        reconcile();
      },
      { root, rootMargin: "1400px 0px" },
    );
    intersection.observe(node);
  }

  $: if (
    mounted &&
    scrollElement &&
    typeof IntersectionObserver !== "undefined"
  )
    observe(scrollElement);
  $: if (mounted) {
    pinned;
    reconcile();
  }

  onMount(() => {
    mounted = true;
    measure();
    if (typeof ResizeObserver !== "undefined") {
      resize = new ResizeObserver(() => {
        if (destroyed) return;
        const currentWidth = node.getBoundingClientRect().width;
        if (
          !awake &&
          currentWidth > 0 &&
          Math.abs(currentWidth - width) > 0.5
        ) {
          // A different column width invalidates the old line-wrap measurement.
          awake = true;
          void tick().then(() => {
            if (!destroyed) reconcile();
          });
        } else {
          reconcile();
        }
      });
      resize.observe(node);
    }
    interaction = new MutationObserver(reconcile);
    const afterInteraction = () => void tick().then(reconcile);
    node.addEventListener("focusout", afterInteraction);
    document.addEventListener("selectionchange", afterInteraction);
    return () => {
      destroyed = true;
      clearTimeout(sleepTimer);
      intersection?.disconnect();
      resize?.disconnect();
      interaction?.disconnect();
      node.removeEventListener("focusout", afterInteraction);
      document.removeEventListener("selectionchange", afterInteraction);
    };
  });
</script>

<div
  class="transcript-batch"
  data-history-batch={batchId}
  data-suspended={!awake ? "true" : undefined}
  aria-hidden={!awake ? "true" : undefined}
  style:height={!awake ? `${height}px` : undefined}
  bind:this={node}
>
  {#if awake}<slot />{/if}
</div>

<style>
  .transcript-batch {
    display: flow-root;
    /* Browser anchoring must not compete with Transcript's measured anchor. */
    overflow-anchor: none;
  }
</style>
