<script lang="ts">
  import type { ResearchResult } from "$lib/api/wire.generated";
  import { when } from "../background/editor";
  import { researchTime } from "./research-view";
  export let finding: ResearchResult;
  export let disabled = false;
  export let onReview: () => void;
  export let onDisposition: (value: ResearchResult["disposition"]) => void;
  export let onDiscuss: () => void;
  let expanded = false;
  $: host = (() => {
    try {
      return new URL(finding.url).hostname.replace(/^www\./, "");
    } catch {
      return finding.url;
    }
  })();
</script>

<article
  class="finding"
  class:unread={finding.attention === "new" || finding.attention === "changed"}
  aria-label={finding.title}
>
  <div class="meta">
    <span
      class="state"
      class:new={finding.attention === "new"}
      class:changed={finding.attention === "changed"}
      >{finding.attention === "new"
        ? "New"
        : finding.attention === "changed"
          ? "Changed"
          : finding.attention === "dismissed"
            ? "Dismissed"
            : "Reviewed"}</span
    >
    {#if finding.disposition === "saved"}<span class="saved">Saved</span>{/if}
    <span class="source">{host}</span>
    <time
      datetime={finding.updated_at ?? finding.first_seen}
      title={when(finding.updated_at ?? finding.first_seen)}
      >{researchTime(finding.updated_at ?? finding.first_seen)}</time
    >
  </div>
  <h4>
    <a href={finding.url} target="_blank" rel="noreferrer"
      >{finding.title}<span aria-hidden="true"> ↗</span></a
    >
  </h4>
  <p class="body" class:preview={!expanded}>{finding.details}</p>
  {#if expanded}
    {#if finding.unknowns}<p class="uncertain">
        Still to verify: {finding.unknowns}
      </p>{/if}
    <p class="provenance">
      First found {when(finding.first_seen)} · Last seen {when(
        finding.last_seen,
      )}
    </p>
  {/if}
  <div class="actions">
    <button
      class="text-action"
      aria-expanded={expanded}
      on:click={() => (expanded = !expanded)}
      >{expanded ? "Less detail" : "Details"}</button
    >
    <div class="item-actions">
      {#if finding.new && finding.disposition !== "dismissed"}<button
          {disabled}
          on:click={onReview}>Mark reviewed</button
        >{/if}
      <button
        {disabled}
        on:click={() =>
          onDisposition(finding.disposition === "saved" ? "open" : "saved")}
        >{finding.disposition === "saved" ? "Unsave" : "Save"}</button
      >
      <button
        {disabled}
        on:click={() =>
          onDisposition(
            finding.disposition === "dismissed" ? "open" : "dismissed",
          )}
        >{finding.disposition === "dismissed" ? "Restore" : "Dismiss"}</button
      >
      <button {disabled} on:click={onDiscuss}>Discuss</button>
    </div>
  </div>
</article>

<style>
  .finding {
    padding: 20px 22px 16px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-lg);
    background: var(--ke-surface);
    min-width: 0;
  }
  .finding.unread {
    background: var(--ke-accent-soft);
  }
  .meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    font-size: var(--ke-text-3xs);
    color: var(--ke-text-secondary);
  }
  .source {
    overflow-wrap: anywhere;
  }
  time {
    margin-left: auto;
  }
  .state,
  .saved {
    padding: 2px 7px;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
  }
  .new {
    background: color-mix(in srgb, var(--ke-marker-sage) 18%, transparent);
    color: var(--ke-text-primary);
  }
  .changed {
    background: color-mix(in srgb, var(--ke-marker-sand) 20%, transparent);
    color: var(--ke-text-primary);
  }
  .saved {
    background: none;
    border: 1px solid var(--ke-border);
  }
  h4 {
    margin: 12px 0 8px;
    font: 500 17px/1.4 var(--ke-font-ui);
  }
  a {
    color: var(--ke-text-primary);
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  a:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  a span {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .body,
  .uncertain {
    font-size: var(--ke-text-sm);
    line-height: 1.75;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    margin: 0 0 12px;
  }
  .preview {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .uncertain {
    border-left: 2px solid var(--ke-marker-sand);
    padding-left: 12px;
    color: var(--ke-text-secondary);
  }
  .provenance {
    font-size: var(--ke-text-3xs);
    color: var(--ke-text-secondary);
  }
  .actions,
  .item-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .actions {
    justify-content: space-between;
    margin-top: 14px;
  }
  /* Review actions use the shared page-action surface from planning.css; only
     the quiet Details toggle keeps its own text treatment. */
  .text-action {
    font-size: var(--ke-text-2xs);
    padding: 5px 8px 5px 0;
    border: 1px solid transparent;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .text-action:hover:not(:disabled) {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  @media (max-width: 600px) {
    .finding {
      padding: 16px;
    }
    time {
      margin-left: 0;
    }
    h4 {
      font-size: var(--ke-text-md);
    }
  }
</style>
