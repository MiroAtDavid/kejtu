<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import {
    editSummary,
    replySummary,
    proposalText,
  } from "$lib/features/workspace/document-assist";
  export let text: string;
  export let answer = false;
  $: summary = answer ? null : editSummary(text);
  $: reply = answer ? replySummary(text) : null;
</script>

<div class="document-edit">
  <div class="label">
    <Icon name="file" size={14} /><span
      >{answer
        ? reply?.action === "reply"
          ? "Document conversation"
          : "Document edit proposal"
        : summary?.document
          ? `Document · ${summary.name}`
          : `${summary?.mode === "insert" ? "Insert into" : "Edit"} ${summary?.name ?? "Markdown"}`}</span
    >
  </div>
  <p>{answer ? reply?.explanation : summary?.instruction}</p>
  {#if !answer || reply?.action === "edit"}<details>
      <summary
        >{answer ? "Proposed Markdown" : "Request and draft context"}</summary
      >
      <pre>{answer
          ? reply?.action === "edit"
            ? proposalText(reply)
            : ""
          : text}</pre>
    </details>{/if}
</div>

<style>
  .document-edit {
    border-left: 2px solid var(--ke-border-strong);
    padding: 5px 12px;
    font-size: var(--ke-text-sm);
    font-weight: 400;
    line-height: 1.6;
    white-space: normal;
  }
  .label {
    display: flex;
    align-items: center;
    gap: 7px;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-2xs);
  }
  p {
    margin: 7px 0;
    white-space: pre-wrap;
  }
  details {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
  }
  summary {
    cursor: pointer;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 240px;
    overflow: auto;
    user-select: text;
  }
</style>
