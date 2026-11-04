<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import { chooseFolder, request } from "$lib/api/client";
  import type { FilesStatus } from "$lib/api/wire.generated";

  export let value: string | null = null;
  export let disabled = false;
  export let picking = false;
  let status: FilesStatus | null = null;
  let loading = true;
  let error = "";
  let alive = true;
  const name = (path: string) =>
    path.split(/[\\/]/).filter(Boolean).at(-1) || path;
  $: locations = status?.configured ? status.locations : [];
  $: options = [
    { value: "", label: "No folder" },
    ...locations.map((folder) => ({
      value: folder.path,
      label: name(folder.path),
      detail: `${folder.path} · ${!folder.available ? "Unavailable" : folder.access === "read" ? "Read only" : "Read & write"}`,
      disabled: !status?.enabled || !folder.available,
    })),
    // A chosen subfolder or a previously saved path must survive refreshes.
    ...(value && !locations.some((folder) => folder.path === value)
      ? [{ value, label: name(value), detail: value }]
      : []),
  ];
  async function refresh() {
    loading = true;
    error = "";
    try {
      const result = await request("files.status", {});
      if (alive) status = result;
    } catch (caught) {
      if (alive)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (alive) loading = false;
    }
  }
  async function browse() {
    if (disabled || picking) return;
    picking = true;
    error = "";
    try {
      const chosen = await chooseFolder(
        value || locations.find((folder) => folder.available)?.path || "",
      );
      // Closing/switching the section while the native dialog is open must
      // never write its late result into another project's draft.
      if (alive && chosen) value = chosen;
    } catch (caught) {
      if (alive)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (alive) picking = false;
    }
  }
  onMount(() => {
    void refresh();
  });
  onDestroy(() => {
    alive = false;
  });
</script>

<div class="project-folder">
  <ChoicePicker
    id="project-resources-folder"
    label="Resources folder"
    value={value || ""}
    {options}
    disabled={disabled || picking}
    describedBy="project-folder-help"
    onChange={(path) => (value = path || null)}
  />
  {#if value}<p class="folder-path" title={value}>{value}</p>{/if}
  <button
    class="browse-folder"
    type="button"
    disabled={disabled || picking}
    on:click={browse}
  >
    {picking ? "Choosing folder…" : "Browse or create a folder…"}
  </button>
  <p class="folder-help" id="project-folder-help">
    {#if loading}
      Loading shared folders…
    {:else if status && (!status.configured || !status.enabled || !locations.length)}
      Share a folder in Settings → Files to use it here.
    {:else}
      Choose a shared folder, or browse to select or create a subfolder inside
      it.
    {/if}
  </p>
  {#if error}
    <p class="error" role="alert">{error}</p>
    <button
      type="button"
      disabled={disabled || loading || picking}
      on:click={refresh}>Reload shared folders</button
    >
  {/if}
  <details>
    <summary>Enter a path</summary>
    <label
      >Folder path<input
        bind:value
        disabled={disabled || picking}
        placeholder="Path to a shared folder"
      /></label
    >
  </details>
</div>

<style>
  .project-folder {
    min-width: 0;
  }
  .folder-path,
  .folder-help {
    margin: 7px 0;
    color: var(--ke-text-secondary);
    font-size: 11px;
    overflow-wrap: anywhere;
  }
  .browse-folder {
    margin-top: 8px;
    min-height: 32px;
    padding: 7px 10px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: 12px;
  }
  details {
    margin-top: 10px;
    color: var(--ke-text-secondary);
    font-size: 11px;
  }
  summary {
    cursor: pointer;
  }
  label {
    display: grid;
    gap: 6px;
    margin-top: 8px;
  }
  input {
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }
</style>
