<script lang="ts">
  import type { Workspace } from "./workspace";
  import Icon from "$lib/design/Icon.svelte";
  export let workspace: Workspace;
  export let floating = false;
  let dismissedError = "";
  let minimizedKey = "";
  let recoveryKey = "";
  $: if (!$workspace.error) dismissedError = "";
  $: pending = $workspace.pending;
  // Ordinary commands stay quiet while in flight. Once recovery is needed,
  // keep its controls visible during subsequent receipt checks.
  $: if (pending && (!$workspace.busy || $workspace.error))
    recoveryKey = pending.key;
  $: player = pending?.action === "media.command";
  $: minimized = floating && !!pending && minimizedKey === pending.key;
</script>

<div class="notifications" class:floating>
  {#if $workspace.error && !pending && $workspace.error !== dismissedError}
    <div class="notification" role="alert">
      <header>
        <strong>Something needs attention</strong><button
          type="button"
          class="dismiss"
          aria-label="Dismiss error"
          on:click={() => (dismissedError = $workspace.error)}
          ><Icon name="close" size={14} /></button
        >
      </header>
      {#if floating}<details>
          <summary>Details</summary>
          <p class="detail">{$workspace.error}</p>
        </details>
      {:else}<p class="detail">{$workspace.error}</p>{/if}
    </div>
  {/if}
  {#if $workspace.notice}
    <div class="notification routine" role="status">
      <p>{$workspace.notice}</p>
      <button
        type="button"
        class="dismiss"
        aria-label="Dismiss notice"
        title="Dismiss notice"
        on:click={() => workspace.clearNotice()}
        ><Icon name="close" size={14} /></button
      >
    </div>
  {/if}
  {#if pending && recoveryKey === pending.key}
    {#if minimized}
      <button
        class="recovery-chip"
        type="button"
        on:click={() => (minimizedKey = "")}
        >{player
          ? "Unconfirmed player request"
          : "Unconfirmed workspace request"}</button
      >
    {:else}
      <div class="notification" role="status">
        <header>
          <strong
            >{player
              ? "Player needs attention"
              : "Workspace needs attention"}</strong
          >
          {#if floating}<button
              type="button"
              class="dismiss"
              aria-label="Minimize recovery notice"
              title="Minimize notice"
              on:click={() => (minimizedKey = pending!.key)}
              ><Icon name="close" size={14} /></button
            >{/if}
        </header>
        <div class="recovery-body">
          <p>
            {player
              ? "The last player command is unconfirmed. You can keep browsing."
              : "A workspace change has an unconfirmed outcome."} Nothing will be
            repeated automatically.
          </p>
          {#if $workspace.error}<div role="alert">
              {#if floating}<details>
                  <summary>Details</summary>
                  <p class="detail">{$workspace.error}</p>
                </details>
              {:else}<p class="detail">{$workspace.error}</p>{/if}
            </div>{/if}
          <div class="actions">
            <button
              type="button"
              disabled={$workspace.busy}
              on:click={() => workspace.resolve()}>Check saved outcome</button
            >
            <button
              type="button"
              disabled={$workspace.busy}
              on:click={() => workspace.clearPending()}
              >Clear if no saved effect</button
            >
          </div>
        </div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .notifications {
    display: grid;
    gap: 8px;
  }
  .floating {
    position: fixed;
    z-index: 60;
    right: max(16px, env(safe-area-inset-right));
    bottom: max(16px, env(safe-area-inset-bottom));
    width: min(360px, calc(100vw - 32px));
    max-height: calc(100dvh - 80px);
    overflow-y: auto;
    pointer-events: none;
    font-family: var(--ke-font-ui);
    font-size: 12px;
    color: var(--ke-text-primary);
  }
  .notification,
  .recovery-chip {
    pointer-events: auto;
    min-width: 0;
    padding: 14px 16px;
    border: 1px solid var(--ke-border);
    border-radius: 12px;
    background: color-mix(
      in srgb,
      var(--ke-accent-soft) 65%,
      var(--ke-surface)
    );
    color: var(--ke-text-primary);
  }
  .floating .notification,
  .floating .recovery-chip {
    box-shadow: 0 4px 16px #00000012;
  }
  header,
  .routine {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  header {
    align-items: center;
    margin-bottom: 6px;
  }
  strong {
    font-size: 12px;
    font-weight: 600;
  }
  p {
    margin: 0;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .routine p {
    max-height: 30dvh;
    overflow-y: auto;
  }
  .recovery-body {
    max-height: 40dvh;
    overflow-y: auto;
  }
  .detail {
    color: var(--ke-text-secondary);
    margin-top: 6px;
    max-height: 25dvh;
    overflow-y: auto;
  }
  details {
    margin-top: 8px;
  }
  summary {
    cursor: pointer;
    color: var(--ke-text-secondary);
  }
  button {
    font: inherit;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .dismiss {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    width: 28px;
    height: 28px;
    min-height: 28px;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .dismiss:hover {
    background: color-mix(in srgb, var(--ke-accent) 10%, transparent);
    color: var(--ke-text-primary);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 12px;
  }
  .actions button {
    padding: 6px 8px;
    border: 1px solid var(--ke-border);
    border-radius: 6px;
    background: transparent;
    color: var(--ke-text-primary);
  }
  .actions button:hover:not(:disabled),
  .recovery-chip:hover {
    background: var(--ke-accent-soft);
  }
  button:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
  .recovery-chip {
    justify-self: end;
    font-size: 11px;
    padding: 8px 12px;
  }
</style>
