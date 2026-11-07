<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import { openWebLink } from "$lib/api/web-links";
  import type { ChatgptStatus, Arguments } from "$lib/api/wire.generated";

  export let disabled = false;
  export let onConnected: () => Promise<void> = async () => {};
  export let onStatus: (connected: boolean) => void = () => {};
  let state: ChatgptStatus | null = null;
  export let busy = false;
  let error = "";
  let mounted = true;
  let polling = false;
  $: active = state?.accounts.find((account) => account.id === state?.active);
  $: pending =
    state && ["waiting", "exchanging", "ready"].includes(state.stage);
  $: onStatus(active?.status === "connected");

  async function act(args: Arguments["models.chatgpt"], launch = false) {
    if (busy || disabled) return;
    busy = true;
    error = "";
    try {
      const result = await request("models.chatgpt", args);
      if (!mounted) return;
      state = result;
      if (launch && result.authorization_url)
        await openWebLink(result.authorization_url);
      if (["finish", "select", "sign_out"].includes(args.action))
        await onConnected();
    } catch (caught) {
      if (mounted)
        error =
          caught instanceof Error
            ? caught.message
            : "ChatGPT connection could not be confirmed.";
    } finally {
      busy = false;
    }
  }
  async function poll() {
    if (polling || busy || !mounted) return;
    polling = true;
    try {
      const value = await request("models.chatgpt.read", {});
      if (!mounted || busy) return;
      state = value;
      if (value.stage === "ready" && !disabled) await act({ action: "finish" });
    } catch (caught) {
      if (mounted)
        error =
          caught instanceof Error
            ? caught.message
            : "ChatGPT connection status is unavailable.";
    } finally {
      polling = false;
    }
  }
  async function link(url: string) {
    try {
      await openWebLink(url);
    } catch {
      error =
        "The browser could not be opened. Check your default browser and try again.";
    }
  }
  onMount(() => {
    void poll();
    const timer = setInterval(() => {
      if (!state || pending) void poll();
    }, 1000);
    return () => clearInterval(timer);
  });
  onDestroy(() => {
    mounted = false;
  });
</script>

<section class="connection" aria-label="ChatGPT connection">
  <h3>Use your ChatGPT plan</h3>
  <p class="muted">
    Connect in your browser. Kejtu keeps its own tools and memory; Codex is not
    required.
  </p>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if state?.message}<p role="status">{state.message}</p>{/if}
  {#if state?.accounts.length}
    <label
      >ChatGPT account
      <select
        value={state.active ?? ""}
        disabled={disabled || busy || !!pending}
        on:change={(event) =>
          act({ action: "select", account_id: event.currentTarget.value })}
      >
        {#each state.accounts as account}
          <option value={account.id}
            >{account.label}{account.status === "signed_out"
              ? " · Signed out"
              : account.status === "permission_needed"
                ? " · Plan usage disabled"
                : ""}</option
          >
        {/each}
      </select>
    </label>
  {/if}
  {#if active?.status === "connected"}
    <p class="muted">
      Connected · Eligible requests use your ChatGPT plan. Account and app
      limits apply.
    </p>
  {/if}
  {#if pending}
    <p role="status">
      {state?.stage === "ready"
        ? "Sign-in verified. Waiting for current work to finish…"
        : state?.stage === "exchanging"
          ? "Verifying your ChatGPT sign-in…"
          : "Complete sign-in in your browser."}
    </p>
    <div class="actions">
      {#if state?.authorization_url}<button
          type="button"
          on:click={() => link(state!.authorization_url!)}
          >Open sign-in page</button
        >{/if}
      <button
        type="button"
        disabled={busy || disabled}
        on:click={() => act({ action: "cancel" })}>Cancel sign-in</button
      >
    </div>
  {:else}
    <div class="actions">
      <button
        class="continue"
        type="button"
        disabled={busy || disabled}
        on:click={() =>
          act(
            { action: "start", ...(active ? { account_id: active.id } : {}) },
            true,
          )}>Continue with ChatGPT</button
      >
      {#if active}
        <button
          type="button"
          disabled={busy || disabled}
          on:click={() => act({ action: "start" }, true)}>Add account</button
        >
        {#if active.status !== "signed_out"}<button
            type="button"
            disabled={busy || disabled}
            on:click={() => act({ action: "sign_out", account_id: active!.id })}
            >Sign out</button
          >{/if}
      {/if}
    </div>
  {/if}
  <button
    class="usage"
    type="button"
    on:click={() => link("https://chatgpt.com/settings/usage")}
    >Manage ChatGPT usage ↗</button
  >
  {#if state?.welcome}
    <div
      class="welcome"
      role="dialog"
      aria-modal="false"
      aria-label="You’re using your ChatGPT plan"
    >
      <h3>You’re using your ChatGPT plan</h3>
      <p>
        Eligible requests in Kejtu use your ChatGPT plan. You can manage Kejtu’s
        usage in ChatGPT settings.
      </p>
      <button
        type="button"
        disabled={busy || disabled}
        on:click={() => act({ action: "welcome_seen" })}>Got it</button
      >
    </div>
  {/if}
</section>

<style>
  .connection {
    margin: 24px 0;
    padding: 20px 0;
    border-block: 1px solid var(--ke-border);
  }
  h3 {
    font-size: var(--ke-text-base);
    font-weight: 500;
  }
  .muted,
  .usage {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  label {
    display: grid;
    gap: 8px;
    margin: 16px 0;
    font-size: var(--ke-text-sm);
  }
  select {
    width: 100%;
    max-width: 100%;
    padding: 8px;
    background: var(--ke-surface);
    color: inherit;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  button {
    font: inherit;
    cursor: pointer;
  }
  .continue {
    background: var(--ke-text-primary);
    color: var(--ke-surface);
    padding: 10px 16px;
    border: 0;
    border-radius: var(--ke-radius-sm);
  }
  .usage {
    margin-top: 16px;
    border: 0;
    padding: 0;
    background: none;
  }
  .welcome {
    border: 1px solid var(--ke-border);
    padding: 16px;
    border-radius: var(--ke-radius-md);
    margin-top: 16px;
  }
  .error {
    color: var(--ke-danger);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
