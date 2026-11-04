<script lang="ts">
  import { onMount, tick } from "svelte";
  export let surface: HTMLElement;
  export let locate: () => { left: number; top: number; height: number } | null;
  export let revision: unknown;
  export let label = "Kejtu is thinking…";
  export let onOpen: () => void;
  let point: { x: number; y: number } | null = null;
  let mounted = false,
    frame = 0;
  function measure() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const rect = locate(),
        bounds = surface.getBoundingClientRect();
      point =
        rect &&
        rect.top >= bounds.top &&
        rect.top < bounds.bottom - 8 &&
        rect.left >= bounds.left &&
        rect.left < bounds.right
          ? {
              x: rect.left - bounds.left,
              y: Math.max(0, rect.top - bounds.top - 12),
            }
          : null;
    });
  }
  $: {
    revision;
    locate;
    if (mounted) void tick().then(measure);
  }
  onMount(() => {
    mounted = true;
    // Capture scroll from each inner pane; never pin an off-screen target to an
    // unrelated visible passage or steal the reader's scroll position.
    surface.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(surface);
    measure();
    return () => {
      mounted = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      surface.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  });
</script>

{#if point}<button
    class="document-activity"
    style:left={`${point.x}px`}
    style:top={`${point.y}px`}
    aria-label={label}
    title={`${label} Open Ask Kejtu`}
    on:click={onOpen}
  >
    <span></span><span></span><span></span>
  </button>{/if}

<style>
  .document-activity {
    position: absolute;
    z-index: 3;
    display: flex;
    gap: 3px;
    align-items: center;
    padding: 3px 2px;
    border: 0;
    border-radius: 4px;
    background: var(--ke-surface-raised);
    color: var(--ke-text-secondary);
    cursor: pointer;
    min-height: 0;
    width: 23px;
    height: 11px;
  }
  .document-activity span {
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: currentColor;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .document-activity span:nth-child(2) {
    animation-delay: 0.15s;
  }
  .document-activity span:nth-child(3) {
    animation-delay: 0.3s;
  }
  .document-activity:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
  @keyframes pulse {
    0%,
    70%,
    100% {
      opacity: 0.3;
    }
    35% {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .document-activity span {
      animation: none;
      opacity: 0.65;
    }
  }
</style>
