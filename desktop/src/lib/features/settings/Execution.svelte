<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import { DEFAULT_EXECUTION } from "$lib/api/wire.generated";
  import type { ExecutionSettings } from "$lib/api/wire.generated";

  export let ownerBusy = false;
  export let busy = false;
  let saved: ExecutionSettings | null = null;
  let toolCalls: number = DEFAULT_EXECUTION.tool_calls;
  let seconds: number | null | undefined = DEFAULT_EXECUTION.timeout_seconds;
  let error = "";
  let notice = "";
  $: dirty =
    saved &&
    (toolCalls !== saved.tool_calls ||
      (seconds ?? null) !== saved.timeout_seconds);
  $: valid =
    Number.isInteger(toolCalls) &&
    toolCalls >= 1 &&
    toolCalls <= 256 &&
    (seconds == null ||
      (Number.isInteger(seconds) && seconds >= 1 && seconds <= 86400));

  function receive(value: ExecutionSettings) {
    saved = value;
    toolCalls = value.tool_calls;
    seconds = value.timeout_seconds ?? undefined;
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    error = "";
    try {
      receive(await request("execution.read", {}));
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "Settings could not be loaded.";
    } finally {
      busy = false;
    }
  }
  async function save() {
    if (busy || ownerBusy || !saved || !valid || !dirty) return;
    busy = true;
    error = "";
    notice = "";
    try {
      receive(
        await request("execution.save", {
          tool_calls: toolCalls,
          timeout_seconds: seconds ?? null,
          expected_revision: saved.revision,
        }),
      );
      notice = "Saved. Applies to your next message in any conversation.";
    } catch (caught) {
      error =
        (caught instanceof Error
          ? caught.message
          : "Save could not be confirmed.") +
        " Refresh to inspect the saved values before trying again.";
      saved = null;
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    void refresh();
  });
</script>

<div class="execution-panel">
  <h2 class="settings-title">Tools and time</h2>
  <p class="muted">
    Give longer research more room. Changes apply to the next message, including
    in existing conversations.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if saved}
    <fieldset disabled={busy || ownerBusy}>
      <label
        >Tool calls per message<input
          type="number"
          min="1"
          max="256"
          step="1"
          bind:value={toolCalls}
        /></label
      >
      <p class="muted">
        1–256 calls, shared by search, page reading, memory and other enabled
        tools. The final answer has its own model call.
      </p>
      <label
        >Time limit per message (seconds)<input
          type="number"
          min="1"
          max="86400"
          step="1"
          placeholder="Use model profile"
          bind:value={seconds}
        /></label
      >
      <p class="muted">
        Includes thinking, tools and the final answer. Leave empty to use the
        conversation’s model profile. For example, 900 seconds allows 15
        minutes.
      </p>
    </fieldset>
    <p class="muted">
      Stop remains available. More calls or time do not enlarge the model’s
      context window.
    </p>
  {/if}
  <div class="actions">
    <button
      type="button"
      disabled={busy || ownerBusy || !saved || !valid || !dirty}
      on:click={() => void save()}>Save limits</button
    >
    <button type="button" disabled={busy} on:click={() => void refresh()}
      >Refresh</button
    >
  </div>
</div>

<style>
  .execution-panel {
    max-width: 44rem;
  }
  fieldset {
    border: 0;
    margin: 1rem 0;
    padding: 0;
    display: grid;
    gap: 0.65rem;
  }
  label {
    display: grid;
    gap: 0.35rem;
  }
  input {
    width: min(100%, 18rem);
    box-sizing: border-box;
    min-width: 0;
  }
  .muted {
    color: var(--ke-text-secondary);
    line-height: 1.5;
  }
</style>
