<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import { openWebLink } from "$lib/api/web-links";
  import type { McpSignInStatus, Arguments } from "$lib/api/wire.generated";

  export let server: string;
  export let revision: number;
  export let disabled = false;
  export let busy = false;
  export let onChanged: () => Promise<void> = async () => {};
  /** The package flow resumes only after this sign-in finishes successfully. */
  export let onConnected: () => Promise<void> = async () => {};
  export let setup = false;
  let state: McpSignInStatus | null = null;
  let error = "";
  let mounted = true;
  let polling = false;
  $: pending =
    !!state && ["waiting", "exchanging", "ready"].includes(state.stage);

  async function act(action: Arguments["mcp.sign_in"]["action"]) {
    if (busy || (disabled && action !== "cancel")) return;
    busy = true;
    error = "";
    let changed = false;
    let connected = false;
    try {
      const args =
        action === "start" || action === "disconnect"
          ? { server, action, expected_revision: state?.revision ?? revision }
          : { server, action };
      const result = await request("mcp.sign_in", args);
      changed = action !== "cancel";
      if (!mounted) return;
      state = result;
      connected = action === "finish" && result.connected;
      if (action === "start" && result.authorization_url)
        await openWebLink(result.authorization_url);
    } catch (caught) {
      if (mounted)
        error =
          caught instanceof Error
            ? caught.message
            : "Account sign-in could not complete.";
    } finally {
      busy = false;
      // Refresh the existing connection owner after registration as well as finish:
      // either may change its revision, enabled state, or tool choices.
      if (changed && mounted) await onChanged();
      if (connected && mounted) await onConnected();
    }
  }
  async function poll() {
    if (!mounted || busy || polling) return;
    polling = true;
    try {
      const value = await request("mcp.sign_in.read", { server });
      if (!mounted || busy) return;
      state = value;
      if (value.stage === "ready" && !disabled && !error) await act("finish");
    } catch (caught) {
      if (mounted)
        error =
          caught instanceof Error
            ? caught.message
            : "Sign-in status is unavailable.";
    } finally {
      polling = false;
    }
  }
  async function reopen() {
    if (!state?.authorization_url) return;
    try {
      await openWebLink(state.authorization_url);
    } catch {
      error =
        "The browser could not be opened. Check your default browser and try again.";
    }
  }
  onMount(() => {
    void poll();
    const timer = setInterval(() => {
      void poll();
    }, 1000);
    return () => clearInterval(timer);
  });
  onDestroy(() => {
    mounted = false;
  });
</script>

<section aria-label="Account sign-in">
  <p class="hint">
    If this service requires an account, sign in through your browser. Your
    login is kept in this computer’s keyring.
  </p>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if state?.message}<p role="status">{state.message}</p>{/if}
  {#if pending}
    <p role="status">
      {state?.stage === "ready"
        ? "Sign-in verified. Waiting for current work to finish…"
        : state?.stage === "exchanging"
          ? "Verifying sign-in…"
          : "Complete sign-in in your browser."}
    </p>
    <div class="actions">
      {#if state?.stage === "ready" && error}
        <button disabled={busy || disabled} on:click={() => act("finish")}
          >Finish connecting</button
        >
      {/if}
      {#if state?.authorization_url}<button on:click={reopen}
          >Open sign-in page</button
        >{/if}
      <button disabled={busy} on:click={() => act("cancel")}
        >Cancel sign-in</button
      >
    </div>
  {:else}
    {#if state?.connected}<p class="hint">
        {setup
          ? "Sign-in saved. Continuing package setup…"
          : "Sign-in saved. Check the connection and choose its tools below."}
        Disconnecting here removes this computer’s login; account access can also
        be revoked at the service.
      </p>{/if}
    <div class="actions">
      <button
        class:primary={setup && !state?.connected}
        disabled={busy || disabled}
        on:click={() => act("start")}
        >{state?.connected ? "Sign in again" : "Sign in with browser"}</button
      >
      {#if state?.connected}<button
          disabled={busy || disabled}
          on:click={() => act("disconnect")}>Disconnect account</button
        >{/if}
    </div>
  {/if}
</section>

<style>
  section {
    margin: 16px 0;
  }
  .hint {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  .error {
    color: var(--ke-danger);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  button {
    font: inherit;
    padding: 8px 12px;
    color: inherit;
    background: var(--ke-surface);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
