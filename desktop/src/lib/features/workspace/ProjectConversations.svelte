<script lang="ts">
  import { workspaceRequest, type Workspace } from "./workspace";
  export let workspace: Workspace;
  export let projectId: string;
  export let active = true;
  export let disabled = false;
  export let onOpen: (id: string) => void = () => {};
  export let onCreate: (projectId: string) => void = () => {};
  type Row = {
    conversation_id: string;
    title: string | null;
    created_at: string;
  };
  let items: Row[] = [];
  let next: number | null = null;
  let loading = false;
  let error = "";
  let token = 0;
  let observed: unknown;
  let previous = "";
  let wasActive = false;
  $: if (
    projectId !== previous ||
    $workspace.snapshot !== observed ||
    active !== wasActive
  ) {
    previous = projectId;
    observed = $workspace.snapshot;
    wasActive = active;
    ++token;
    loading = false;
    if (active) void load();
  }
  async function load(offset = 0) {
    const current = ++token;
    loading = true;
    error = "";
    if (!offset) {
      items = [];
      next = null;
    }
    try {
      const result = await workspaceRequest<{
        items: Row[];
        next_offset: number | null;
      }>("conversation.page", { project_id: projectId, offset, limit: 25 });
      if (current !== token) return;
      items = offset ? [...items, ...result.items] : result.items;
      next = result.next_offset;
    } catch (e) {
      if (current === token) error = String(e);
    } finally {
      if (current === token) loading = false;
    }
  }
</script>

<section class="project-conversations" aria-label="Project conversations">
  <div class="heading">
    <h2>Conversations</h2>
    <button
      class="workspace-action"
      {disabled}
      on:click={() => onCreate(projectId)}>New conversation</button
    >
  </div>
  {#each items as item (item.conversation_id)}
    <button
      class="conversation-row"
      {disabled}
      on:click={() => onOpen(item.conversation_id)}
      >{item.title || "Untitled conversation"}</button
    >
  {:else}{#if !loading && !error}<p>
        Conversations attached to this project will appear here.
      </p>{/if}{/each}
  {#if loading}<p role="status">Loading conversations…</p>{/if}
  {#if error}<p role="alert">{error}</p>
    <button disabled={loading} on:click={() => load()}>Try again</button>{/if}
  {#if next !== null}<button
      disabled={loading || disabled}
      on:click={() => load(next!)}>More conversations</button
    >{/if}
</section>

<style>
  /* Sits in a card on the project's page; the card is the frame. */
  .project-conversations {
    min-width: 0;
  }
  .heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .project-conversations h2 {
    margin: 0;
    color: var(--ke-text-tertiary);
    font: 500 11.5px/1.6 var(--ke-font-ui);
  }
  p {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .conversation-row {
    display: block;
    width: 100%;
    text-align: left;
    margin-top: 8px;
    padding: 10px 0;
    color: var(--ke-text-primary);
    font: inherit;
    border: 0;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;
  }
  .conversation-row:hover:not(:disabled) {
    background: var(--ke-surface-inset);
  }
  .conversation-row:disabled {
    opacity: 0.5;
  }
  .conversation-row:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
</style>
