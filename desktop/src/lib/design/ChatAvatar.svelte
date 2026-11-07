<script lang="ts">
  import { userAvatar } from "./user-avatar";
  import { rotatingKejtu } from "./theme";
  import KejtuMark from "./KejtuMark.svelte";
  import { replyState } from "./kejtu-state";

  export let role: "user" | "assistant";
  export let working = false;
  /** The reply phase the native stream reports; it chooses what Kejtu shows. */
  export let phase: string | null | undefined = undefined;

  // With animation off the mark stays still. A finished reply passes its
  // "complete" phase after work stops, which the mark turns into one blink.
  $: state = !$rotatingKejtu
    ? "idle"
    : working
      ? replyState(phase)
      : phase === "complete"
        ? "done"
        : "idle";
</script>

<span
  class="chat-avatar"
  role={role === "user" && !$userAvatar ? undefined : "img"}
  aria-hidden={role === "user" && !$userAvatar ? "true" : undefined}
  aria-label={role === "user" ? ($userAvatar ? "You" : undefined) : "Kejtu"}
>
  {#if role === "user"}
    {#if $userAvatar}<img
        src={$userAvatar}
        width="24"
        height="24"
        alt=""
        draggable="false"
      />{/if}
  {:else}
    <KejtuMark {state} />
  {/if}
</span>

<style>
  /* Fixed dimensions keep history/stream measurements independent of image load. */
  .chat-avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    flex: none;
    color: var(--ke-accent);
    user-select: none;
  }
  img {
    display: block;
    width: 24px;
    height: 24px;
    object-fit: contain;
    border-radius: var(--ke-radius-sm);
  }
</style>
