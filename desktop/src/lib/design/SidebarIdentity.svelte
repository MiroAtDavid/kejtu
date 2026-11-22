<script lang="ts">
  import Wordmark from "./Wordmark.svelte";
  import KejtuMark from "./KejtuMark.svelte";
  import type { KejtuGesture } from "./kejtu-state";
  import { companionGesture, spinOnDoubleClick } from "./companion";
  import { chatAppearance } from "./theme";
  export let subtitle = "Your quiet space";
  /** A reply is being worked on: Kejtu stays awake, and the quiet time
   *  before it dozes counts again from when the work ends. */
  export let working = false;

  // The Kejtu that is always there: it says hello on the first Home visit of
  // the day, dozes off while you are away and spins on a double click.
  const AWAY = 5 * 60_000;
  let mark: { play: (gesture: KejtuGesture) => Promise<void> } | undefined;
  let handled = 0;
  $: if (mark && $companionGesture && $companionGesture.at !== handled) {
    handled = $companionGesture.at;
    // A hello asked for before the sidebar was there still plays, but not
    // one from long ago.
    if (Date.now() - $companionGesture.at < 4000)
      void mark.play($companionGesture.gesture);
  }
  $: spin = $chatAppearance.rotateKejtu
    ? (gesture: KejtuGesture) => mark?.play(gesture)
    : null;
</script>

<div class="sidebar-identity">
  <span class="companion-mark" use:spinOnDoubleClick={spin}
    ><KejtuMark
      bind:this={mark}
      size={33}
      moves={$chatAppearance.rotateKejtu}
      sleepAfter={$chatAppearance.rotateKejtu && !working ? AWAY : 0}
    /></span
  >
  <div class="brand-copy">
    <Wordmark bottom={0} />
    <p class="subtitle">{subtitle}</p>
  </div>
</div>

<style>
  /* Keep the companion mark in the classic sidebar identity area. */
  .sidebar-identity {
    display: flex;
    align-items: center;
    gap: 11px;
    margin-bottom: calc(28px * var(--ke-density));
    padding: 4px 0 12px;
  }
  .companion-mark {
    display: block;
    flex: none;
    width: 33px;
    height: 33px;
    color: var(--ke-accent);
  }
  .subtitle {
    margin: 0;
    margin-top: 3px;
    font: 8px/1.5 var(--ke-font-ui);
    line-height: 1.5;
    color: var(--ke-text-secondary);
    text-transform: uppercase;
    letter-spacing: 1.25px;
  }
</style>
