<script lang="ts">
  /** Shown only above the guided-introduction conversation, while it runs. */
  export let busy = false;
  export let onComplete: () => void = () => {};
</script>

<p class="introduction">
  <span>Guided introduction</span>
  <button type="button" disabled={busy} on:click={onComplete}>I’m ready</button>
</p>

<style>
  .introduction {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    max-width: 48rem;
    margin: 0.75rem auto 1rem;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  button {
    font: inherit;
    padding: 0.35rem 0.6rem;
    border: 0;
    border-radius: var(--ke-radius-md);
    background: transparent;
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 3px;
    cursor: pointer;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
