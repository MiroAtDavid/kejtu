<script lang="ts">
  import { onMount } from "svelte";
  import type { Results, Turn } from "$lib/api/wire.generated";
  import AnswerDetails from "./AnswerDetails.svelte";
  export let turn: Turn;
  export let section: "activity" | "context";
  export let readInspection:
    | ((id: string, cursor: string | null) => Promise<Results["turn.inspect"]>)
    | undefined = undefined;
  export let onClose: () => void;
  let dialog: HTMLDialogElement;
  onMount(() => {
    const previous = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  });
</script>

<dialog
  bind:this={dialog}
  aria-label="Reply information"
  on:cancel|preventDefault={onClose}
>
  <header>
    <div>
      <h2>Reply information</h2>
      {#if turn.created_at}<time datetime={turn.created_at}
          >{new Date(turn.created_at).toLocaleString()}</time
        >{/if}
    </div>
    <button
      type="button"
      class="close"
      aria-label="Close reply information"
      on:click={onClose}>×</button
    >
  </header>
  <AnswerDetails {turn} {readInspection} initialSection={section} />
</dialog>

<style>
  dialog {
    box-sizing: border-box;
    width: min(38rem, calc(100vw - 32px));
    max-height: calc(100dvh - 64px);
    padding: 22px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-overlay-radius);
    background: var(--ke-surface);
    color: var(--ke-text-primary);
    box-shadow: var(--ke-overlay-shadow);
    overflow: auto;
    animation: ke-dialog-in var(--ke-motion-slow) var(--ke-motion-ease);
  }
  dialog::backdrop {
    background: var(--ke-scrim, rgb(0 0 0 / 0.32));
    /* Older engines do not let ::backdrop inherit custom properties. */
    animation: ke-fade-in var(--ke-motion-slow, 260ms)
      var(--ke-motion-ease, ease-out);
  }
  header {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 24px;
  }
  h2 {
    margin: 0 0 6px;
    font: var(--ke-title-section);
    letter-spacing: var(--ke-title-section-tracking);
  }
  time {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .close {
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    width: 28px;
    height: 28px;
    font: 20px/1 sans-serif;
    cursor: pointer;
  }
  .close:hover {
    background: var(--ke-surface-inset);
  }
  .close:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
</style>
