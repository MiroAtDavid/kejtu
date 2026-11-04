<script lang="ts">
  import { onMount } from "svelte";
  import { identity } from "$lib/api/client";
  import { PeopleAccessJournal, type Status, type Receipt } from "./access";

  export let stateId: string;
  export let conversationId: string;
  export let busy = false;
  export let recoveryOnly = false;
  export let locked = false;
  // The parent keys by archive/chat. A late reply cannot change another chat.
  const journal = new PeopleAccessJournal(stateId, conversationId);
  let current: Status | null = null;
  let working = false,
    ready = false,
    pending = false,
    absent = false,
    destroyed = false;
  let error = "",
    notice = "";
  const labels = {
    off: "Off",
    read: "Recall only",
    write: "Recall and remember",
  };
  $: locked = working || pending;

  async function read() {
    current = null;
    ready = false;
    const value = await journal.read();
    if (!destroyed) {
      current = value;
      ready = true;
    }
  }
  async function finish(receipt: Receipt | null) {
    if (destroyed) return;
    notice = receipt
      ? "People access updated."
      : "No saved change found. Retry this exact choice or release it and refresh.";
    await read(); // A past mutation receipt is not current permission authority.
  }
  async function load() {
    ready = false;
    current = null;
    const receipt = await journal.restore();
    if (receipt || journal.pending) await finish(receipt);
    else await read();
  }
  async function change(mode: "off" | "read" | "write") {
    if (!current || !ready || busy || journal.pending) return;
    await finish(
      await journal.begin({
        action: "set",
        conversation_id: conversationId,
        mode,
        expected_revision: current.revision,
        mutation_key: identity(),
      }),
    );
  }
  async function run(action: () => Promise<void>) {
    if (working) return;
    working = true;
    error = "";
    try {
      await action();
    } catch (caught) {
      if (!destroyed)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (!destroyed) {
        working = false;
        pending = journal.pending !== null;
        absent = journal.absent;
      }
    }
  }
  onMount(() => {
    void run(load);
    return () => {
      destroyed = true;
      locked = false;
    };
  });
</script>

<section aria-label="Conversation people access">
  <p class="panel-intro">
    Remember people you mention and the relationships you describe, with links
    to your original words. This permission is separate from notebook writing
    and applies to this conversation.
  </p>
  <p>
    Access: <strong>{current ? labels[current.mode] : "Unavailable"}</strong>
  </p>
  {#if current && !current.available}<p>
      Conversation memory access is required.
    </p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  <div class="actions">
    {#if pending}
      <button
        disabled={working}
        on:click={() => run(async () => finish(await journal.resolve()))}
        >Resolve people access change</button
      >
      {#if absent}
        <button
          disabled={working || busy || recoveryOnly}
          on:click={() => run(async () => finish(await journal.retry()))}
          >Retry same choice</button
        >
        <button
          disabled={working}
          on:click={() =>
            run(async () => {
              journal.release();
              await read();
            })}>Release uncommitted choice</button
        >
      {/if}
    {:else if !recoveryOnly}
      {#each ["off", "read", "write"] as mode}
        <button
          disabled={!ready ||
            working ||
            busy ||
            !current ||
            current.mode === mode ||
            (!current.available && mode !== "off")}
          on:click={() => run(() => change(mode as "off" | "read" | "write"))}
        >
          {mode === "off"
            ? "Off"
            : mode === "read"
              ? "Recall only"
              : "Recall and remember"}
        </button>
      {/each}
      <button
        disabled={working || busy || recoveryOnly}
        on:click={() => run(read)}>Refresh access</button
      >
    {/if}
  </div>
</section>

<style>
  p {
    margin: 0 0 var(--ke-space-4);
    max-width: 42rem;
    line-height: 1.6;
  }
</style>
