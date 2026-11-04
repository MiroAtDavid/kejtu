<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import { chooseFolder, request } from "$lib/api/client";
  import type { FilesStatus, SharedFolder } from "$lib/api/wire.generated";

  export let ownerBusy = false;
  export let busy = false;
  let saved: FilesStatus | null = null;
  let enabled = true;
  let workspaceMode: "create" | "existing" | "none" = "create";
  let workspace = "";
  let folders: SharedFolder[] = [];
  let servers: string[] = [];
  let editingWorkspace = false;
  let adding = false;
  let folderPath = "";
  let folderAccess: "read" | "write" = "read";
  let error = "";
  let notice = "";

  function receive(value: FilesStatus) {
    saved = value;
    enabled = value.configured ? value.enabled : true;
    workspace = value.workspace ?? value.suggested_workspace;
    workspaceMode = value.workspace
      ? "existing"
      : value.configured
        ? "none"
        : "create";
    editingWorkspace = false;
    folders = value.folders.map((row) => ({ ...row }));
    servers = [...value.servers];
  }
  async function action(run: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    notice = "";
    try {
      await run();
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "File settings could not be changed.";
    } finally {
      busy = false;
    }
  }
  async function refresh() {
    await action(async () => receive(await request("files.status", {})));
  }
  async function pick(target: "workspace" | "folder") {
    await action(async () => {
      const result = await chooseFolder(
        target === "workspace" ? workspace : folderPath || saved?.home || "",
      );
      if (!result) return;
      if (target === "workspace")
        workspace =
          workspaceMode === "create"
            ? `${result.replace(/\/$/, "")}/Kejtu`
            : result;
      else folderPath = result;
    });
  }
  function addFolder() {
    const path = folderPath.trim();
    if (!path) return;
    if (
      folders.some((row) => row.path === path) ||
      (workspaceMode !== "none" && path === workspace)
    ) {
      error = "This folder is already listed.";
      return;
    }
    folders = [...folders, { path, access: folderAccess }];
    folderPath = "";
    folderAccess = "read";
    adding = false;
    error = "";
  }
  async function save() {
    if (!saved || ownerBusy || busy) return;
    const revision = saved.revision;
    await action(async () => {
      try {
        receive(
          await request("files.save", {
            expected_revision: revision,
            enabled,
            workspace: workspaceMode === "none" ? null : workspace,
            create_workspace: workspaceMode === "create",
            folders,
            servers,
          }),
        );
        notice =
          "File access saved. Your existing files stayed where they were.";
      } catch (caught) {
        // Reconcile a lost acknowledgement before offering another permission save.
        try {
          saved = await request("files.status", {});
        } catch {
          saved = null;
        }
        throw caught;
      }
    });
  }
  async function disable() {
    if (!saved || busy) return;
    const revision = saved.revision;
    await action(async () => {
      try {
        // Revocation is immediate; keep unsaved folder edits for the next Save.
        saved = await request("files.disable", { expected_revision: revision });
        enabled = false;
        notice =
          "Shared file access is off. Your files have not been moved or deleted.";
      } catch (caught) {
        saved = null;
        throw caught;
      }
    });
  }
  function tool(id: string, checked: boolean) {
    servers = checked ? [...servers, id] : servers.filter((v) => v !== id);
  }
  async function toggleAccess() {
    if (!saved || busy) return;
    if (saved.configured && saved.enabled && enabled) await disable();
    else if (!ownerBusy) {
      enabled = !enabled;
      notice = "";
    }
  }
  $: hasChanges =
    !!saved &&
    (!saved.configured ||
      enabled !== saved.enabled ||
      workspaceMode === "create" ||
      (workspaceMode === "none" ? null : workspace) !== saved.workspace ||
      JSON.stringify(folders) !== JSON.stringify(saved.folders) ||
      JSON.stringify(servers) !== JSON.stringify(saved.servers));
  $: quickSetup =
    !!saved &&
    !saved.configured &&
    !editingWorkspace &&
    workspaceMode === "create" &&
    enabled &&
    !adding &&
    JSON.stringify(folders) === JSON.stringify(saved.folders) &&
    JSON.stringify(servers) === JSON.stringify(saved.servers);
  onMount(refresh);
</script>

<section class="files-panel" aria-label="Files settings">
  <header class="files-heading">
    <div>
      <h2 class="settings-title">Files</h2>
      <p class="panel-intro">
        A place to work, and the folders you choose to share.
      </p>
    </div>
    <button class="quiet" on:click={refresh} disabled={busy}>Refresh</button>
  </header>
  {#if error}<p class="feedback error" role="alert">{error}</p>{/if}
  {#if notice}<p class="feedback" role="status">{notice}</p>{/if}
  {#if saved}
    <div class="access-row">
      <h3>
        <label class="access-switch">
          <input
            type="checkbox"
            role="switch"
            aria-label="Shared file access"
            aria-describedby="access-hint"
            checked={enabled}
            disabled={busy ||
              (ownerBusy && !(saved.configured && saved.enabled && enabled))}
            on:change={toggleAccess}
          />
          <span>Shared file access</span>
        </label>
      </h3>
      <p id="access-hint" class="help">
        {#if enabled && !(saved.configured && saved.enabled)}Ready to turn on
          when you save.
        {:else if enabled}Available in chat and background tasks. Turn off to
          stop sharing immediately.
        {:else}Access is off. Your saved folder choices are kept.{/if}
      </p>
    </div>
    <p class="privacy-note">
      Using files with a remote AI model can send their contents to that
      provider.
    </p>
    {#if !saved.runtime_available}<p role="alert">
        The built-in Files component is missing. Repair the Kejtu installation
        to enable it.
      </p>{/if}
    {#if !saved.configured && saved.folders.length}<p class="legacy-note">
        Connected tools already use the folders below. Save to manage that
        access here.
      </p>{/if}
    <fieldset class="folder-settings" disabled={busy || ownerBusy}>
      <section class="folder-section" aria-labelledby="working-title">
        <div class="section-heading">
          <h3 id="working-title">
            Working folder <span class="optional">Optional</span>
          </h3>
          {#if !editingWorkspace}<button
              class="quiet"
              on:click={() => (editingWorkspace = true)}
              >{workspaceMode === "none" ? "Choose folder…" : "Change…"}</button
            >{/if}
        </div>
        <p class="section-description">
          Where Kejtu creates files. Existing files stay where they are.
        </p>
        {#if editingWorkspace}
          <label for="workspace-mode" class="sr-only"
            >Working folder choice</label
          >
          <select
            use:choiceSelect={workspaceMode}
            id="workspace-mode"
            bind:value={workspaceMode}
          >
            <option value="create">Create a Kejtu folder</option>
            <option value="existing">Use an existing folder</option>
            <option value="none">No working folder</option>
          </select>
          {#if workspaceMode !== "none"}
            <label for="workspace-path"
              >{workspaceMode === "create"
                ? "New folder location"
                : "Working folder"}</label
            >
            <div class="path-picker">
              <input
                id="workspace-path"
                bind:value={workspace}
                spellcheck="false"
              />
              <button on:click={() => pick("workspace")}
                >{workspaceMode === "create"
                  ? "Choose location…"
                  : "Choose folder…"}</button
              >
            </div>
            <p class="help">
              {workspaceMode === "create"
                ? "Choose a parent folder or edit the path. Created when you save, with read & write access."
                : "Read & write access. Changing this folder does not move existing files."}
            </p>
          {/if}
        {:else if workspaceMode !== "none"}
          <div class="workspace-location">
            <svg
              viewBox="0 0 24 24"
              width="22"
              height="22"
              fill="none"
              stroke="currentColor"
              stroke-width="1.25"
              aria-hidden="true"
              ><path
                d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z"
              /></svg
            >
            <span class="path">{workspace}</span><span class="permission"
              >Read & write</span
            >
          </div>
          {#if saved.locations.some((r) => r.path === workspace && !r.available)}<p
              class="help"
            >
              Currently unavailable — check the drive or choose another folder.
            </p>{/if}
          {#if quickSetup}
            <p class="help">
              Create this working folder with read &amp; write access. Use
              Change to choose another location.
            </p>
            <button
              disabled={busy || ownerBusy || !saved.runtime_available || adding}
              on:click={save}>Set up working folder</button
            >
          {/if}
        {:else}<p class="empty">No working folder selected.</p>{/if}
      </section>
      <section class="folder-section" aria-labelledby="shared-title">
        <div class="section-heading">
          <h3 id="shared-title">Shared folders</h3>
          {#if !adding}<button
              class="quiet"
              on:click={() => (adding = true)}
              disabled={folders.length >= 32}>Share a folder…</button
            >{/if}
        </div>
        <p class="section-description">
          Other folders Kejtu can use. You choose the access for each.
        </p>
        <div class="folder-list">
          {#each folders as folder, i}
            <div class="folder-row">
              <div class="folder-path">
                <strong
                  >{folder.path.split("/").filter(Boolean).at(-1) ||
                    folder.path}</strong
                ><span>{folder.path}</span>
                {#if saved.locations.some((r) => r.path === folder.path && !r.available)}<small
                    >Currently unavailable — check the drive or choose another
                    folder.</small
                  >{/if}
              </div>
              <div class="folder-controls">
                <select
                  use:choiceSelect={folder.access}
                  aria-label={`Access for ${folder.path}`}
                  bind:value={folder.access}
                >
                  <option value="read">Read only</option><option value="write"
                    >Read & write</option
                  >
                </select>
                <button
                  class="quiet"
                  aria-label={`Stop sharing ${folder.path}`}
                  on:click={() => (folders = folders.filter((_, n) => n !== i))}
                  >Remove</button
                >
              </div>
            </div>
          {:else}{#if !adding}<p class="empty">
                No additional folders shared.
              </p>{/if}{/each}
        </div>
        {#if adding}
          <div class="add-folder">
            <label for="shared-path">Folder to share</label>
            <div class="path-picker">
              <input
                id="shared-path"
                bind:value={folderPath}
                placeholder="Choose a folder or enter its full path"
                spellcheck="false"
              />
              <button on:click={() => pick("folder")}>Choose folder…</button>
            </div>
            <div class="shortcuts">
              {#if saved.documents}<button
                  class="quiet"
                  on:click={() => (folderPath = saved!.documents)}
                  >Documents</button
                >{/if}
              <button class="quiet" on:click={() => (folderPath = saved!.home)}
                >Home</button
              >
            </div>
            <div class="add-controls">
              <div>
                <label for="shared-access">Access</label><select
                  use:choiceSelect={folderAccess}
                  id="shared-access"
                  bind:value={folderAccess}
                >
                  <option value="read">Read only</option><option value="write"
                    >Read & write</option
                  >
                </select>
              </div>
              <button on:click={addFolder} disabled={!folderPath.trim()}
                >Add folder</button
              >
              <button class="quiet" on:click={() => (adding = false)}
                >Cancel</button
              >
            </div>
          </div>
        {/if}
        <details class="access-details">
          <summary>About folder access</summary>
          <p class="help">
            Read only allows opening and searching. Read & write also allows
            creating, editing and moving files.
          </p>
          <p class="help">
            Subfolders are included; a more specific folder choice overrides its
            parent. Removing a folder stops sharing it after Save and never
            deletes its files. Kejtu’s private app data stays excluded.
          </p>
        </details>
      </section>
      <details class="tools-details">
        <summary
          >Tools using these folders <span class="summary-note"
            >Files{servers.length ? ` + ${servers.length}` : ""}</span
          ></summary
        >
        <div class="tool-list">
          <div class="builtin">
            <strong>Files</strong><span
              >Included · find, read, write and organise files</span
            >
          </div>
          {#each saved.tools as row}
            <label class="check"
              ><input
                type="checkbox"
                checked={servers.includes(row.id)}
                on:change={(event) => tool(row.id, event.currentTarget.checked)}
              /><span
                ><strong>{row.id}</strong>{#if !row.enabled}<small
                    >Connection disabled</small
                  >{/if}<span class="tool-description">{row.description}</span
                ></span
              ></label
            >
          {/each}
        </div>
        <p class="help">
          Selected local tools use these folders with their existing tool
          permissions. Remote connections do not receive folder access.
        </p>
      </details>
    </fieldset>
    {#if saved.connection_read_folders.length}
      <details class="connection-folders">
        <summary>Separate connection access</summary>
        <p class="help">
          These read-only folders include tool programs and dependencies. They
          remain available when shared file access is off. Manage them in
          Connections → Advanced.
        </p>
        {#each saved.connection_read_folders as row}<p>
            <strong>{row.server}</strong><br /><span class="path"
              >{row.path}</span
            >
          </p>{/each}
      </details>
    {/if}
    {#if ownerBusy}<p class="help">
        Finish or stop the current reply before changing folders. You can still
        turn access off.
      </p>{/if}
    {#if !quickSetup}<footer class="save-row">
        <button
          disabled={busy ||
            ownerBusy ||
            !hasChanges ||
            (enabled && !saved.runtime_available) ||
            adding}
          on:click={save}>{busy ? "Working…" : "Save changes"}</button
        >
        <span class="help"
          >{adding
            ? "Add or cancel the folder above to save."
            : hasChanges
              ? "Changes take effect when saved."
              : "All changes saved."}</span
        >
      </footer>{/if}
  {:else if !error}<p role="status">Loading file settings…</p>{/if}
</section>

<style>
  .files-panel {
    max-width: 58rem;
  }
  .files-heading,
  .section-heading,
  .workspace-location,
  .folder-row,
  .folder-controls,
  .save-row,
  .add-controls {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .files-heading {
    align-items: flex-start;
    justify-content: space-between;
  }
  .files-heading > .quiet {
    flex: none;
    margin-top: 2px;
  }
  .files-panel .files-heading .panel-intro {
    margin-bottom: 24px;
  }
  .files-panel .quiet {
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .files-panel .access-row h3,
  .files-panel .section-heading h3 {
    margin: 0;
  }
  .files-panel .access-row .help {
    margin: 2px 0 0 50px;
  }
  .files-panel .privacy-note {
    color: var(--ke-text-secondary);
    font-size: 12px;
    margin: 12px 0 24px;
  }
  .files-panel .access-row .access-switch {
    font-size: 13px;
    font-weight: 500;
    color: var(--ke-text-primary);
    margin: 0;
  }
  .files-panel .folder-settings {
    margin: 0;
  }
  .folder-section {
    border-top: 1px solid var(--ke-border);
    padding: 22px 0;
  }
  .section-heading {
    justify-content: space-between;
    min-height: 32px;
  }
  .files-panel .section-description {
    color: var(--ke-text-secondary);
    margin: 3px 0 14px;
  }
  .optional,
  .permission,
  .summary-note {
    color: var(--ke-text-secondary);
    font-size: 11px;
    font-weight: 400;
  }
  .optional {
    margin-left: 8px;
  }
  .workspace-location {
    padding: 10px 0;
  }
  .workspace-location svg {
    color: var(--ke-text-secondary);
    flex: none;
  }
  .workspace-location .path {
    flex: 1;
  }
  .permission {
    white-space: nowrap;
  }
  .path,
  .folder-path {
    overflow-wrap: anywhere;
    min-width: 0;
  }
  .files-panel .empty {
    color: var(--ke-text-secondary);
    margin: 14px 0 8px;
  }
  .files-panel label:not(.check):not(.sr-only):not(.access-switch) {
    display: block;
    margin: 12px 0 6px;
  }
  .path-picker {
    display: flex;
    gap: 8px;
  }
  .path-picker input {
    flex: 1;
    min-width: 0;
  }
  .path-picker button {
    flex: none;
  }
  .folder-row {
    padding: 12px 0;
  }
  .folder-row + .folder-row {
    border-top: 1px solid var(--ke-border);
  }
  .folder-path {
    flex: 1;
  }
  .folder-path strong,
  .folder-path span,
  .folder-path small {
    display: block;
  }
  .folder-path span,
  .folder-path small {
    color: var(--ke-text-secondary);
    font-size: 12px;
    margin-top: 3px;
  }
  .folder-controls {
    gap: 8px;
    flex: none;
  }
  .add-folder {
    padding: 0 0 8px;
  }
  .shortcuts {
    display: flex;
    gap: 4px;
    margin-top: 4px;
  }
  .add-controls {
    align-items: flex-end;
    gap: 8px;
  }
  .add-controls > div {
    flex: 1;
  }
  .add-controls select {
    width: 100%;
  }
  .files-panel .access-details {
    margin: 12px 0 0;
  }
  .files-panel .access-details summary {
    padding: 4px 0;
  }
  .files-panel .tools-details {
    margin: 0;
    padding: 10px 0;
    border-top: 1px solid var(--ke-border);
  }
  .summary-note {
    margin-left: 10px;
  }
  .tool-list {
    margin: 12px 0;
  }
  .builtin {
    margin: 12px 0 12px 25px;
  }
  .builtin span,
  .tool-description {
    display: block;
    color: var(--ke-text-secondary);
    font-size: 12px;
    margin-top: 2px;
  }
  .check {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 12px 0;
  }
  .check input {
    flex: none;
    margin-top: 3px;
  }
  .check > span {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .check small {
    margin-left: 8px;
    color: var(--ke-text-secondary);
  }
  .files-panel .connection-folders {
    margin: 0;
    padding: 8px 0;
    border-top: 1px solid var(--ke-border);
  }
  .files-panel .save-row {
    border-top: 1px solid var(--ke-border);
    margin-top: 8px;
    padding: 20px 0;
    flex-wrap: wrap;
  }
  .files-panel .save-row > button:not(:disabled) {
    color: var(--ke-on-accent);
    background: var(--ke-accent);
  }
  .feedback {
    padding: 10px 0;
  }
  .error {
    color: var(--ke-danger);
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  @media (max-width: 640px) {
    .folder-row,
    .workspace-location {
      flex-wrap: wrap;
    }
    .folder-path {
      flex-basis: 100%;
    }
    .workspace-location .permission {
      margin-left: 38px;
    }
    .path-picker {
      flex-wrap: wrap;
    }
    .path-picker input {
      flex-basis: 100%;
    }
    .add-controls {
      flex-wrap: wrap;
    }
    .add-controls > div {
      flex-basis: 100%;
    }
  }
</style>
