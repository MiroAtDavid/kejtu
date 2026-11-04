<script lang="ts">
  import { setContext, untrack } from "svelte";
  import SvelteMarkdown, { allowHtmlOnly } from "@humanspeak/svelte-markdown";
  import { markedKatex } from "@humanspeak/svelte-markdown/extensions/katex";
  import { markedMermaid } from "@humanspeak/svelte-markdown/extensions/mermaid";
  import { webUrl } from "$lib/api/web-links";
  import { request } from "$lib/api/client";
  import type { WebSourceSummary } from "$lib/api/wire.generated";
  import SourceLink from "./SourceLink.svelte";
  import CodeBlock from "./CodeBlock.svelte";
  import Diagram from "$lib/features/replies/Diagram.svelte";
  import Math from "$lib/features/replies/Math.svelte";
  import { chartBlocks, decodeChart } from "$lib/features/replies/chart";
  import { VISUALS, type VisualContext } from "$lib/features/replies/context";
  import "$lib/features/replies/visuals.css";
  let {
    source,
    complete = true,
    turnId = "",
  }: { source: string; complete?: boolean; turnId?: string } = $props();
  const charts = $derived(
    complete ? chartBlocks(source) : new Map<string, string | null>(),
  );
  const hasSourceRefs = $derived(
    [...charts.values()].some(
      (body) => body && decodeChart("kejtu-chart", body)?.sourceRefs?.length,
    ),
  );
  let sources = $state<WebSourceSummary[]>([]);
  setContext<VisualContext>(VISUALS, {
    turnId: () => turnId,
    complete: () => complete,
    charts: () => charts,
    sources: () => sources,
  });
  $effect(() => {
    const id = turnId,
      needed = complete && hasSourceRefs;
    let current = true;
    sources = [];
    // Read existing receipts only when a completed chart asks for attribution.
    // A model-supplied URL or another turn's ID never creates source authority.
    if (needed && /^[a-f0-9]{32}$/.test(id))
      void request("web.sources", { turn_id: id })
        .then((value) => {
          if (current)
            sources = value.items.filter((item) =>
              ["ok", "partial"].includes(item.status),
            );
        })
        .catch(() => {
          /* Chart retains exact values and an unavailable-source notice. */
        });
    return () => {
      current = false;
    };
  });
  let renderer = $state<ReturnType<typeof SvelteMarkdown>>();
  const seed = untrack(() => source);
  let previous = seed;
  // The same incremental parser lives through completion. No final flush,
  // replacement renderer, terminal timer, or stats-dependent text update.
  $effect(() => {
    const next = source,
      instance = renderer;
    if (!instance || next === previous) return;
    untrack(() => {
      if (next.startsWith(previous))
        instance.writeChunk({
          offset: previous.length,
          value: next.slice(previous.length),
        });
      else instance.resetStream(next);
      previous = next;
    });
  });
  const renderers = {
    link: SourceLink,
    code: CodeBlock,
    image: null,
    inlineKatex: Math,
    blockKatex: Math,
    mermaid: Diagram,
    html: allowHtmlOnly([]),
  };
  const extensions = [markedKatex(), markedMermaid()];
</script>

<div class="rich-visuals">
  <SvelteMarkdown
    source={seed}
    bind:this={renderer}
    streaming
    {renderers}
    {extensions}
    sanitizeUrl={(value) => webUrl(value) ?? ""}
    sanitizeAttributes={() => ({})}
  />
</div>
