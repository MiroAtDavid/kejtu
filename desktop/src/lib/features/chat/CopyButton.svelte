<script lang="ts">
  import { onDestroy } from "svelte";
  export let text = "";
  export let label = "Copy answer";
  let copied = false;
  let error = "";
  let timer: ReturnType<typeof setTimeout>;
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
      error = "";
      clearTimeout(timer);
      timer = setTimeout(() => (copied = false), 1600);
    } catch {
      error = "Could not copy. Select the text to copy it.";
    }
  }
  onDestroy(() => clearTimeout(timer));
</script>

<button
  class="copy"
  type="button"
  aria-label={copied ? "Copied" : label}
  title={copied ? "Copied" : label}
  disabled={!text}
  on:click={copy}
>
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    aria-hidden="true"
  >
    {#if copied}<path d="m5 12 4 4L19 6" />{:else}<rect
        x="8"
        y="8"
        width="12"
        height="12"
        rx="2"
      /><path d="M16 8V4H4v12h4" />{/if}
  </svg>
</button>
{#if error}<span class="error" role="status">{error}</span>{/if}

<style>
  .copy {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-tertiary);
    cursor: pointer;
  }
  .copy:hover {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .copy:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  .copy:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .error {
    font-size: var(--ke-text-xs);
    color: var(--ke-danger);
  }
</style>
