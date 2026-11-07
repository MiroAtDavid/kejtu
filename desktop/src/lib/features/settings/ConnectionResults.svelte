<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type { McpResultSummary } from "$lib/api/wire.generated";
  export let turnId: string;
  let rows: McpResultSummary[] = [];
  let error = "",
    content = "",
    selected = "";
  let busy = false,
    alive = true;
  let next: number | null = null;
  let total = 0;
  async function load() {
    try {
      const value = await request("mcp.results", { turn_id: turnId });
      if (alive) rows = value.items;
    } catch (caught) {
      if (alive)
        error =
          caught instanceof Error
            ? caught.message
            : "Could not load connected tool activity.";
    }
  }
  async function read(resultId: string, offset = 0) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      const page = await request("mcp.result", {
        turn_id: turnId,
        result_id: resultId,
        offset,
        maximum_bytes: 8192,
      });
      if (!alive) return;
      selected = resultId;
      content = page.content;
      next = page.next_offset;
      total = page.total_bytes;
    } catch (caught) {
      if (alive)
        error =
          caught instanceof Error
            ? caught.message
            : "Could not read the saved result.";
    } finally {
      if (alive) busy = false;
    }
  }
  onMount(() => {
    void load();
  });
  onDestroy(() => {
    alive = false;
  });
</script>

<div class="connected-results">
  {#each rows as row (row.id)}
    <p>
      <strong>{row.server} · {row.tool}</strong> — {row.status === "uncertain"
        ? "Completion unknown; not retried"
        : row.status}
      {#if row.result_id}<button
          disabled={busy}
          on:click={() => read(row.result_id!)}>Read saved result</button
        >{/if}
    </p>
  {/each}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if selected}
    <p>
      Exact saved MCP response · {total.toLocaleString()} bytes. Reading this does
      not call the tool again.
    </p>
    <pre>{content}</pre>
    {#if next !== null}<button
        disabled={busy}
        on:click={() => read(selected, next!)}>Next part</button
      >{/if}
  {/if}
</div>

<style>
  .connected-results {
    margin-block: 0.7rem;
    font-size: var(--ke-text-sm);
    color: var(--ke-text-secondary);
  }
  p {
    overflow-wrap: anywhere;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 24rem;
    overflow-y: auto;
  }
  button {
    background: var(--ke-surface);
    color: var(--ke-text-primary);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    padding: 0.3rem 0.5rem;
    margin: 0.3rem;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
  }
</style>
