<script lang="ts">
  import { onMount } from "svelte";
  import type { ChatNotice } from "$lib/features/reminders/messages";
  export let item: ChatNotice;
  export let visible = true;
  export let disabled = false;
  export let onAction: (
    item: ChatNotice,
    action: "present" | "later" | "dismiss",
  ) => Promise<void>;
  let node: HTMLElement;
  let exposed = false;
  let focused = false;
  let mounted = false;
  let working = false;
  let attempted = "";
  let error = "";
  $: token = `${item.id}:${item.reminder?.revision ?? item.delivery?.receipt ?? "shown"}`;
  $: if (
    mounted &&
    visible &&
    exposed &&
    focused &&
    !disabled &&
    !working &&
    item.pending &&
    attempted !== token
  ) {
    attempted = token;
    void act("present");
  }
  async function act(action: "present" | "later" | "dismiss") {
    if (working || disabled) return;
    working = true;
    error = "";
    try {
      await onAction(item, action);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      working = false;
    }
  }
  function focus() {
    focused = document.visibilityState === "visible" && document.hasFocus();
  }
  onMount(() => {
    mounted = true;
    focus();
    const observer = new IntersectionObserver(
      (entries) => {
        exposed =
          entries[0]?.isIntersecting && entries[0].intersectionRatio >= 0.5;
      },
      { threshold: [0, 0.5] },
    );
    observer.observe(node);
    document.addEventListener("visibilitychange", focus);
    window.addEventListener("focus", focus);
    window.addEventListener("blur", focus);
    return () => {
      mounted = false;
      observer.disconnect();
      document.removeEventListener("visibilitychange", focus);
      window.removeEventListener("focus", focus);
      window.removeEventListener("blur", focus);
    };
  });
</script>

<article
  bind:this={node}
  class="chat-notice"
  aria-label={item.kind === "reminder" ? "Reminder" : "Check-in"}
  data-message-id={item.id}
>
  <div class="label">
    {item.kind === "reminder" ? "Reminder" : "Check-in"}<span>
      · {new Date(item.at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })}</span
    >
  </div>
  <p class="message">{item.text}</p>
  {#if item.dismissed}<span class="state">Dismissed</span>
  {:else if item.available}
    <div class="actions">
      {#if item.reminder}<button
          type="button"
          disabled={disabled || working}
          on:click={() => act("later")}>Later · 30 min</button
        >{/if}
      <button
        type="button"
        disabled={disabled || working}
        on:click={() => act("dismiss")}>Dismiss</button
      >
    </div>
  {/if}
  {#if error}<p class="message-error">{error}</p>{/if}
</article>

<style>
  .chat-notice {
    margin: 1.5rem 0 2rem;
    padding: 0.9rem 1.15rem;
    border-left: 2px solid var(--ke-border);
    color: var(--ke-text-primary);
    max-width: 68ch;
  }
  .label,
  .state {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .label {
    letter-spacing: 0.02em;
  }
  .message {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: var(--ke-text-sm);
    line-height: 1.7;
    margin: 0.6rem 0;
  }
  .actions {
    display: flex;
    gap: 1rem;
  }
  button {
    border: 0;
    background: transparent;
    padding: 0.3rem 0;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }
  button:hover {
    color: var(--ke-text-primary);
    text-decoration: underline;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .message-error {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
</style>
