<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import ProjectFolderPicker from "./ProjectFolderPicker.svelte";
  import type { Course, RecordRow, Workspace } from "./workspace";

  export let workspace: Workspace;
  export let course: RecordRow<Course>;
  export let busy = false;
  export let active = true;
  let dialog: HTMLDialogElement;
  let choosing = false;
  let picking = false;
  let saving = false;
  let selected: string | null = null;
  let error = "";
  let pending: {
    course: RecordRow<Course>;
    resolve: (folder: string | null | undefined) => void;
  } | null = null;
  $: locked =
    busy || saving || picking || $workspace.busy || !!$workspace.pending;
  $: if (pending && (!active || pending.course.id !== course.id))
    finish(undefined);

  // null means an explicit save elsewhere; undefined cancels this save entirely.
  export async function choose(): Promise<string | null | undefined> {
    if (course.data.folder) return course.data.folder;
    if (pending || !active) return undefined;
    selected = null;
    choosing = false;
    error = "";
    const result = new Promise<string | null | undefined>((resolve) => {
      pending = { course, resolve };
    });
    await tick();
    if (pending) dialog.showModal();
    return result;
  }
  function finish(folder: string | null | undefined) {
    const current = pending;
    pending = null;
    dialog?.close();
    current?.resolve(folder);
  }
  async function useFolder() {
    if (!pending || !selected || locked) return;
    const current = pending;
    const folder = selected;
    saving = true;
    error = "";
    try {
      // Reuse the section owner: current revision, Files validation and uncertain
      // outcome recovery apply before this folder becomes the save suggestion.
      const receipt = await workspace.mutate("record.save", {
        kind: "course",
        id: current.course.id,
        expected_revision: current.course.revision,
        data: { ...current.course.data, folder },
      });
      if (pending !== current) return;
      if (receipt) finish(folder);
      else
        error =
          $workspace.error ||
          "The section folder could not be saved. Resolve any pending section change before trying again.";
    } catch (caught) {
      if (pending === current) error = String(caught);
    } finally {
      saving = false;
    }
  }
  function cancel(event: Event) {
    event.preventDefault();
    if (!saving && !picking) finish(undefined);
  }
  onDestroy(() => finish(undefined));
</script>

<dialog
  bind:this={dialog}
  on:cancel={cancel}
  aria-labelledby="project-save-title"
>
  {#if pending}
    <p class="eyebrow">{pending.course.data.title}</p>
    <h2 id="project-save-title">Give this section a folder?</h2>
    <p class="description">
      Keep its documents together, or save this copy somewhere else.
    </p>
    {#if choosing}
      <ProjectFolderPicker
        bind:value={selected}
        bind:picking
        disabled={locked}
      />
      <button
        class="primary"
        disabled={locked || !selected}
        on:click={useFolder}
      >
        {saving ? "Saving section folder…" : "Use folder and continue"}
      </button>
    {:else}
      <button
        class="primary"
        disabled={locked}
        on:click={() => (choosing = true)}
      >
        Choose or create a section folder…
      </button>
    {/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="actions">
      <button disabled={saving || picking} on:click={() => finish(null)}
        >Save elsewhere…</button
      >
      <button disabled={saving || picking} on:click={() => finish(undefined)}
        >Cancel</button
      >
    </div>
  {/if}
</dialog>

<style>
  dialog {
    width: min(460px, calc(100vw - 40px));
    max-height: calc(100vh - 48px);
    overflow: auto;
    box-sizing: border-box;
    border: 1px solid var(--ke-border);
    border-radius: 12px;
    padding: 26px;
    color: var(--ke-text-primary);
    background: var(--ke-surface);
    box-shadow: 0 16px 60px #0003;
  }
  dialog::backdrop {
    background: #0006;
  }
  .eyebrow {
    margin: 0 0 8px;
    color: var(--ke-text-secondary);
    font-size: 12px;
  }
  h2 {
    margin: 0;
    font-size: 23px;
    font-weight: 500;
  }
  .description {
    margin: 12px 0 24px;
    color: var(--ke-text-secondary);
    line-height: 1.6;
  }
  button {
    padding: 10px 13px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 3px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .primary {
    margin-top: 10px;
    width: 100%;
  }
  .actions {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-top: 20px;
  }
  .error {
    color: var(--ke-error, #bd4343);
    font-size: 13px;
    line-height: 1.5;
  }
</style>
