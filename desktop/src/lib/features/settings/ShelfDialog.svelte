<script lang="ts">
  import { onMount } from "svelte";

  /**
   * The Shelf's small window for adding something: Install from, Add skill,
   * Add connection. The fields keep the control look they have in Settings.
   * It opens over the page, focuses the field marked data-first, and gives
   * focus back to the button that opened it.
   */
  export let title: string;
  export let closeLabel: string;
  export let busy = false;
  export let onClose: () => void;
  let dialog: HTMLDialogElement;
  const heading = `shelf-dialog-${crypto.randomUUID()}`;

  onMount(() => {
    const trigger = document.activeElement;
    dialog.showModal();
    dialog.querySelector<HTMLElement>("[data-first]")?.focus();
    return () => {
      dialog.close();
      // WebKit does not reliably return focus when a dialog is unmounted.
      queueMicrotask(() => {
        if (trigger instanceof HTMLElement && trigger.isConnected)
          trigger.focus({ preventScroll: true });
      });
    };
  });
  function close() {
    if (!busy) onClose();
  }
</script>

<div class="settings-page">
  <dialog
    bind:this={dialog}
    aria-labelledby={heading}
    on:cancel|preventDefault={close}
  >
    <header>
      <h2 id={heading}>{title}</h2>
      <button disabled={busy} aria-label={closeLabel} on:click={close}>×</button
      >
    </header>
    <slot />
    <footer><slot name="footer" /></footer>
  </dialog>
</div>

<style>
  dialog {
    box-sizing: border-box;
    width: min(34rem, calc(100vw - 32px));
    max-height: calc(100dvh - 48px);
    padding: 24px;
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
    animation: ke-fade-in var(--ke-motion-slow, 260ms)
      var(--ke-motion-ease, ease-out);
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
  }
  h2 {
    margin: 0;
    font: var(--ke-title-section);
  }
  footer {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 24px;
  }
</style>
