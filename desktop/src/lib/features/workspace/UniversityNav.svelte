<script lang="ts">
  import type { Snapshot, View } from "./workspace";
  export let snapshot: Snapshot | null = null;
  export let onOpen: (view: View, id?: string) => void = () => {};
  export let disabled = false;
  export let embedded = false;
</script>

{#if snapshot?.settings.university_enabled}
  <nav class="university-nav" class:embedded aria-label="Projects">
    <button
      class="university-disclosure"
      {disabled}
      on:click={() => onOpen("overview")}
    >
      {embedded ? "Open projects calendar" : "Projects"}<span aria-hidden="true"
        >›</span
      >
    </button>
  </nav>
{/if}

<style>
  .university-nav {
    margin: 20px 0 26px;
    color: var(--ke-text-secondary);
  }
  .university-nav.embedded {
    margin: 0;
  }
  .university-disclosure {
    font: inherit;
    color: inherit;
    border: 0;
    background: none;
    cursor: pointer;
    text-align: left;
    padding: 5px 0;
    width: 100%;
    line-height: 1.6;
  }
  .university-disclosure {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: var(--ke-text-xs);
  }
  .university-disclosure span {
    color: var(--ke-text-tertiary);
  }
  button:hover:not(:disabled) {
    color: var(--ke-text-primary);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 3px;
  }
</style>
