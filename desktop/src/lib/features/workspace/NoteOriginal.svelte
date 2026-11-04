<script lang="ts">
  import { request } from "$lib/api/client";
  import { drawerFocus } from "./drawer";
  import { workspaceRequest } from "./workspace";
  import type { Results } from "$lib/api/wire.generated";
  export let widgetId: string;
  export let conversationId: string;
  export let preview: Results["note.read"] & { view_token: string };
  export let onClose: () => void = () => {};
  let body = preview.content,
    end = preview.end,
    complete = preview.complete,
    loading = false,
    error = "";
  let sources: {
    relation?: string;
    read_ref?: string;
    status?: string;
    source_id?: string;
    artifact_id?: string;
    reason?: string;
  }[] = [];
  let sourceNext: number | null = null;
  let sourceRef = "",
    sourceText = "",
    sourceOffset = 0,
    sourceMore: number | null = null;
  async function more() {
    loading = true;
    error = "";
    try {
      const result = await request("note.read", {
        conversation_id: conversationId,
        note_id: preview.note_id,
        revision_id: preview.revision_id,
        view_token: preview.view_token,
        offset: end,
        maximum_bytes: 16384,
      });
      if (
        result.revision_id !== preview.revision_id ||
        result.sha256 !== preview.sha256
      )
        throw new Error("The pinned original changed; reopen it.");
      body += result.content;
      end = result.end;
      complete = result.complete;
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }
  async function provenance(append = false) {
    loading = true;
    error = "";
    try {
      const result = await workspaceRequest<{
        results: typeof sources;
        next_offset: number | null;
      }>("widget.sources", {
        id: widgetId,
        conversation_id: conversationId,
        offset: append ? sourceNext || 0 : 0,
      });
      sources = append ? [...sources, ...result.results] : result.results;
      sourceNext = result.next_offset;
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }
  async function readSource(reference: string, append = false) {
    loading = true;
    error = "";
    try {
      const result = await workspaceRequest<{
        content?: string;
        next_offset: number | null;
        reason?: string;
        status?: string;
      }>("widget.source", {
        id: widgetId,
        conversation_id: conversationId,
        read_ref: reference,
        offset: append ? sourceMore || 0 : 0,
        maximum: 16384,
      });
      if (typeof result.content !== "string")
        throw new Error(result.reason || "This source could not be delivered.");
      sourceText = append ? sourceText + result.content : result.content;
      sourceRef = reference;
      sourceMore = result.next_offset;
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }
</script>

<div
  class="uni drawer"
  use:drawerFocus={{ close: onClose }}
  role="dialog"
  tabindex="-1"
  aria-modal="false"
  aria-label="Pinned original note"
>
  <div class="row spread">
    <h2>{preview.note?.title || "Original note"}</h2>
    <button on:click={onClose}>Close original</button>
  </div>
  <p class="source">
    Note {preview.note_id} · revision {preview.revision_id}<br />SHA-256 {preview.sha256}
  </p>
  <pre>{body}</pre>
  <p class="muted">
    {end} / {preview.total_bytes} UTF-8 bytes · {complete
      ? "Complete original"
      : "Exact page; more remains"}
  </p>
  {#if !complete}<button disabled={loading} on:click={more}
      >Read more original note</button
    >{/if}
  {#if preview.attribution}<details>
      <summary>Attributed original wording</summary>
      <blockquote>{preview.attribution.source_quote}</blockquote>
    </details>{/if}
  {#if preview.correction}<details>
      <summary>Last correction</summary>
      <p>{preview.correction.reason}</p>
      <blockquote>{preview.correction.source_quote}</blockquote>
    </details>{/if}
  <button disabled={loading} on:click={() => provenance()}
    >Read original sources &amp; provenance</button
  >
  {#each sources as item}<div class="source-link">
      <p class="muted">
        {item.relation?.replaceAll("_", " ") || item.status || "Source"}
      </p>
      {#if item.read_ref}<button
          disabled={loading}
          on:click={() => readSource(item.read_ref!)}
          >Read linked original source</button
        >{:else}<p class="muted">Source access unavailable.</p>{/if}
    </div>{/each}
  {#if sourceNext !== null}<button
      disabled={loading}
      on:click={() => provenance(true)}>More source links</button
    >{/if}
  {#if sourceText}<h3>Linked original source</h3>
    <pre>{sourceText}</pre>
    {#if sourceMore !== null}<button
        disabled={loading}
        on:click={() => readSource(sourceRef, true)}
        >Next exact source page</button
      >{/if}{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font:
      12px/1.8 ui-monospace,
      monospace;
    padding: 14px;
    background: var(--ke-surface-inset);
  }
  .source-link {
    margin: 12px 0;
  }
  blockquote {
    font-size: 12px;
    white-space: pre-wrap;
  }
</style>
