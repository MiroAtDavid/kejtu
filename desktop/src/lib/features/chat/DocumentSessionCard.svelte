<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import type { DocumentReference } from "$lib/features/workspace/document-assist";
  import type { DocumentSession } from "$lib/features/workspace/document-conversation";
  export let session: DocumentSession;
  export let time = "";
  export let expanded = false;
  export let disabled = false;
  export let stopping = false;
  export let onToggle: () => void;
  export let onOpen:
    ((reference: DocumentReference) => Promise<void>) | undefined = undefined;
  export let onStop: (() => void) | undefined = undefined;
  let error = "";
  async function open() {
    if (!session.reference || !onOpen) return;
    error = "";
    try {
      await onOpen(session.reference);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
</script>

<div class="document-session" data-turn={session.head}>
  <time>{time}</time>
  <div class="row">
    <Icon name="file" size={15} />
    {#if session.reference && onOpen}<button
        class="name"
        {disabled}
        on:click={open}>Worked on {session.name}</button
      >
    {:else}<span class="name">Worked on {session.name}</span>{/if}
    <span class="count"
      >{session.count} {session.count === 1 ? "exchange" : "exchanges"}</span
    >
    <button class="details" aria-expanded={expanded} on:click={onToggle}
      >{expanded ? "Hide details" : "Details"}</button
    >
  </div>
  {#if session.running}<div class="status" role="status">
      Kejtu is working… {#if onStop}<button
          disabled={stopping}
          on:click={onStop}>{stopping ? "Stopping…" : "Stop"}</button
        >{/if}
    </div>
  {:else if session.failed}<p class="status">
      An exchange did not complete. See Details.
    </p>{/if}
  {#if error}<p class="status" role="status">{error}</p>{/if}
</div>

<style>
  .document-session {
    margin: 24px 0;
    padding: 12px 0;
    border-top: 1px solid var(--ke-border);
    color: var(--ke-text-secondary);
  }
  time {
    font-size: 10px;
    color: var(--ke-text-tertiary);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 8px 0;
    min-width: 0;
  }
  button {
    background: transparent;
    border: 0;
    color: inherit;
    font: inherit;
    padding: 2px;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .name {
    min-width: 0;
    text-align: left;
    overflow-wrap: anywhere;
    font-size: 13px;
  }
  .count,
  .details,
  .status {
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .count {
    white-space: nowrap;
  }
  .details {
    margin-left: auto;
    white-space: nowrap;
  }
  .status {
    margin: 4px 0;
  }
  @media (max-width: 500px) {
    .row {
      flex-wrap: wrap;
    }
    .name {
      max-width: 75%;
    }
  }
</style>
