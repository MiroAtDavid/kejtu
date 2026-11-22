<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import type { ResearchResult } from "$lib/api/wire.generated";
  import { when } from "../background/editor";
  import { findingHost, findingState, researchDay } from "./research-view";
  export let finding: ResearchResult;
  export let disabled = false;
  export let onOpen: () => void;
  export let onReview: () => void;
  export let onDisposition: (value: ResearchResult["disposition"]) => void;
  $: saved = finding.disposition === "saved";
  $: fresh = finding.attention === "new" || finding.attention === "changed";
</script>

<article
  class="finding cp-card"
  aria-label={finding.title}
  data-finding={finding.id}
>
  <div class="top">
    <!-- The card opens the finding's own page; the source link stays below. -->
    <button class="open" aria-label={finding.title} on:click={onOpen}>
      <span class="title">{finding.title}</span>
      <span class="details">{finding.details}</span>
    </button>
    <div class="quick">
      {#if finding.new && finding.disposition !== "dismissed"}<button
          class="cp-icon"
          aria-label={`Mark ${finding.title} reviewed`}
          title="Mark reviewed"
          {disabled}
          on:click={onReview}><Icon name="check" size={14} /></button
        >{/if}
      <button
        class="cp-icon"
        class:saved
        aria-label={`${saved ? "Unsave" : "Save"} ${finding.title}`}
        title={saved ? "Unsave" : "Save"}
        {disabled}
        on:click={() => onDisposition(saved ? "open" : "saved")}
        ><Icon name="bookmark" size={14} /></button
      >
    </div>
  </div>
  <div class="meta">
    <span class="state"
      >{#if fresh}<span
          class="dot"
          class:changed={finding.attention === "changed"}
          aria-hidden="true"
        ></span>{/if}{findingState(finding)}{saved ? " · Saved" : ""}</span
    >
    <span class="where"
      ><a href={finding.url} target="_blank" rel="noreferrer"
        >{findingHost(finding.url)}</a
      >
      ·
      <time
        datetime={finding.updated_at ?? finding.first_seen}
        title={when(finding.updated_at ?? finding.first_seen)}
        >{researchDay(finding.updated_at ?? finding.first_seen)}</time
      ></span
    >
  </div>
</article>

<style>
  .finding {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
    min-height: 118px;
    padding: 12px 14px;
  }
  .finding:hover {
    border-color: var(--ke-border-strong);
  }
  .top {
    display: flex;
    align-items: flex-start;
    gap: 4px;
    min-width: 0;
  }
  .open {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    min-height: 0;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .open:hover:not(:disabled) {
    background: transparent;
  }
  .title {
    font-size: var(--ke-text-sm);
    font-weight: 500;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  .details {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    color: var(--ke-text-secondary);
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .quick {
    display: flex;
    flex: none;
    margin: -4px -6px 0 0;
  }
  .quick .cp-icon {
    width: 26px;
    min-height: 26px;
  }
  .quick .saved {
    color: var(--ke-text-primary);
  }
  .quick .saved :global(svg) {
    fill: currentColor;
  }
  .meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .state {
    display: flex;
    flex: none;
    align-items: center;
    gap: 5px;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ke-marker-sage);
  }
  .dot.changed {
    background: var(--ke-marker-sand);
  }
  .where {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  a {
    color: inherit;
    text-decoration: none;
  }
  a:hover {
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
</style>
