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
  let notice = "";
  let pendingFolder = "";
  let creating = false;
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
  function writable(saved: FilesStatus, path: string) {
    if (!saved.configured || !saved.enabled) return false;
    // Existing nested read-only grants still override a writable ancestor.
    const grants = [
      ...saved.folders,
      ...(saved.workspace ? [{ path: saved.workspace, access: "write" }] : []),
    ];
    const covering = grants.filter(
      (row) =>
        path === row.path || path.startsWith(row.path.replace(/\/$/, "") + "/"),
    );
    covering.sort((a, b) => b.path.length - a.path.length);
    return covering[0]?.access === "write";
  }
  async function share(path: string, saved: FilesStatus) {
    if (!writable(saved, path)) {
      // Preserve the other grants and tools. Only this explicit folder action
      // adds/upgrades a share; selecting an existing dropdown option does not.
      const folders = saved.folders.filter((row) => row.path !== path);
      if (saved.workspace !== path) folders.push({ path, access: "write" });
      saved = await request("files.save", {
        expected_revision: saved.revision,
        enabled: true,
        workspace: saved.workspace,
        create_workspace: false,
        folders,
        servers: saved.servers,
      });
    }
    if (alive) {
      status = saved;
      value = path;
      pendingFolder = "";
      notice = "Folder ready · Read & write";
    }
  }
  async function browse(create = false, retry = false) {
    if (disabled || picking || loading) return;
    picking = true;
    creating = create;
    error = "";
    notice = "";
    let chosen: string | null = null;
    try {
      // Read before opening the chooser. A concurrent settings change then
      // fails the existing revision check rather than being overwritten.
      const saved = await request("files.status", {});
      if (!alive) return;
      status = saved;
      if (!saved.runtime_available)
        throw new Error(
          "The built-in Files component is unavailable. Repair the Kejtu installation to share folders.",
        );
      const initial =
        value ||
        saved.locations.find((folder) => folder.available)?.path ||
        saved.documents ||
        saved.home ||
        "";
      chosen = retry
        ? pendingFolder
        : create
          ? await chooseFolder(initial, true)
          : await chooseFolder(initial);
      // A closed section must never grant access or receive a late picker result.
      if (!alive || !chosen) return;
      pendingFolder = chosen;
      await share(chosen, saved);
    } catch (caught) {
      if (!alive) return;
      // Reconcile a lost acknowledgement; never automatically repeat a save.
      if (chosen) {
        try {
          status = await request("files.status", {});
          if (alive && writable(status, chosen)) {
            value = chosen;
            pendingFolder = "";
            notice = "Folder ready · Read & write";
            return;
          }
        } catch {
          if (alive) status = null;
        }
      }
      if (alive)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (alive) {
        picking = false;
        creating = false;
      }
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
    onChange={(path) => {
      value = path || null;
      pendingFolder = "";
      notice = "";
      error = "";
    }}
  />
  {#if value}<p class="folder-path" title={value}>{value}</p>{/if}
  <div class="folder-actions">
    <button
      class="browse-folder"
      type="button"
      disabled={disabled || picking || loading}
      on:click={() => browse()}
      >{picking && !creating ? "Choosing folder…" : "Browse folder…"}</button
    >
    <button
      class="browse-folder"
      type="button"
      disabled={disabled || picking || loading}
      on:click={() => browse(true)}
      >{creating ? "Creating folder…" : "Create folder…"}</button
    >
  </div>
  <p class="folder-help" id="project-folder-help">
    {#if loading}Loading shared folders…
    {:else}Folders you browse to or create here are shared with Kejtu for
      reading and writing.
      {#if status?.configured && !status.enabled}This also turns shared file
        access back on for your saved folders.{/if}
    {/if}
  </p>
  {#if notice}<p class="folder-help" role="status">{notice}</p>{/if}
  {#if pendingFolder && !picking}
    <p class="folder-help">
      Folder selected: {pendingFolder}. Read/write access has not been
      confirmed.
    </p>
    <button
      class="browse-folder"
      type="button"
      disabled={disabled || picking || loading}
      on:click={() => browse(false, true)}>Enable read & write</button
    >
  {/if}
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
        on:input={() => {
          pendingFolder = "";
          notice = "";
          error = "";
        }}
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
    font-size: var(--ke-text-2xs);
    overflow-wrap: anywhere;
  }
  .folder-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .browse-folder {
    margin-top: 8px;
    min-height: 32px;
    padding: 7px 10px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: var(--ke-text-xs);
  }
  details {
    margin-top: 10px;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-2xs);
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
