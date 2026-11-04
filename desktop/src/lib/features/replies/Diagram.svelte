<script lang="ts">
  import { getContext, onMount } from "svelte";
  import DOMPurify from "dompurify";
  import { VISUALS, type VisualContext } from "./context";
  import CodeBlock from "../chat/CodeBlock.svelte";
  import { renderDiagram } from "./diagram";
  let { text }: { text: string } = $props();
  const context = getContext<VisualContext>(VISUALS);
  let theme = $state("default");
  let svg = $state("");
  let failed = $state(false);
  onMount(() => {
    const update = () => {
      theme =
        getComputedStyle(document.documentElement).colorScheme === "dark"
          ? "dark"
          : "default";
    };
    update();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "style", "class"],
    });
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  });
  $effect(() => {
    const body = text,
      mode = theme,
      complete = context?.complete() ?? true;
    let cancelled = false;
    if (!complete) {
      svg = "";
      return;
    }
    failed = false;
    void renderDiagram(body, mode)
      .then((result) => {
        if (!cancelled)
          svg = DOMPurify.sanitize(result, {
            USE_PROFILES: { svg: true, svgFilters: true },
            FORBID_TAGS: ["foreignObject", "image", "a", "script", "use"],
            FORBID_ATTR: ["href", "xlink:href", "onload"],
          });
      })
      .catch(() => {
        if (!cancelled) {
          failed = true;
          svg = "";
        }
      });
    return () => {
      cancelled = true;
    };
  });
</script>

{#if failed || !(context?.complete() ?? true)}<CodeBlock
    lang="mermaid"
    {text}
  />
{:else if svg}<figure class="rich-diagram">
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      class="rich-diagram-scroll"
      tabindex="0"
      role="region"
      aria-label="Diagram; scroll horizontally if needed"
    >
      {@html svg}
    </div>
    <details>
      <summary>Diagram source</summary>
      <CodeBlock lang="mermaid" {text} />
    </details>
  </figure>
{:else}<p class="rich-pending">The diagram follows.</p>{/if}
