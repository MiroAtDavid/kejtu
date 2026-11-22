<script lang="ts">
  import { userAvatar } from "./user-avatar";
  import { rotatingKejtu } from "./theme";
  import KejtuMark from "./KejtuMark.svelte";
  import { replyState, type KejtuGesture } from "./kejtu-state";
  import { spinOnDoubleClick } from "./companion";

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

  // A reply that took this long ends with a hop instead of the blink.
  const LONG_REPLY = 30_000;
  let mark: { play: (gesture: KejtuGesture) => Promise<void> } | undefined;
  $: spin =
    role === "assistant" && $rotatingKejtu
      ? (gesture: KejtuGesture) => mark?.play(gesture)
      : null;
</script>

<span
  class="chat-avatar"
  role={role === "user" && !$userAvatar ? undefined : "img"}
  aria-hidden={role === "user" && !$userAvatar ? "true" : undefined}
  aria-label={role === "user" ? ($userAvatar ? "You" : undefined) : "Kejtu"}
  use:spinOnDoubleClick={spin}
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
    <KejtuMark
      bind:this={mark}
      {state}
      moves={$rotatingKejtu}
      celebrateAfter={$rotatingKejtu ? LONG_REPLY : 0}
    />
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
