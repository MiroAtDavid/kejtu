<script lang="ts">
  import ChatAvatar from "$lib/design/ChatAvatar.svelte";
  import { rotatingKejtu } from "$lib/design/theme";
  // Keep progress separate from the fixed cancellation target as labels change.
  export let label = "Preparing a response…";
  export let stopping = false;
  export let onStop: (() => void) | undefined = undefined;
  export let showMark = true;
  export let paused = false;
</script>

<div class="stream-status">
  <div
    class="searching-mark"
    role="status"
    aria-live="polite"
    aria-atomic="true"
  >
    {#if showMark}
      {#if $rotatingKejtu}<span class="activity-mark" aria-hidden="true">
          <ChatAvatar role="assistant" working={!paused} />
        </span>
      {:else}<span class="activity-dots" class:paused aria-hidden="true">
          <span class="dot"></span><span class="dot"></span><span class="dot"
          ></span>
        </span>{/if}
    {/if}
    <span class="activity-label">{stopping ? "Stopping…" : label}</span>
  </div>
  {#if onStop}
    <button
      class="stop-reply"
      type="button"
      aria-label="Stop"
      title="Stop generating"
      disabled={stopping}
      on:click={onStop}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor" />
      </svg>
    </button>
  {/if}
</div>

<style>
  .stream-status {
    display: flex;
    flex: 1;
    min-width: 0;
    align-items: center;
    gap: var(--ke-space-3);
  }
  .stop-reply {
    border: 0;
    background: transparent;
    color: var(--ke-text-tertiary);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    margin-left: auto;
    padding: 0;
    border-radius: var(--ke-radius-sm);
    cursor: pointer;
    flex-shrink: 0;
  }
  .stop-reply:hover {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .stop-reply:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .searching-mark {
    height: 2rem;
    display: inline-flex;
    align-items: center;
    gap: var(--ke-space-3);
    min-width: 0;
    flex: 1;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }
  .activity-mark {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
  }
  .stop-reply:focus-visible {
    outline: 2px solid var(--ke-border-strong);
    outline-offset: 2px;
  }
  .activity-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* The original three-dot activity indicator. Reduced motion and unavailable
     progress stay still; requesting Stop is not proof that work has stopped. */
  .activity-dots {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }
  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--ke-text-secondary);
    animation: searching 1.1s ease-in-out infinite;
  }
  .dot:nth-child(2) {
    animation-delay: 120ms;
  }
  .dot:nth-child(3) {
    animation-delay: 240ms;
  }
  .paused .dot {
    animation: none;
  }
  @media (prefers-reduced-motion: reduce) {
    .dot {
      animation: none;
    }
  }
  @keyframes searching {
    0%,
    70%,
    100% {
      opacity: 0.35;
      transform: translateY(0);
    }
    35% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
</style>
