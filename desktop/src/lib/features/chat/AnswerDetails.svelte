<script lang="ts">
  import ConnectionResults from "$lib/features/settings/ConnectionResults.svelte";
  import WebSources from "$lib/features/web/WebSources.svelte";
  import { onDestroy } from "svelte";
  import type { Inspection, Results, Turn } from "$lib/api/wire.generated";
  export let turn: Turn;
  export let initialSection: "activity" | "context" | null = null;
  export let readInspection:
    | ((id: string, cursor: string | null) => Promise<Results["turn.inspect"]>)
    | undefined = undefined;
  let items: Inspection[] = [];
  let cursor: string | null = null;
  let loaded = false;
  let loading = false;
  let error = "";
  let alive = true;
  let revision = 0;
  let observed = "";
  let contextOpen = initialSection === "context";
  let activityOpen = initialSection === "activity";
  const number = (value: number) => value.toLocaleString();

  $: identity = `${turn.id}:${turn.status}`;
  $: if (identity !== observed) {
    const refresh = loaded || loading || contextOpen || activityOpen;
    observed = identity;
    revision++;
    items = [];
    cursor = null;
    loaded = false;
    loading = false;
    error = "";
    // Only refresh an inspected reply. Normal text delivery/completion does not
    // wait for metadata or cause every historical turn to fetch inspection pages.
    if (refresh && readInspection) void load();
  }
  $: summary = items.find((item) => item.kind === "summary");
  $: attempts = items.filter((item) => item.kind === "model_attempt");
  $: accounting = items.filter((item) => item.kind === "context_accounting");
  $: checkpoints = items.filter((item) => item.kind === "context_checkpoint");
  $: operations = items.filter((item) => item.kind === "tool_delivery");
  $: latest = attempts.reduce<(typeof attempts)[number] | undefined>(
    (last, item) => (!last || item.ordinal > last.ordinal ? item : last),
    undefined,
  );
  $: latestAccounting = accounting.findLast(
    (item) => item.ordinal === latest?.ordinal,
  );
  $: windowSize =
    latestAccounting?.context_limit ?? summary?.profile.context_limit;
  // Never use cumulative turn tokens or a partially loaded inspection as the
  // latest request's occupancy. Unknown measurements stay unknown, not zero.
  $: used =
    loaded &&
    !cursor &&
    !loading &&
    !error &&
    turn.status !== "running" &&
    latest?.input_tokens != null &&
    latest.output_tokens != null &&
    windowSize
      ? latest.input_tokens + latest.output_tokens
      : null;
  $: meterLabel =
    used !== null && windowSize
      ? `Latest request: ${number(used)} of ${number(windowSize)} tokens`
      : "Context usage not loaded or not measured";

  async function load(more = false) {
    if (!readInspection || loading) return;
    const token = revision;
    const id = turn.id;
    loading = true;
    error = "";
    try {
      const page = await readInspection(id, more ? cursor : null);
      if (!alive || token !== revision) return;
      items = more ? [...items, ...page.items] : page.items;
      cursor = page.next_cursor;
      loaded = true;
    } catch {
      if (alive && token === revision)
        error = "Could not load reply information.";
    } finally {
      if (alive && token === revision) loading = false;
    }
  }
  function opened(event: Event) {
    if (
      (event.currentTarget as HTMLDetailsElement).open &&
      !loaded &&
      !loading &&
      !error
    )
      void load();
  }
  onDestroy(() => {
    alive = false;
    revision++;
  });
</script>

<div class="answer-information">
  <details class="tool-activity" bind:open={activityOpen} on:toggle={opened}>
    <summary>Tool activity{summary ? ` · ${summary.operations}` : ""}</summary>
    <div class="information">
      {#if !loaded && !loading && !error}<p>
          Open to inspect recorded operations.
        </p>{/if}
      {#if loading}<p role="status">Loading…</p>{/if}
      {#if error}<p role="status">{error}</p>{/if}
      {#if loaded && summary?.operations === 0}<p>
          No tool operations recorded.
        </p>{/if}
      {#each operations as operation (operation.call_id)}
        <div class="command">
          <p>{operation.state.replaceAll("_", " ")}</p>
          <pre><code>{operation.name}</code></pre>
        </div>
      {/each}
      {#if activityOpen && operations.some((operation) => operation.name === "web_search" || operation.name === "web_read")}
        {#key identity}<WebSources turnId={turn.id} />{/key}
      {/if}
      {#if activityOpen && operations.some((operation) => operation.name === "mcp_call")}
        {#key identity}<ConnectionResults turnId={turn.id} />{/key}
      {/if}
      {#if cursor}<p>More records are available.</p>{/if}
      {#if readInspection}
        <button
          type="button"
          disabled={loading}
          on:click={() => load(!!cursor)}
        >
          {error
            ? "Retry tool information"
            : cursor
              ? "More tool information"
              : "Refresh tool information"}
        </button>
      {/if}
    </div>
  </details>

  <details class="context-usage" bind:open={contextOpen} on:toggle={opened}>
    <summary>
      <span>Context</span>
      {#if used !== null && windowSize}
        <meter
          min="0"
          max={windowSize}
          value={used}
          aria-label={meterLabel}
          title={meterLabel}
        ></meter>
      {:else}
        <span
          class="unknown-meter"
          role="img"
          aria-label={meterLabel}
          title={meterLabel}
        ></span>
      {/if}
    </summary>
    <div class="information">
      {#if summary}<p class="model">{summary.profile.model}</p>{/if}
      {#if used !== null && windowSize}
        <p>
          {number(used)} / {number(windowSize)} tokens · {Math.round(
            (used / windowSize) * 100,
          )}% in the latest request.
        </p>
      {:else if loaded}<p>
          Latest request occupancy is {cursor
            ? "unavailable until all records are loaded"
            : "not measured"}.
        </p>{/if}
      {#if loading}<p role="status">Loading…</p>{/if}
      {#if error}<p role="status">{error}</p>{/if}
      {#each attempts as attempt (attempt.ordinal)}
        {@const allocation = accounting.findLast(
          (item) => item.ordinal === attempt.ordinal,
        )}
        <div class="call">
          <strong>Request {attempt.ordinal}</strong>
          <p>
            {attempt.input_tokens === null
              ? "Input unknown"
              : `${number(attempt.input_tokens)} input`} · {attempt.output_tokens ===
            null
              ? "Output unknown"
              : `${number(attempt.output_tokens)} generated`}
          </p>
          {#if allocation}
            <p>
              {number(allocation.context_limit)} token window · {number(
                allocation.generation_reserved,
              )} reserved for reply and reasoning.
            </p>
            <p>
              {number(allocation.request_input)} estimated input · {number(
                allocation.safety_margin + allocation.framing_allowance,
              )} margin/framing ({allocation.method}).
            </p>
          {/if}
          {#if attempt.finish_reason}<p>
              Completion: {attempt.finish_reason.replaceAll("_", " ")}
            </p>{/if}
        </div>
      {/each}
      {#if summary?.admission}
        <p>
          {summary.admission.admitted_turns} of {summary.admission
            .earlier_turns} earlier exchanges admitted; {summary.admission
            .omitted_turns} omitted.
        </p>
      {/if}
      {#each checkpoints as checkpoint}
        <p>
          Context compression: {checkpoint.status.replaceAll("_", " ")}.
          Original messages retained.
        </p>
      {/each}
      <div class="metrics">
        <strong>Total usage for this reply</strong>
        {#if turn.status === "running"}<span>Usage pending</span>{:else}
          <span
            >{turn.input_tokens === null
              ? "Input usage unknown"
              : `${number(turn.input_tokens)} input tokens`}</span
          >
          <span
            >{turn.output_tokens === null
              ? "Output usage unknown"
              : `${number(turn.output_tokens)} output tokens`}</span
          >
        {/if}
        <span>{turn.calls} model {turn.calls === 1 ? "call" : "calls"}</span>
        <p>Totals across this response’s calls; not context occupancy.</p>
      </div>
      {#if cursor}<p>Showing loaded records only.</p>{/if}
      {#if readInspection}
        <button
          type="button"
          disabled={loading}
          on:click={() => load(!!cursor)}
        >
          {error
            ? "Retry context information"
            : cursor
              ? "More context information"
              : "Refresh context information"}
        </button>
      {/if}
    </div>
  </details>
</div>

<style>
  .answer-information {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    padding: 0;
    display: grid;
    gap: 1.25rem;
  }
  summary {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    cursor: pointer;
    width: fit-content;
    min-height: 1.25rem;
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  summary::before {
    content: "›";
  }
  details[open] summary::before {
    content: "⌄";
  }
  summary:hover {
    color: var(--ke-text-primary);
  }
  /* The original CommandActivity disclosure: native triangle, muted label and
     a separate ruled command block. Only recorded replacement data is shown. */
  .tool-activity {
    color: var(--ke-text-secondary);
    font-size: 0.78rem;
  }
  .tool-activity summary {
    display: list-item;
    list-style: disclosure-closed inside;
  }
  .tool-activity[open] summary {
    list-style-type: disclosure-open;
  }
  .tool-activity summary::before,
  .tool-activity[open] summary::before {
    content: none;
  }
  .tool-activity summary::-webkit-details-marker {
    display: revert;
  }
  .tool-activity .information {
    display: block;
    margin-top: 0;
    padding-left: 0;
    border-left: 0;
  }
  .tool-activity p {
    margin: 0.4rem 0;
  }
  .command {
    border-left: 1px solid var(--ke-border);
    padding-left: 0.8rem;
    margin-top: 0.8rem;
  }
  .command pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 14rem;
    overflow: auto;
    font-size: 0.76rem;
    color: var(--ke-text-primary);
  }
  summary:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--ke-border-strong);
    outline-offset: 3px;
  }
  .information {
    display: grid;
    gap: 0.65rem;
    margin-top: 0.65rem;
    padding-left: 0.75rem;
    border-left: 1px solid var(--ke-border);
    max-width: 42rem;
  }
  p {
    margin: 0;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  strong {
    font-weight: 550;
  }
  .model {
    color: var(--ke-text-secondary);
  }
  .call {
    display: grid;
    gap: 0.2rem;
  }
  .metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .metrics strong,
  .metrics p {
    flex-basis: 100%;
  }
  button {
    color: inherit;
    font: inherit;
    border: 0;
    background: none;
    padding: 0;
    width: fit-content;
    text-decoration: underline;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  meter,
  .unknown-meter {
    appearance: none;
    -webkit-appearance: none;
    width: 1.5rem;
    height: 3px;
    border: 0;
    border-radius: 999px;
    background: var(--ke-border);
  }
  meter::-webkit-meter-bar {
    height: 3px;
    border: 0;
    border-radius: 999px;
    background: var(--ke-border);
    box-shadow: none;
  }
  meter::-webkit-meter-optimum-value {
    border-radius: 999px;
    background: var(--ke-text-tertiary);
  }
  meter::-moz-meter-bar {
    border-radius: 999px;
    background: var(--ke-text-tertiary);
  }
</style>
