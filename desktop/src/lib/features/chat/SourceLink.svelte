<script lang="ts">
  import type { Snippet } from "svelte";
  import { webUrl, openWebLink } from "$lib/api/web-links";
  let {
    href,
    attributes,
    children,
  }: {
    href?: string;
    attributes?: Record<string, string>;
    children?: Snippet;
  } = $props();
  const destination = $derived(webUrl(href ?? attributes?.href));
  let error = $state("");
  function follow(event: MouseEvent) {
    if (event.type === "auxclick" ? event.button !== 1 : event.button !== 0)
      return;
    event.preventDefault();
    event.stopPropagation();
    if (destination)
      void openWebLink(destination)
        .then(() => (error = ""))
        .catch(() => (error = "Could not open this source."));
  }
</script>

{#if destination}
  <a
    href={destination}
    title={destination}
    target="_blank"
    rel="noopener noreferrer"
    onclick={follow}
    onauxclick={follow}>{@render children?.()}</a
  >
{:else}{@render children?.()}{/if}
{#if error}<span role="status">{error}</span>{/if}
