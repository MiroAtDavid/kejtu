<script lang="ts">
  import type { Inspection } from "$lib/api/wire.generated";
  export let items: Inspection[];
  export let complete: boolean;
  $: attempts = items.filter((item) => item.kind === "model_attempt");
  $: measuredInput = attempts.reduce(
    (total, item) => total + (item.input_tokens ?? 0),
    0,
  );
  $: measuredOutput = attempts.reduce(
    (total, item) => total + (item.output_tokens ?? 0),
    0,
  );
  $: unknown = attempts.some(
    (item) => item.input_tokens === null || item.output_tokens === null,
  );
</script>

<section aria-label="Context accounting">
  <p>
    Cumulative usage{complete ? "" : " (loaded inspection pages only)"}:
    {measuredInput} input + {measuredOutput} output tokens
    {unknown ? "— incomplete: some calls have unknown usage." : "."}
    These totals are not context occupancy.
  </p>
  {#each items as item}
    {#if item.kind === "context_accounting"}
      {@const attempt = attempts.find(
        (value) => value.ordinal === item.ordinal,
      )}
      <p>
        Request {item.ordinal} ({item.phase}):
        {attempt?.input_tokens ?? item.request_input} input
        {attempt?.input_tokens == null
          ? `— ${item.method} (estimate)`
          : "(provider measured)"};
        {item.generation_reserved} tokens reserved for reply, including reasoning;
        {item.safety_margin + item.framing_allowance} margin/framing; window {item.context_limit}.
      </p>
    {:else if item.kind === "context_checkpoint"}
      <p>
        Compression ({item.phase}): {item.status}. Originals retained; summary
        is fallible working context.
      </p>
    {/if}
  {/each}
</section>
