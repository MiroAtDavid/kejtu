<script lang="ts">
  import { onMount } from "svelte";
  import { identity, request } from "$lib/api/client";
  import {
    isNativeRequest,
    PROTOCOL,
    type Arguments,
    type TaskAccessStatus,
    type TaskAccessReceipt,
  } from "$lib/api/wire.generated";

  export let stateId: string;
  export let conversationId: string;
  export let busy = false;
  export let recoveryOnly = false;
  export let locked = false;
  type Change = Arguments["task.access"];
  // The parent keys this component by state/conversation. The journal and all
  // late replies stay bound to this exact instance's target, never a new chat.
  const target = conversationId;
  const journal = `kejtu-next.task-access.v1.${stateId}.${target}`;
  let current: TaskAccessStatus | null = null;
  let pending: Change | null = null;
  let working = false,
    ready = false,
    absent = false;
  let error = "",
    notice = "";

  $: locked = working || pending !== null;

  async function read() {
    current = null;
    const result = await request("task.access", {
      action: "read",
      conversation_id: target,
    });
    if (!("readable" in result) || result.conversation_id !== target)
      throw new Error("Task access response does not match this conversation.");
    current = result;
    ready = true;
  }
  async function accepted(result: TaskAccessReceipt) {
    if (
      !pending ||
      result.conversation_id !== target ||
      result.mutation_key !== pending.mutation_key ||
      result.enabled !== pending.enabled
    )
      throw new Error(
        "Task access receipt does not match the pending change. Resolve it before trying again.",
      );
    localStorage.removeItem(journal);
    pending = null;
    absent = false;
    notice = "Task access change confirmed.";
    await read(); // An older receipt is not authority for the current grant.
  }
  async function resolve() {
    if (!pending?.mutation_key) return;
    const result = await request("task.access", {
      action: "lookup",
      conversation_id: target,
      mutation_key: pending.mutation_key,
    });
    if ("status" in result && result.status === "absent") {
      absent = true;
      notice =
        "No saved change found. Retry this exact choice or release it and refresh.";
      await read();
    } else if ("mutation_key" in result) await accepted(result);
    else throw new Error("Expected a task access receipt.");
  }
  async function send() {
    if (!pending) return;
    absent = false;
    localStorage.setItem(journal, JSON.stringify(pending));
    const result = await request("task.access", pending);
    if (!("mutation_key" in result))
      throw new Error("Expected a task access receipt.");
    await accepted(result);
  }
  async function change() {
    if (!current || pending || busy) return;
    pending = {
      action: "set",
      conversation_id: target,
      enabled: !current.enabled,
      expected_revision: current.revision,
      mutation_key: identity(),
    };
    await send();
  }
  async function release() {
    if (!absent) return;
    localStorage.removeItem(journal);
    pending = null;
    absent = false;
    notice = "Uncommitted choice released. Choose again from current access.";
    await read();
  }
  async function run(work: () => Promise<void>) {
    if (working) return;
    working = true;
    error = "";
    try {
      await work();
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      working = false;
    }
  }
  async function load() {
    ready = false;
    current = null;
    const stored = localStorage.getItem(journal);
    if (stored !== null) {
      const value = JSON.parse(stored);
      if (
        !isNativeRequest({
          protocol: PROTOCOL,
          request_id: "0".repeat(32),
          operation: "task.access",
          arguments: value,
        }) ||
        value.action !== "set" ||
        value.conversation_id !== target ||
        typeof value.enabled !== "boolean" ||
        typeof value.expected_revision !== "number" ||
        !value.mutation_key
      )
        throw new Error(
          "Task access recovery record is invalid. It was retained; no change was sent.",
        );
      pending = value;
      await resolve();
    } else await read();
  }
  onMount(() => {
    void run(load);
  });
</script>

<section aria-label="Conversation task access">
  <p>
    Model task access in this conversation: <strong
      >{current ? (current.enabled ? "on" : "off") : "unavailable"}</strong
    >.
  </p>
  <p>
    Local task controls always work. Enabling model access keeps the existing
    memory and note-write permissions; it grants no reminders or execution.
  </p>
  {#if current && !current.readable}<p>
      Memory permission is unavailable. Task access cannot enable it.
    </p>
  {:else if current && !current.writable}<p>
      Model task access is read-only while note writing is off.
    </p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  <div class="actions">
    {#if pending}
      <button disabled={working} on:click={() => run(resolve)}
        >Resolve task access change</button
      >
      {#if absent}
        <button
          disabled={working || busy || recoveryOnly}
          on:click={() => run(send)}>Retry same access change</button
        >
        <button disabled={working} on:click={() => run(release)}
          >Release uncommitted access choice</button
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
          ? "Disable model task access"
          : "Enable model task access"}</button
      >
      <button disabled={working} on:click={() => run(load)}
        >Refresh task access</button
      >
    {/if}
  </div>
</section>

<style>
  section {
    border-bottom: 1px solid var(--ke-border);
    margin-bottom: 1rem;
    padding-bottom: 0.75rem;
  }
  p {
    margin: 0.4rem 0;
  }
  button {
    margin: 0.25rem 0.4rem 0.25rem 0;
  }
</style>
