<script lang="ts">
  export let visible = false;
  export let onLatest: () => void = () => {};
</script>

{#if visible}
  <button type="button" class="latest" on:click={onLatest}>
    <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true"
      ><path d="m4.5 7 4.5 4.5L13.5 7" /></svg
    >
    Latest
  </button>
{/if}

<style>
  .latest {
    display: inline-flex;
    align-items: center;
    gap: var(--ke-space-2);
    padding: var(--ke-space-2) var(--ke-space-3);
    border: 0;
    background: transparent;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }
  .latest:hover {
    color: var(--ke-text-primary);
  }
  svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.25;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
</style>
