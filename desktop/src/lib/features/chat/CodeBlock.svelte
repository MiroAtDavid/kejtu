<script lang="ts">
  import CopyButton from "./CopyButton.svelte";
  import { getContext } from "svelte";
  import { VISUALS, type VisualContext } from "$lib/features/replies/context";
  import { decodeChart } from "$lib/features/replies/chart";
  import ChartBlock from "$lib/features/replies/ChartBlock.svelte";
  let { lang = "", text = "" }: { lang?: string; text?: string } = $props();
  const context = getContext<VisualContext | undefined>(VISUALS);
  const spec = $derived(context?.complete() ? decodeChart(lang, text) : null);
  const chart = $derived(
    spec && context?.charts().get(spec.id) === text ? spec : null,
  );
</script>

{#if chart}
  {#key text}<ChartBlock
      spec={chart}
      sources={context?.sources() ?? []}
    />{/key}
{:else}
  <div class="code-block">
    <div class="code-tools">
      <span>{lang.split(/\s/)[0]}</span><CopyButton {text} label="Copy code" />
    </div>
    <pre><code>{text}</code></pre>
  </div>
{/if}

<style>
  .code-block {
    margin: 0.8em 0;
    overflow: hidden;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
  }
  .code-tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 8px;
    color: var(--ke-text-tertiary);
    font: 11px var(--ke-font-mono);
  }
  pre {
    margin: 0;
    padding: 0 12px 12px;
    overflow-x: auto;
    white-space: pre;
    font: 12px/1.6 var(--ke-font-mono);
  }
</style>
