<script lang="ts">
  import type { ChartSpec } from "./chart";
  import type { WebSourceSummary } from "$lib/api/wire.generated";
  let {
    spec,
    sources = [],
  }: { spec: ChartSpec; sources?: WebSourceSummary[] } = $props();
</script>

{#await import("./Chart.svelte")}
  <p class="rich-pending">Opening chart…</p>
{:then module}
  <module.default {spec} {sources} />
{:catch}
  <p>The chart could not load. Its original values are shown below.</p>
  <pre>{JSON.stringify(spec.values, null, 2)}</pre>
{/await}
