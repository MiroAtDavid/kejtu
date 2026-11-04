<script lang="ts">
  import type { Conversation } from '$lib/api/wire.generated';
  import {
    conversationAccessibleName,
    visibleConversationLabel,
  } from '$lib/features/chat/conversation-label';

  /**
   * Compact conversation chooser.
   *
   * This is deliberately not the old left sidebar: a single disclosure row
   * replaces both old sidebar bars, keeping the current conversation visible and
   * the full list one action away. The row/list styling is adapted from the old
   * `HistoryList.svelte` (row grid, icon cell, hover/active treatment) without
   * its rename/delete affordances, which have no native operation.
   *
   * Labels come from stored `created_at` metadata only. No title, summary or
   * message text is invented and no body is fetched to build a label. Actual ids
   * are used for selection; paging, selected state and the disabled guard while a
   * write is uncertain are preserved.
   */
  export let conversations: Conversation[] = [];
  export let selected = '';
  export let cursor: string | null = null;
  export let listing = false;
  export let disabled = false;
  export let onOpen: (id: string) => void = () => {};
  export let onMore: () => void = () => {};

  $: current = conversations.find((item) => item.conversation_id === selected) ?? null;
  $: currentLabel = current ? visibleConversationLabel(current.created_at) : 'Choose a conversation';
  $: currentExact = current
    ? conversationAccessibleName(current.created_at, current.conversation_id)
    : '';
  /**
   * The menu opens on an explicit reader action and closes after a selection.
   *
   * A collapsed `<details>` hides its content from the accessibility tree and is
   * unclickable in a real browser, which jsdom does not reproduce — so the
   * trigger is a real summary that a person clicks, and the list is *shown* only
   * while that disclosure is open. Nothing depends on a hidden control.
   */
  let open = false;

  function choose(id: string) {
    open = false;
    onOpen(id);
  }
</script>

<div class="chooser">
  <details class="menu" bind:open>
    <!-- The disclosure has no aria-label of its own: its accessible name comes
         from its contents, and any extra label here would also match the
         list region below, making one click target ambiguous. -->
    <summary class="trigger" title={currentExact}>
      <span class="icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" width="16" height="16">
          <path d="M3 5h14M3 10h14M3 15h9"></path>
        </svg>
      </span>
      <span class="copy">
        <strong>{currentLabel}</strong>
        {#if current}
          <!-- Visible disambiguator is a short id; the exact stored timestamp
               stays available through the trigger's title and accessible name. -->
          <small>{current.conversation_id.slice(0, 8)}</small>
        {/if}
      </span>
      <span class="chev" aria-hidden="true">
        <svg viewBox="0 0 20 20" width="16" height="16">
          <path d="m6 8 4 4 4-4"></path>
        </svg>
      </span>
    </summary>

    <div class="list" role="group" aria-label="Conversations">
      {#if conversations.length === 0}
        <p class="empty">No conversations yet.</p>
      {/if}
      {#each conversations as conversation (conversation.conversation_id)}
        <div
          class="conversation-row"
          class:active={selected === conversation.conversation_id}
        >
          <button
            type="button"
            class="conversation"
            aria-current={selected === conversation.conversation_id ? 'true' : undefined}
            aria-label={conversationAccessibleName(
              conversation.created_at,
              conversation.conversation_id,
            )}
            {disabled}
            on:click={() => choose(conversation.conversation_id)}
          >
            <span class="conversation-icon" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="16" height="16">
                <path d="M4 5.5h12v7H9l-3 2.5v-2.5H4z"></path>
              </svg>
            </span>
            <span class="copy">
              <strong>{visibleConversationLabel(conversation.created_at)}</strong>
              <small>{conversation.conversation_id.slice(0, 8)}</small>
            </span>          </button>
        </div>
      {/each}
      {#if cursor}
        <button type="button" class="more" disabled={listing} on:click={onMore}
          >More conversations</button
        >
      {/if}
    </div>
  </details>
</div>

<style>
  .chooser {
    flex: 0 0 auto;
    position: relative;
    z-index: 10;
    padding: var(--ke-space-2) var(--ke-space-4);
    background: var(--ke-surface);
  }

  .menu {
    position: relative;
    max-width: 34rem;
  }

  /* Old `.conversation-row` grid: icon cell, then the copy column. */
  .trigger {
    display: grid;
    grid-template-columns: 1.5rem minmax(0, 1fr) 1.25rem;
    align-items: center;
    gap: var(--ke-space-2);
    min-height: 2.25rem;
    padding: 0.35rem var(--ke-space-2);
    border: 1px solid transparent;
    border-radius: var(--ke-radius-md);
    color: var(--ke-text-secondary);
    cursor: pointer;
    list-style: none;
  }

  .trigger::-webkit-details-marker {
    display: none;
  }

  .trigger:hover {
    background: var(--ke-surface-inset);
  }

  .menu[open] .trigger {
    background: var(--ke-surface-inset);
  }

  .chev {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--ke-text-tertiary);
  }

  .menu[open] .chev {
    transform: rotate(180deg);
  }

  .icon,
  .conversation-icon {
    width: 1.5rem;
    height: 1.5rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--ke-text-secondary);
  }

  .copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
    text-align: left;
  }

  .copy strong {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--ke-text-xs);
    font-weight: 500;
    color: var(--ke-text-primary);
  }

  .copy small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }

  /* Click-to-open menu anchored under the trigger, in the old list idiom. */
  .list {
    position: absolute;
    z-index: 20;
    top: calc(100% + 2px);
    left: 0;
    right: 0;
    max-height: min(50vh, 20rem);
    overflow: auto;
    padding: var(--ke-space-1);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-shadow-panel);
  }

  .conversation-row {
    min-width: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--ke-space-1);
    border: 1px solid transparent;
    min-height: 2.25rem;
    border-radius: var(--ke-radius-md);
    color: var(--ke-text-secondary);
  }

  .conversation-row.active {
    background: var(--ke-surface-inset);
  }

  .conversation {
    display: grid;
    grid-template-columns: 1.5rem minmax(0, 1fr);
    align-items: center;
    gap: var(--ke-space-2);
    padding: 0.35rem var(--ke-space-2);
    border: none;
    border-radius: inherit;
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }

  .conversation:hover .copy strong {
    color: var(--ke-accent);
  }

  .empty {
    margin: 0;
    padding: var(--ke-space-2);
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }

  .more {
    width: 100%;
    margin-top: var(--ke-space-1);
    padding: var(--ke-space-1) var(--ke-space-2);
    border: 1px dashed var(--ke-border-strong);
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }

  button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  @media (max-width: 700px) {
    .chooser {
      padding: var(--ke-space-2) var(--ke-space-3);
    }

    .menu {
      max-width: none;
    }
  }
</style>
