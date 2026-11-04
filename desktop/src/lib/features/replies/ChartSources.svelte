<script lang="ts">
  import type { WebSourceSummary } from "$lib/api/wire.generated";
  import { getContext } from "svelte";
  import { VISUALS, type VisualContext } from "./context";
  import WebSources from "$lib/features/web/WebSources.svelte";
  const context = getContext<VisualContext>(VISUALS);
  let {
    refs = [],
    sources = [],
  }: { refs?: string[]; sources?: WebSourceSummary[] } = $props();
  const matched = $derived(
    sources.filter((source) => refs.includes(source.source_id)),
  );
</script>

{#if refs.length}
  {#if refs.some((ref) => !matched.some((source) => source.source_id === ref))}
    <p class="rich-source-warning">
      Some source links are unavailable in this saved reply.
    </p>
  {/if}
  {#if matched.length}
    <details class="rich-sources">
      <summary>Chart sources</summary>
      <p>
        Saved web sources from this reply. Check the supplied values against the
        source.
      </p>
      {#key matched.map((source) => source.source_id).join("/")}
        <WebSources turnId={context.turnId()} onlySources={matched} />
      {/key}
    </details>
  {/if}
{/if}
