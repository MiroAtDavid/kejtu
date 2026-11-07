<script lang="ts">
  import { onMount } from "svelte";
  import { openWebLink } from "$lib/api/web-links";
  import {
    workspaceRequest,
    type Workspace,
    type Widget,
    type View,
    type Receipt,
  } from "./workspace";
  import type { Results } from "$lib/api/wire.generated";
  import Radio from "./Radio.svelte";
  import Upcoming from "./Upcoming.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import type { Agenda } from "./upcoming";
  import UniversityNav from "./UniversityNav.svelte";
  import { widgetDefinitions } from "./widgets";
  import "./widgets.css";
  import NoteOriginal from "./NoteOriginal.svelte";
  export let workspace: Workspace;
  export let widget: Widget;
  export let conversationId = "";
  export let busy = false;
  export let active = true;
  export let onConfigure: (id: string) => void = () => {};
  export let onOpen: (view: View, id?: string) => void = () => {};
  type Note = Results["note.read"] & { view_token: string };
  type Value = {
    status: string;
    kind: string;
    reason?: string;
    data?: unknown;
  };
  let value: Value | null = null,
    error = "",
    openNote = false,
    seenConversation = "",
    generation = 0;
  let note: Note | null = null,
    radio: (Receipt & { data?: { state?: string } }) | null = null;
  let agenda: Agenda | null = null;
  let taskItems: { id: string; title: string; due: string | null }[] = [];
  let seenRevision = "";
  $: if (
    $workspace.snapshot &&
    seenRevision !==
      `${active}:${$workspace.snapshot.revision}:${$workspace.snapshot.layout_revision}`
  ) {
    seenRevision = `${active}:${$workspace.snapshot.revision}:${$workspace.snapshot.layout_revision}`;
    if (active) void refresh();
  }
  $: if (conversationId !== seenConversation) {
    seenConversation = conversationId;
    openNote = false;
    note = null;
    if (widget.kind === "note") void refresh();
  }
  async function refresh(force = false, offset = 0) {
    const token = ++generation,
      cid = conversationId;
    error = "";
    try {
      const result = await workspaceRequest<Value>("widget.read", {
        id: widget.id,
        conversation_id: cid || undefined,
        offset,
        refresh: force,
      });
      if (token !== generation || cid !== conversationId) return;
      value = result;
      if (result.status === "ready" || result.status === "partial") {
        if (widget.kind === "note") note = result.data as Note;
        else if (widget.kind === "radio") radio = result.data as typeof radio;
        else if (widget.kind === "calendar") {
          const data = result.data as Agenda;
          agenda =
            offset && agenda
              ? { ...data, events: [...agenda.events, ...data.events] }
              : data;
        } else if (widget.kind === "tasks")
          taskItems = (result.data as { items: typeof taskItems }).items;
      } else {
        note = null;
        agenda = null;
        radio = null;
        taskItems = [];
      }
    } catch (caught) {
      if (token === generation) error = String(caught);
    }
  }
  onMount(() => {
    const timer =
      widget.kind === "radio" || widget.kind === "calendar"
        ? setInterval(
            () => {
              if (active && !widget.collapsed && !document.hidden)
                void refresh();
            },
            widget.kind === "radio" ? 4000 : 60000,
          )
        : null;
    return () => {
      generation++;
      if (timer) clearInterval(timer);
    };
  });
</script>

<article
  class="journal-widget"
  data-size={widget.size}
  aria-label={`${widgetDefinitions[widget.kind].title} widget`}
>
  <header class="widget-heading">
    <h3>
      {widgetDefinitions[widget.kind].title}{widget.collapsed
        ? " · folded"
        : ""}
    </h3>
    <div class="widget-heading-actions">
      {#if widget.kind === "calendar" && !widget.collapsed}<button
          class="widget-icon calendar-refresh"
          aria-label="Refresh appointments"
          title="Refresh appointments"
          on:click={() => refresh(true)}
          ><Icon name="refresh" size={13} /></button
        >{/if}
      <button
        class="widget-icon"
        aria-label={`Configure ${widgetDefinitions[widget.kind].title}`}
        disabled={busy}
        on:click={() => onConfigure(widget.id)}>···</button
      >
    </div>
  </header>
  <div hidden={widget.collapsed}>
    {#if widget.kind === "bookmarks"}{#each widget.config.links || [] as link}<button
          class="widget-link bookmark"
          on:click={() =>
            openWebLink(link.url).catch((caught) => (error = String(caught)))}
          >{link.title} ↗</button
        >{:else}<p class="widget-muted">
          Add a bookmark with the ··· menu.
        </p>{/each}
    {:else if widget.kind === "radio" && value?.status === "ready"}<Radio
        {workspace}
        {busy}
        compact
        comfortable={widget.size === "comfortable"}
        observed={radio}
      />
    {:else if widget.kind === "note" && note}<h3>
        {note.note?.title || "Original note"}
      </h3>
      <p class="preview">{note.content}</p>
      <button class="widget-link" on:click={() => (openNote = true)}
        >Read complete original &amp; sources</button
      >
    {:else if widget.kind === "calendar" && agenda}
      <Upcoming
        {agenda}
        timezone={$workspace.snapshot?.settings.availability.timezone || "UTC"}
        onMore={() =>
          agenda?.next_offset != null
            ? refresh(false, agenda.next_offset)
            : Promise.resolve()}
      />
      {#if agenda.incomplete}<p class="widget-muted">
          Partial calendar read. {agenda.errors
            ?.map((item) => item.error)
            .join(" ")}
        </p>{/if}
    {:else if widget.kind === "university" && value?.status === "ready"}<UniversityNav
        snapshot={$workspace.snapshot}
        {onOpen}
        disabled={busy}
        embedded
      />
    {:else if widget.kind === "tasks" && value?.status === "ready"}{#each taskItems as task}<button
          class="widget-link bookmark"
          on:click={() => onOpen("tasks")}
          >{task.title}{task.due ? ` · ${task.due}` : ""}</button
        >{:else}<p class="widget-muted">No open tasks.</p>{/each}<button
        class="widget-link"
        on:click={() => onOpen("tasks")}>All personal tasks</button
      >
    {:else}<p class="widget-muted">
        {!value
          ? "Loading…"
          : value.status === "empty"
            ? "Choose content with the ··· menu."
            : value?.status === "disabled"
              ? "Disabled in Workspace settings."
              : value?.reason || "Unavailable under current access."}
      </p>{/if}
    {#if widget.kind === "note" || widget.kind === "tasks"}<button
        class="widget-link widget-refresh"
        on:click={() => refresh(true)}
        >Refresh {widget.kind === "note" ? "note" : "tasks"}</button
      >{/if}
    {#if error}<p class="widget-error" role="alert">{error}</p>{/if}
  </div>
</article>
{#if openNote && note}{#key `${note.note_id}:${note.revision_id}:${note.view_token}`}<NoteOriginal
      widgetId={widget.id}
      {conversationId}
      preview={note}
      onClose={() => (openNote = false)}
    />{/key}{/if}

<style>
  .preview {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: var(--ke-text-xs);
    line-height: 1.85;
    max-height: 150px;
    overflow: auto;
    margin: 12px 0 16px;
  }
  .journal-widget[data-size="comfortable"] .preview {
    max-height: 320px;
    font-size: var(--ke-text-sm);
  }
  .journal-widget h3:not(.widget-heading h3) {
    font-size: var(--ke-text-base);
    font-weight: 500;
    margin: 0;
  }
  .bookmark {
    display: block;
    width: 100%;
  }
  .widget-heading-actions {
    display: flex;
    gap: 2px;
    align-items: center;
  }
  .calendar-refresh {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .widget-refresh {
    margin-top: 10px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
  }
</style>
