<script lang="ts">
  import { onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type { McpTool } from "$lib/api/wire.generated";

  export let server: string;
  export let revision: number;
  export let tool: McpTool;
  let content = tool.input_schema;
  let next = tool.schema_page?.next_offset ?? null;
  let busy = false;
  let alive = true;
  let error = "";
  $: shown = new TextEncoder().encode(content).length;

  async function more() {
    if (busy || next === null || !tool.schema_page) return;
    busy = true;
    error = "";
    const offset = next;
    const identity = tool.schema_page;
    try {
      const page = await request("mcp.schema", {
        server,
        tool: tool.name,
        expected_revision: revision,
        sha256: identity.sha256,
        offset,
      });
      if (!alive) return;
      const bytes = new TextEncoder().encode(page.content).length;
      if (
        page.sha256 !== identity.sha256 ||
        page.total_bytes !== identity.total_bytes ||
        page.offset !== offset ||
        page.end !== offset + bytes ||
        bytes === 0 ||
        page.end > identity.total_bytes ||
        page.next_offset !== (page.end < identity.total_bytes ? page.end : null)
      )
        throw new Error("The schema page changed. Check the connection again.");
      content += page.content;
      next = page.next_offset;
    } catch (caught) {
      if (alive)
        error =
          caught instanceof Error
            ? caught.message
            : "Could not read the schema.";
    } finally {
      if (alive) busy = false;
    }
  }
  onDestroy(() => {
    alive = false;
  });
</script>

<pre>{content}</pre>
{#if tool.schema_page}
  <p class="hint">
    Showing {shown.toLocaleString()} of {tool.schema_page.total_bytes.toLocaleString()}
    schema bytes.
  </p>
{/if}
{#if error}<p role="alert">{error}</p>{/if}
{#if next !== null}
  <button disabled={busy} on:click={more}
    >{busy ? "Reading…" : "Read more schema"}</button
  >
{/if}

<style>
  pre {
    font-size: var(--ke-text-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 24rem;
    overflow: auto;
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
  .hint {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
</style>
