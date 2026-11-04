<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type { WebSourceSummary } from "$lib/api/wire.generated";
  import SourceLink from "$lib/features/chat/SourceLink.svelte";

  // Parent keys by turn/status. Late responses never populate another exchange.
  export let turnId: string;
  /** Optional subset already resolved through this turn's native source list. */
  export let onlySources: WebSourceSummary[] | null = null;
  let sources: WebSourceSummary[] = [];
  let loading = false,
    alive = true;
  let selected = "",
    content = "",
    error = "";
  let next: number | null = null;
  let total = 0;
  async function load() {
    loading = true;
    error = "";
    try {
      const result = await request("web.sources", { turn_id: turnId });
      if (alive)
        sources =
          onlySources === null
            ? result.items
            : result.items.filter((item) =>
                onlySources?.some(
                  (source) => source.source_id === item.source_id,
                ),
              );
    } catch (caught) {
      if (alive)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (alive) loading = false;
    }
  }
  async function inspect(id: string, offset = 0) {
    if (loading) return;
    loading = true;
    error = "";
    try {
      const page = await request("web.source", {
        source_id: id,
        offset,
        maximum_bytes: 8192,
      });
      if (!alive) return;
      if (
        page.source_id !== id ||
        page.offset !== offset ||
        (page.next_offset !== null && page.next_offset <= offset)
      )
        throw new Error(
          "Saved source page does not match the requested position.",
        );
      selected = id;
      // Display one exact bounded page; reading a large source cannot grow the
      // transcript or trigger a fresh network request.
      content = page.content;
      next = page.next_offset;
      total = page.total_bytes;
    } catch (caught) {
      if (alive)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (alive) loading = false;
    }
  }
  onMount(() => {
    if (onlySources === null) void load();
    else sources = onlySources;
  });
  onDestroy(() => {
    alive = false;
  });
</script>

<div class="sources" aria-label="Saved web sources">
  {#each sources as source (source.source_id)}
    <div class="source">
      <span
        ><SourceLink href={source.url}
          >{source.title || source.url || "Search results"}</SourceLink
        ></span
      >
      <span class="metadata"
        >{source.kind === "search" ? "Search results" : "Page read"} · {source.status}
        · {source.fetched_at}</span
      >
      <button disabled={loading} on:click={() => inspect(source.source_id)}
        >Inspect saved source</button
      >
      {#if selected === source.source_id}
        <pre aria-label="Saved source text">{content}</pre>
        <span class="metadata"
          >{total.toLocaleString()} bytes saved{next !== null
            ? " · More available"
            : " · End of source"}</span
        >
        {#if next !== null}<button
            disabled={loading}
            on:click={() => inspect(source.source_id, next!)}
            >Next source page</button
          >{/if}
      {/if}
    </div>
  {/each}
  {#if loading}<p role="status">Loading saved sources…</p>{/if}
  {#if error}<p role="alert">{error}</p>
    <button disabled={loading} on:click={load}>Reload sources</button>{/if}
</div>

<style>
  .sources,
  .source {
    display: grid;
    gap: 0.5rem;
  }
  .source {
    padding-block: 0.65rem;
  }
  .metadata {
    color: var(--ke-text-tertiary);
    overflow-wrap: anywhere;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 24rem;
    overflow: auto;
    margin: 0;
    font-size: inherit;
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
</style>
