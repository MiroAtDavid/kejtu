<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import SidebarIdentity from "$lib/design/SidebarIdentity.svelte";
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import type { IndexConversation } from "./index-rows";

  // Conversation and entry labels retain their backend identity and order.
  export let conversations: IndexConversation[] = [];
  export let selected = "";
  export let cursor: string | null = null;
  export let listing = false;
  export let disabled = false;

  export let active = true;
  export let initialConversationsOpen = false;
  let conversationsOpen = initialConversationsOpen;
  let enteredConversations = initialConversationsOpen;
  export let onLatest: (id: string) => void = () => {};

  // Leaving chat, or starting a fresh draft, makes the next heading click a
  // navigation action again. Choosing older history within chat does not.
  $: if (!active || (!selected && conversations.length))
    enteredConversations = false;
  function activateConversations() {
    if (disabled || listing) return;
    if (!enteredConversations) {
      enteredConversations = true;
      conversationsOpen = false;
      // The archive's existing newest-first list owns this order, not the filter.
      onLatest(conversations[0]?.id || "");
      return;
    }
    conversationsOpen = !conversationsOpen;
  }
  export let canCreate = false;
  export let onCreate: () => void = () => {};
  export let onOpen: (id: string) => void = () => {};
  export let onMore: () => void = () => {};

  /** Filter text. The journal's compact list filters by name the same way. */
  let query = "";

  $: shown = query.trim()
    ? conversations.filter((item) =>
        item.label
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase()),
      )
    : conversations;
</script>

<!-- `use:quietScrollbar` matches the transcript: the bar appears only while the
     index is being scrolled, then fades. It never changes the gutter, so revealing
     it cannot shift the rows. -->
<div class="index" use:quietScrollbar>
  <SidebarIdentity />

  <button
    type="button"
    class="sidebar-menu-row"
    aria-label="Start a new conversation"
    disabled={!canCreate || disabled}
    on:click={onCreate}><Icon name="plus" size={15} />New conversation</button
  >
  <nav class="collections" aria-label="Conversations">
    <button
      type="button"
      class="sidebar-menu-row disclosure"
      class:current={active}
      aria-current={active ? "page" : undefined}
      aria-expanded={conversationsOpen}
      aria-controls="index-conversations"
      disabled={disabled || listing}
      on:click={activateConversations}
      ><Icon name="comment" size={15} />Conversations</button
    >
    <div
      id="index-conversations"
      class="conversation-list"
      hidden={!conversationsOpen}
    >
      {#if conversations.length === 0}
        <p class="empty">No conversations yet.</p>
      {:else}
        <input
          type="search"
          class="history-search"
          aria-label="Find conversation"
          placeholder="Find conversation…"
          bind:value={query}
        />
      {/if}
      <ol use:quietScrollbar>
        {#each shown as conversation (conversation.id)}
          <li>
            <button
              type="button"
              class="row"
              class:active={selected === conversation.id}
              aria-current={selected === conversation.id ? "true" : undefined}
              {disabled}
              title={`${conversation.label} — ${conversation.createdAt} (${conversation.id})`}
              on:click={() => onOpen(conversation.id)}
            >
              <span class="name">{conversation.label}</span>
            </button>
          </li>
        {/each}
      </ol>
      {#if cursor}
        <button type="button" class="more" disabled={listing} on:click={onMore}
          >More conversations</button
        >
      {/if}
    </div>
  </nav>
</div>

<style>
  .row.active {
    background: var(--ke-surface-inset);
  }

  .index {
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 0;
    overflow-y: auto;
    overscroll-behavior-y: contain;
  }

  .collections {
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: var(--ke-space-1);
  }

  .conversation-list {
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: var(--ke-space-1);
    margin: 4px 0 8px 10px;
    padding: 2px 0 8px 10px;
    border-left: 1px solid var(--ke-border);
  }

  /* Explicit display is needed because the flex rule overrides native hidden. */
  .conversation-list[hidden] {
    display: none;
  }

  /* The journal's compact list keeps a bounded height and scrolls inside itself
     (`.compact ol { max-height: 320px }`), so a long history cannot push the
     index off the window. */
  ol {
    display: flex;
    flex-direction: column;
    gap: var(--ke-space-1);
    margin: 0;
    padding: 0;
    list-style: none;
    max-height: 320px;
    overflow-y: auto;
    overscroll-behavior-y: contain;
  }

  .history-search {
    width: 100%;
    margin: 0 0 6px;
    padding: 6px 0;
    border: 0;
    border-bottom: 1px solid var(--ke-border);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    font-size: var(--ke-text-2xs);
  }

  .history-search::placeholder {
    color: var(--ke-text-tertiary);
  }

  .history-search:focus {
    outline: none;
    border-bottom-color: var(--ke-text-tertiary);
  }

  /* Compact row: the name only, 11px and regular. No icon, no timestamp — the
     journal shows those in its full list, which this index does not use. */
  .row {
    display: block;
    width: 100%;
    min-width: 0;
    padding: calc(9px * var(--ke-density)) 10px;
    border: 0;
    background: none;
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
    font-size: var(--ke-label-size);
    font-weight: 400;
    line-height: 1.6;
    text-align: left;
    cursor: pointer;
  }

  .row:hover:not(:disabled) .name {
    color: var(--ke-accent);
  }

  .row.active .name {
    color: var(--ke-text-primary);
  }

  .name {
    display: block;
    min-width: 0;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .row:disabled {
    opacity: 0.5;
    cursor: default;
  }

  /* The list must keep its natural height... */
  .collections,
  .conversation-list,
  ol,
  ol > li {
    flex-shrink: 0;
  }

  .empty {
    margin: 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }

  .more {
    align-self: flex-start;
    text-align: left;
    line-height: 1.6;
    margin-top: var(--ke-space-1);
    padding: 2px 0;
    border: 0;
    background: transparent;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-2xs);
    cursor: pointer;
  }
  .more:hover:not(:disabled) {
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .more:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
