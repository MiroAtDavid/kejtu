<script lang="ts">
  import { setContext } from "svelte";
  import SvelteMarkdown, { allowHtmlOnly } from "@humanspeak/svelte-markdown";
  import DocumentImage from "./DocumentImage.svelte";
  import {
    documentAttributes,
    documentUrl,
    documentHtmlTags,
  } from "./document-html";
  import SourceLink from "$lib/features/chat/SourceLink.svelte";
  import CodeBlock from "$lib/features/chat/CodeBlock.svelte";
  import Diagram from "$lib/features/replies/Diagram.svelte";
  import Math from "$lib/features/replies/Math.svelte";
  import { chartBlocks } from "$lib/features/replies/chart";
  import { VISUALS, type VisualContext } from "$lib/features/replies/context";
  import "$lib/features/replies/visuals.css";
  import { previewBlocks, documentExtensions } from "./document-preview";
  import type { EditTarget } from "./document-assist";
  export let source: string;
  export let target: EditTarget | null = null;
  $: blocks = previewBlocks(source);
  $: charts = chartBlocks(source);
  setContext<VisualContext>(VISUALS, {
    turnId: () => "",
    complete: () => true,
    charts: () => charts,
    sources: () => [],
  });
  const renderers = {
    link: SourceLink,
    code: CodeBlock,
    image: DocumentImage,
    inlineKatex: Math,
    blockKatex: Math,
    mermaid: Diagram,
    html: allowHtmlOnly(documentHtmlTags),
  };
</script>

<div class="rich-visuals">
  {#each blocks as block, i (i)}
    <div
      class="document-block"
      class:marked={target?.valid &&
        target.from < block.to &&
        target.to > block.from}
      class:insert-after={target?.valid &&
        target.from === target.to &&
        target.from === block.to}
      data-document-from={block.from}
      data-document-to={block.to}
      data-document-label={block.label}
    >
      <SvelteMarkdown
        source={block.renderSource}
        {renderers}
        extensions={documentExtensions}
        sanitizeUrl={documentUrl}
        sanitizeAttributes={documentAttributes}
      />
    </div>
  {/each}
</div>

<style>
  .document-block {
    display: flow-root;
    border-radius: var(--ke-radius-sm);
  }
  .document-block.marked {
    background: var(--ke-accent-soft);
    box-shadow: -3px 0 var(--ke-marker-blue);
  }
  .document-block.insert-after {
    box-shadow: 0 2px var(--ke-marker-blue);
  }
  .document-block:first-child :global(:first-child) {
    margin-top: 0;
  }
</style>
