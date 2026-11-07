<script lang="ts">
  import { onMount, tick } from "svelte";
  import {
    ACTIVITY_SIZE,
    measureActivity,
    type ActivityAnchor,
  } from "./document-activity";
  export let surface: HTMLElement;
  export let locate: () => ActivityAnchor | null;
  export let revision: unknown;
  export let label = "Kejtu is thinking…";
  export let onOpen: () => void;
  let point: { x: number; y: number } | null = null;
  let layer: HTMLDivElement;
  let mounted = false,
    frame = 0;
  function measure() {
    if (!mounted) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      if (mounted) point = measureActivity(locate(), layer, surface);
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
    observer.observe(layer);
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

<div class="document-activity-layer" bind:this={layer}>
  {#if point}<button
      class="document-activity"
      style:left={`${point.x}px`}
      style:top={`${point.y}px`}
      style:width={`${ACTIVITY_SIZE.width}px`}
      style:height={`${ACTIVITY_SIZE.height}px`}
      aria-label={label}
      title={`${label} Open Ask Kejtu`}
      on:click={onOpen}
    >
      <span></span><span></span><span></span>
    </button>{/if}
</div>

<style>
  .document-activity-layer {
    position: absolute;
    inset: 0;
    z-index: 3;
    pointer-events: none;
  }
  .document-activity {
    position: absolute;
    box-sizing: border-box;
    pointer-events: auto;
    display: flex;
    gap: 3px;
    align-items: center;
    padding: 3px 2px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-secondary);
    cursor: pointer;
    min-height: 0;
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
