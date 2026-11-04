<script lang="ts">
  import { onDestroy } from "svelte";
  import HomeArtwork from "./HomeArtwork.svelte";
  import { request } from "$lib/api/client";
  import type { ResearchSearch } from "$lib/api/wire.generated";
  import {
    changes,
    emptyCounts,
    unread,
    searchStatus,
    type FindingView,
  } from "../tasks/research-view";
  export let active = true;
  export let disabled = false;
  export let researchNew = 0;
  export let researchVersion = 0;
  export let refreshVersion = 0;
  export let zone = "UTC";
  export let onResearch: (
    jobId?: string,
    view?: FindingView,
  ) => void = () => {};
  let rows: ResearchSearch[] = [],
    counts = emptyCounts();
  let loading = false,
    error = "",
    seen = "",
    generation = 0;
  let total = 0,
    reports = 0;
  $: key = `${researchNew}:${researchVersion}:${refreshVersion}`;
  $: if (active && key !== seen) {
    seen = key;
    void refresh();
  }
  $: if (!active) seen = "";
  $: count = unread(counts);
  async function refresh() {
    const token = ++generation;
    loading = true;
    error = "";
    try {
      const value = await request("research.overview", { offset: 0, limit: 3 });
      if (token !== generation) return;
      rows = value.items;
      counts = value.counts;
      total = value.total;
      reports = value.unread_reports;
    } catch (cause) {
      if (token === generation) {
        error = String(cause);
        rows = [];
      }
    } finally {
      if (token === generation) loading = false;
    }
  }
  function stamp(value: string | null | undefined) {
    if (!value) return "";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      timeZone: zone,
      month: "short",
      day: "numeric",
    }).format(d);
  }
  onDestroy(() => {
    generation++;
  });
</script>

<div class="research">
  <header>
    <h2>Research</h2>
    <button class="more" {disabled} on:click={() => onResearch()}
      >All research →</button
    >
  </header>
  {#if error}<p class="error" role="status">
      Research could not be read. {error}
    </p>
  {:else if rows.length}
    <div class="research-feature">
      <button
        class="research-metric"
        {disabled}
        on:click={() => onResearch()}
        aria-label={`${count} findings to review`}
      >
        <span class="big-number">{count}</span>
        <span class="metric-label"
          >{count === 1 ? "finding" : "findings"} to review<br />{changes(
            counts,
          ) ||
            (reports
              ? `${reports} ${reports === 1 ? "report" : "reports"} ready`
              : "You’re caught up")}<span aria-hidden="true"> ↗</span></span
        >
      </button>
      <div class="research-art"><HomeArtwork /></div>
    </div>
    <ul>
      {#each rows as task}<li>
          <button
            class="research-row"
            {disabled}
            on:click={() =>
              onResearch(task.id, unread(task.counts) ? "inbox" : "all")}
          >
            <span
              class="marker"
              class:unread={unread(task.counts) > 0}
              aria-hidden="true"
            ></span>
            <span class="details"
              ><strong title={task.title}>{task.title}</strong><span
                class="meta"
                >{changes(task.counts) ||
                  (task.new_report
                    ? "No unreviewed findings"
                    : `${task.counts.total} findings`)} · {searchStatus(
                  task,
                )}{#if task.last_run?.finished_at}
                  · {stamp(task.last_run.finished_at)}{/if}</span
              ></span
            ><span class="arrow" aria-hidden="true">↗</span>
          </button>
        </li>{/each}
    </ul>
    {#if total > rows.length}<p class="footnote">
        {total - rows.length} more {total - rows.length === 1
          ? "search"
          : "searches"} in All research.
      </p>{/if}
  {:else if loading}<p class="muted" role="status">Reading saved research…</p>
  {:else}<p class="muted">No research saved yet.</p>
    <button class="more" {disabled} on:click={() => onResearch()}
      >Open research →</button
    >{/if}
</div>

<style>
  header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  h2 {
    font: 550 13px/1.6 var(--ke-font-ui);
    margin: 0;
  }
  button {
    font: inherit;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 4px;
  }
  .more {
    font-size: 11px;
    color: var(--ke-text-secondary);
    background: none;
    border: 0;
    padding: 5px 0;
    text-align: left;
  }
  .more:hover:not(:disabled) {
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  li + li {
    border-top: 1px solid var(--ke-border);
  }
  .research-row {
    display: flex;
    align-items: baseline;
    width: 100%;
    padding: 12px 0;
    gap: 10px;
    border: 0;
    background: none;
    color: var(--ke-text-primary);
    text-align: left;
  }
  .marker {
    width: 5px;
    height: 5px;
    flex: none;
    border-radius: 50%;
    border: 1px solid var(--ke-border-strong);
  }
  .marker.unread {
    background: var(--ke-accent);
    border-color: var(--ke-accent);
  }
  .details {
    min-width: 0;
    flex: 1;
  }
  strong {
    font-size: 13px;
    font-weight: 450;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .research-row:hover:not(:disabled) strong {
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .meta {
    display: block;
    color: var(--ke-text-secondary);
    font-size: 10px;
    line-height: 1.6;
    margin-top: 4px;
  }
  .arrow {
    color: var(--ke-text-tertiary);
    font-size: 12px;
  }
  .muted,
  .error {
    font-size: 12px;
    line-height: 1.8;
    color: var(--ke-text-secondary);
    margin: 0;
  }
  .error {
    color: var(--ke-danger);
    overflow-wrap: anywhere;
  }
  .footnote {
    font-size: 10px;
    line-height: 1.6;
    color: var(--ke-text-secondary);
    margin: 12px 0 0;
  }

  strong {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .research-row {
    padding: 8px 0;
  }

  .research-feature {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 0 12px;
  }
  .research-metric {
    display: flex;
    gap: 16px;
    align-items: center;
    padding: 0;
    border: 0;
    background: none;
    text-align: left;
    color: var(--ke-text-primary);
  }
  .big-number {
    font:
      400 68px/0.95 Georgia,
      serif;
    letter-spacing: -4px;
  }
  .metric-label {
    font: 12px/1.6 var(--ke-font-ui);
    color: var(--ke-text-secondary);
  }
  .research-art {
    width: 142px;
    height: 65px;
    margin-left: auto;
    flex-shrink: 1;
    min-width: 70px;
  }
  header {
    margin-bottom: 8px;
  }
  .research-row {
    border-top: 1px solid var(--ke-border);
    padding: 10px 0;
  }
  li + li {
    border-top: 0;
  }
  .meta {
    margin-top: 2px;
  }
  @container home (max-width: 350px) {
    .research-art {
      width: 90px;
      min-width: 45px;
    }
    .big-number {
      font-size: 64px;
    }
    .research-metric {
      gap: 10px;
    }
    .metric-label {
      font-size: 11px;
    }
  }
</style>
