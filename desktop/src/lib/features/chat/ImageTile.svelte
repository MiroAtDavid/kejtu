<script lang="ts">
  import { onMount } from "svelte";
  export let src = "";
  export let index = 0;
  export let compact = false;
  export let error = "";
  export let onOpen: (button: HTMLButtonElement) => void;
  export let onVisible: (visible: boolean) => void = () => {};
  let button: HTMLButtonElement;
  onMount(() => {
    if (typeof IntersectionObserver === "undefined") {
      onVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => onVisible(entry.isIntersecting),
      { rootMargin: "120px" },
    );
    observer.observe(button);
    return () => observer.disconnect();
  });
</script>

<button
  bind:this={button}
  type="button"
  class="thumbnail"
  class:compact
  aria-label={`Open image ${index + 1}`}
  on:click={() => onOpen(button)}
>
  {#if src}<img
      {src}
      draggable="false"
      loading="lazy"
      alt={compact ? `Attached image ${index + 1}` : `Image ${index + 1}`}
    />
  {:else}<span class="placeholder"
      >{error
        ? "Preview unavailable · open to retry"
        : `Image ${index + 1}`}</span
    >{/if}
  <span class="number" aria-hidden="true">{index + 1}</span>
</button>

<style>
  .thumbnail {
    position: relative;
    display: grid;
    place-items: center;
    width: fit-content;
    max-width: min(220px, 100%);
    padding: 4px;
    overflow: hidden;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-secondary);
    cursor: zoom-in;
  }
  .thumbnail.compact {
    width: 112px;
    height: 84px;
    aspect-ratio: auto;
    padding: 0.4rem;
  }
  img {
    display: block;
    width: auto;
    height: auto;
    max-width: min(210px, 100%);
    max-height: 170px;
    min-height: 0;
    object-fit: contain;
  }
  .compact img {
    width: 100%;
    height: 100%;
    max-width: 100%;
    max-height: 100%;
  }
  .number {
    position: absolute;
    left: 0.3rem;
    bottom: 0.25rem;
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    background: var(--ke-surface);
    font-size: var(--ke-text-xs);
  }
  .placeholder {
    font-size: var(--ke-text-xs);
    min-height: 48px;
  }
  .thumbnail:focus-visible {
    outline: 2px solid var(--ke-text-primary);
    outline-offset: 2px;
  }
</style>
