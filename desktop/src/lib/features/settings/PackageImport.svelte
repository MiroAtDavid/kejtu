<script lang="ts">
  import ShelfDialog from "./ShelfDialog.svelte";

  export let directory = "";
  export let busy = false;
  export let error = "";
  export let doing = "";
  export let onPick: () => void;
  export let onInspect: () => void;
  export let onClose: () => void;
</script>

<ShelfDialog
  title="Install from…"
  closeLabel="Close install from"
  {busy}
  {onClose}
>
  <p class="hint">Choose a package folder on your computer.</p>
  <label for="package-import-folder">Package folder</label>
  <div class="folder-row">
    <input
      id="package-import-folder"
      data-first
      bind:value={directory}
      disabled={busy}
      spellcheck="false"
      placeholder="Path to a package folder"
    />
    <button disabled={busy} on:click={onPick}>Choose folder…</button>
  </div>
  <p class="hint formats">
    Agent Plugins, Claude or Codex plugins, unpacked MCPB bundles and single
    skill folders.
  </p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if busy}<p role="status" class="hint">{doing}</p>{/if}
  <svelte:fragment slot="footer">
    <button disabled={busy} on:click={() => !busy && onClose()}>Cancel</button>
    <button
      class="primary"
      disabled={busy || !directory.trim()}
      on:click={onInspect}
    >
      Preview package
    </button>
  </svelte:fragment>
</ShelfDialog>

<style>
  .hint {
    color: var(--ke-text-secondary);
  }
  label {
    display: block;
    margin: 20px 0 8px;
  }
  .folder-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  input {
    flex: 1 1 15rem;
    min-width: 0;
  }
  .formats {
    margin: 16px 0;
  }
  .error {
    color: var(--ke-danger);
  }
</style>
