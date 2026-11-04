<script lang="ts">
  import katex from "katex";
  import "katex/dist/katex.min.css";
  let { text, displayMode = false }: { text: string; displayMode?: boolean } =
    $props();
  // Render mathematical markup only; no trusted HTML commands or remote links.
  const html = $derived(
    text.length <= 12000
      ? katex.renderToString(text, {
          displayMode,
          throwOnError: false,
          trust: false,
          maxExpand: 1000,
          maxSize: 20,
        })
      : null,
  );
</script>

{#if html}{@html html}{:else}<code>{text}</code>{/if}
