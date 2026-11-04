<script lang="ts">
  import type { Turn } from "$lib/api/wire.generated";
  export let turn: Turn;
  $: usage = turn.status !== "running" ? turn.context_usage : null;
  $: used = usage ? usage.input_tokens + usage.output_tokens : null;
  $: label =
    usage && used !== null
      ? `Latest request context: ${used.toLocaleString()} of ${usage.context_limit.toLocaleString()} tokens (${Math.round((used / usage.context_limit) * 100)}%). Details in the message menu.`
      : "Context usage is not measured. Details in the message menu.";
</script>

<div class="context-line">
  {#if usage && used !== null}
    <meter
      min="0"
      max={usage.context_limit}
      value={used}
      aria-label={label}
      title={label}
    ></meter>
  {:else}
    <span class="unknown" role="img" aria-label={label} title={label}></span>
  {/if}
</div>

<style>
  .context-line {
    display: flex;
    align-items: center;
    min-height: 12px;
    margin-top: 4px;
  }
  meter,
  .unknown {
    appearance: none;
    -webkit-appearance: none;
    display: block;
    width: 48px;
    height: 2px;
    border: 0;
    border-radius: 2px;
    background: var(--ke-accent-soft);
  }
  .unknown {
    opacity: 0.55;
  }
  meter::-webkit-meter-bar {
    background: var(--ke-accent-soft);
    border: 0;
    height: 2px;
    border-radius: 2px;
  }
  meter::-webkit-meter-optimum-value {
    background: var(--ke-accent);
    border-radius: 2px;
  }
  meter::-moz-meter-bar {
    background: var(--ke-accent);
  }
</style>
