<script lang="ts">
  import { onMount } from "svelte";
  import { identity } from "$lib/api/client";
  import { ReminderAccessJournal, type Status, type Receipt } from "./access";

  export let stateId: string;
  export let conversationId: string;
  export let busy = false;
  export let recoveryOnly = false;
  export let locked = false;
  // The parent keys by archive/chat. A late reply cannot change another chat.
  const journal = new ReminderAccessJournal(stateId, conversationId);
  let current: Status | null = null;
  let working = false,
    ready = false,
    pending = false,
    absent = false,
    destroyed = false;
  let error = "",
    notice = "";
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
      ? "Reminder access change confirmed. No reminder was created by this switch."
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
  async function change() {
    if (!current || !ready || busy || journal.pending) return;
    await finish(
      await journal.begin({
        action: "set",
        conversation_id: conversationId,
        enabled: !current.enabled,
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

<section aria-label="Conversation reminder access">
  <p class="panel-intro">
    Enable once for this conversation, then ask Kejtu to create, change or
    cancel a reminder. Reminders are separate from personal tasks and do not
    require note-writing permission.
  </p>
  <p>
    Chat reminders: <strong
      >{current ? (current.enabled ? "on" : "off") : "unavailable"}</strong
    >.
  </p>
  <p role="note">
    Creation only for now: timed notifications are not connected yet. Saving a
    reminder does not mean you will receive an alert. Scheduling runs only while
    Kejtu is open.
  </p>
  {#if current && !current.readable}<p>
      Memory permission is unavailable. Reminder access cannot enable it.
    </p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  <div class="actions">
    {#if pending}
      <p>
        Resolve this permission change before sending a message. Nothing is
        automatically retried.
      </p>
      <button
        disabled={working}
        on:click={() => run(async () => finish(await journal.resolve()))}
        >Resolve reminder access change</button
      >
      {#if absent}
        <button
          disabled={working || busy || recoveryOnly}
          on:click={() => run(async () => finish(await journal.retry()))}
          >Retry same reminder access change</button
        >
        <button
          disabled={working}
          on:click={() =>
            run(async () => {
              journal.release();
              await read();
            })}>Release uncommitted reminder access choice</button
        >
      {/if}
    {:else if !recoveryOnly}
      <button
        disabled={!ready ||
          working ||
          busy ||
          !current ||
          (!current.readable && !current.enabled)}
        on:click={() => run(change)}
        >{current?.enabled
          ? "Disable chat reminders"
          : "Enable chat reminders"}</button
      >
      <button disabled={working} on:click={() => run(load)}
        >Refresh reminder access</button
      >
    {/if}
  </div>
</section>

<style>
  section {
    margin: 0;
    max-width: 42rem;
  }
  p {
    margin: 0 0 10px;
  }
  [role="note"] {
    padding: 12px 14px;
    border-left: 3px solid var(--ke-border-strong);
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
</style>
