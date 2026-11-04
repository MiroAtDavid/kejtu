<script lang="ts">
  import { onMount, tick } from "svelte";
  import { request, identity } from "$lib/api/client";
  import type { NotebookItem } from "$lib/api/wire.generated";
  import type { Workspace, Widget } from "./workspace";
  import { widgetDefinitions } from "./widgets";
  import "./widgets.css";
  export let workspace: Workspace;
  export let conversationId = "";
  export let side: Widget["side"] | undefined = undefined;
  export let selectedId = "";
  export let busy = false;
  export let onClose: () => void = () => {};
  // A modal owns one staged layout. Live refreshes never replace its draft.
  const revision = $workspace.snapshot!.layout_revision;
  const baseline = JSON.stringify($workspace.snapshot!.settings.widgets);
  let rows: Widget[] = JSON.parse(baseline);
  let dialog: HTMLDialogElement;
  let error = "",
    selecting = "",
    loading = false;
  let notes: NotebookItem[] = [],
    cursor: string | null = null;
  $: locked = busy || $workspace.busy || !!$workspace.pending;
  $: dirty = JSON.stringify(rows) !== baseline;
  $: stale = revision !== $workspace.snapshot?.layout_revision;
  onMount(() => {
    const trigger =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    dialog.showModal();
    if (selectedId) void focusRow(selectedId);
    return () => {
      dialog.close();
      // Unmounting a dialog does not reliably restore focus in WebKit.
      queueMicrotask(() => {
        if (trigger?.isConnected) trigger.focus({ preventScroll: true });
      });
    };
  });
  async function focusRow(id: string) {
    await tick();
    const row = dialog.querySelector<HTMLElement>(`[data-widget-id="${id}"]`);
    row?.scrollIntoView({ block: "nearest" });
    row?.querySelector<HTMLElement>("select")?.focus({ preventScroll: true });
  }
  function add(kind: Widget["kind"]) {
    const id = identity();
    rows = [
      ...rows,
      {
        id,
        kind,
        side: side || "right",
        size: "compact",
        collapsed: false,
        config:
          kind === "note"
            ? { note_id: null }
            : kind === "bookmarks"
              ? { links: [] }
              : {},
      },
    ];
    void focusRow(id);
  }
  function move(index: number, delta: number) {
    const next = [...rows];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    rows = next;
  }
  async function listNotes(id: string, more = false) {
    if (loading) return;
    selecting = id;
    loading = true;
    error = "";
    try {
      if (!conversationId)
        throw new Error("Open a conversation with notebook access first.");
      const result = await request("notes.page", {
        conversation_id: conversationId,
        collection: "current",
        cursor: more ? cursor : null,
      });
      notes = more ? [...notes, ...result.items] : result.items;
      cursor = result.next_cursor;
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }
  function close() {
    if (!$workspace.busy) onClose();
  }
  async function save() {
    if (locked || stale || !dirty) return;
    error = "";
    try {
      // Layout changes cannot grant model, calendar or player permissions.
      const result = await workspace.mutate("layout.save", {
        widgets: rows,
        expected_revision: revision,
      });
      if (result?.status === "saved") onClose();
      else
        error =
          $workspace.error ||
          result?.reason ||
          "The arrangement could not be saved. Your draft is still here.";
    } catch (caught) {
      error = String(caught);
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="journal-widget-editor"
  aria-labelledby="widget-editor-title"
  on:cancel|preventDefault={close}
>
  <header>
    <div>
      <p class="eyebrow">Your widgets</p>
      <h2 id="widget-editor-title">
        {selectedId ? "Edit widgets" : side ? "Add a widget" : "Edit widgets"}
      </h2>
    </div>
    <button
      type="button"
      class="widget-icon"
      aria-label="Close widget picker"
      disabled={$workspace.busy}
      on:click={close}>×</button
    >
  </header>
  <p class="widget-muted">
    Choose what stays in your Widgets panel, across conversations and projects.
  </p>
  <form on:submit|preventDefault={save}>
    <fieldset disabled={locked}>
      <div class="widget-picker">
        {#each Object.entries(widgetDefinitions) as [kind, definition]}
          <button
            type="button"
            disabled={rows.length >= 12}
            on:click={() => add(kind as Widget["kind"])}
          >
            <strong>＋ {definition.title}</strong><span
              >{definition.description}</span
            >
          </button>
        {/each}
      </div>
      {#if !rows.length}<p class="widget-muted">
          Your panel can stay empty. Add a widget whenever you need one.
        </p>{/if}
      <ol class="widget-editor-list">
        {#each rows as widget, index (widget.id)}
          <li data-widget-id={widget.id}>
            <div class="widget-edit-heading">
              <h3>{widgetDefinitions[widget.kind].title}</h3>
              <div>
                <button
                  type="button"
                  class="widget-link"
                  disabled={index === 0}
                  aria-label={`Move ${widgetDefinitions[widget.kind].title} up`}
                  on:click={() => move(index, -1)}>↑</button
                >
                <button
                  type="button"
                  class="widget-link"
                  disabled={index === rows.length - 1}
                  aria-label={`Move ${widgetDefinitions[widget.kind].title} down`}
                  on:click={() => move(index, 1)}>↓</button
                >
                <button
                  type="button"
                  class="widget-link"
                  aria-label={`Remove ${widgetDefinitions[widget.kind].title}`}
                  on:click={() =>
                    (rows = rows.filter((row) => row.id !== widget.id))}
                  >Remove</button
                >
              </div>
            </div>
            <div class="widget-edit-fields">
              <!-- Existing side values remain in saved layouts for compatibility. -->
              <label
                >Size<select bind:value={widget.size}
                  ><option value="compact">Compact</option><option
                    value="comfortable">Comfortable</option
                  ></select
                ></label
              >
            </div>
            <label class="widget-fold"
              ><input type="checkbox" bind:checked={widget.collapsed} /> Keep folded</label
            >
            {#if widget.kind === "note"}
              <p class="widget-muted">
                {widget.config.note_id
                  ? "An original note is pinned here."
                  : "Choose a notebook original to keep here."}
              </p>
              <button
                type="button"
                class="widget-link"
                disabled={loading}
                on:click={() => listNotes(widget.id)}>Choose a note</button
              >
              {#if selecting === widget.id}<div class="widget-note-options">
                  {#each notes as note}
                    <button
                      type="button"
                      class="widget-link"
                      aria-pressed={widget.config.note_id === note.note_id}
                      on:click={() => {
                        widget.config.note_id = note.note_id;
                        rows = rows;
                        selecting = "";
                      }}
                    >
                      {note.note?.title ||
                        note.preview.slice(0, 80) ||
                        "Untitled original"}
                    </button>
                  {:else}<p class="widget-muted">
                      {loading
                        ? "Reading notes…"
                        : "No accessible original notes on this page."}
                    </p>{/each}
                  {#if cursor}<button
                      type="button"
                      class="widget-link"
                      disabled={loading}
                      on:click={() => listNotes(widget.id, true)}
                      >More original notes</button
                    >{/if}
                </div>{/if}
            {:else if widget.kind === "bookmarks"}
              {#each widget.config.links || [] as link, i}<div
                  class="widget-bookmark-edit"
                >
                  <label
                    >Title<input
                      required
                      maxlength="160"
                      bind:value={link.title}
                    /></label
                  >
                  <label
                    >Web address<input
                      required
                      type="url"
                      maxlength="2048"
                      pattern="https?://.+"
                      placeholder="https://…"
                      bind:value={link.url}
                    /></label
                  >
                  <button
                    type="button"
                    class="widget-link"
                    aria-label={`Remove bookmark ${i + 1}`}
                    on:click={() => {
                      widget.config.links = widget.config.links!.filter(
                        (_, n) => i !== n,
                      );
                      rows = rows;
                    }}>Remove link</button
                  >
                </div>{/each}
              <button
                type="button"
                class="widget-link"
                disabled={(widget.config.links?.length || 0) >= 20}
                on:click={() => {
                  widget.config.links = [
                    ...(widget.config.links || []),
                    { title: "", url: "" },
                  ];
                  rows = rows;
                }}>＋ Add a bookmark</button
              >
            {/if}
          </li>
        {/each}
      </ol>
    </fieldset>
    {#if stale}<p class="widget-error" role="alert">
        The saved arrangement changed while this editor was open. Close and
        reopen it before saving.
      </p>{/if}
    {#if error}<p class="widget-error" role="alert">{error}</p>{/if}
    <footer class="widget-savebar">
      <span>{dirty ? "Unsaved arrangement" : "Saved arrangement"}</span>
      <div>
        <button type="button" disabled={$workspace.busy} on:click={close}
          >Cancel</button
        >
        <button disabled={locked || stale || !dirty}>Save arrangement</button>
      </div>
    </footer>
  </form>
</dialog>
