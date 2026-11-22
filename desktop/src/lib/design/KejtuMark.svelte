<script module lang="ts">
  // Each mark clips its eyes to its own outline, so clip ids are per mark.
  let marks = 0;
</script>

<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import {
    GESTURE_MS,
    WORKING_STATES,
    type KejtuGesture,
    type KejtuState,
  } from "./kejtu-state";

  /** What Kejtu is doing. See kejtu-state.ts. */
  export let state: KejtuState = "idle";
  /** Rendered size of the outline itself; props may reach slightly beyond it. */
  export let size = 24;
  /** While not working, the eyes look toward the pointer. */
  export let follow = false;
  /** While idle: a blink now and then, sometimes a glance up. */
  export let lively = false;
  /** Doze off after this many ms without pointer or key activity; 0 never.
   *  Any activity wakes it with a small start. */
  export let sleepAfter = 0;
  /** When work this mark saw lasted at least this long (ms), it finishes with
   *  a happy hop instead of the blink; 0 never. */
  export let celebrateAfter = 0;
  /** Whether one-off moves may play at all. Turning it off ends any move,
   *  played or still waiting, at once. */
  export let moves = true;

  const clipId = `kejtu-face-${++marks}`;
  const reducedMotion = () =>
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  // Below this size (the conversation, inline status) props outside the
  // outline (thoughts, trail, page, ink, laptop) shrink to specks that read as
  // dust on the screen. Every state is then shown by the eyes alone, inside
  // the outline.
  const ROOM = 40;
  $: compact = size < ROOM;

  // Soft changes. Each state is held for a moment, so quick tool switches do
  // not flicker. On a change the eyes and body glide from wherever their loop
  // left them back to rest before the next loop starts; nothing snaps.
  const HOLD = 1100;
  const SETTLE = 560;
  let svg: SVGSVGElement;
  let applied: KejtuState = state;
  let changedAt = -Infinity;
  let holdTimer: ReturnType<typeof setTimeout> | undefined;
  let settleTimer: ReturnType<typeof setTimeout> | undefined;
  let settling = false;
  // When the work this mark is watching began, for the hop after long work.
  let workSince: number | null = WORKING_STATES.has(state)
    ? performance.now()
    : null;
  let celebrateTimer: ReturnType<typeof setTimeout> | undefined;

  $: request(state);
  function request(next: KejtuState) {
    clearTimeout(holdTimer);
    if (next === applied) return;
    const wait = changedAt + HOLD - performance.now();
    if (wait > 0) holdTimer = setTimeout(() => void apply(next), wait);
    else void apply(next);
  }
  async function apply(next: KejtuState) {
    const moving =
      svg && !reducedMotion()
        ? [...svg.querySelectorAll<SVGElement>(".move, .eye, .figure")]
        : [];
    const from = moving.map((element) => getComputedStyle(element).transform);
    const now = performance.now();
    if (WORKING_STATES.has(next) && !WORKING_STATES.has(applied))
      workSince = now;
    const long =
      next === "done" &&
      celebrateAfter > 0 &&
      workSince !== null &&
      now - workSince >= celebrateAfter;
    if (!WORKING_STATES.has(next)) workSince = null;
    applied = next;
    changedAt = now;
    clearTimeout(celebrateTimer);
    // After the glide back to rest, so the hop is not held by it. The
    // settings are read again when it is due: either may have been turned off.
    if (long)
      celebrateTimer = setTimeout(
        () => {
          if (moves && celebrateAfter > 0) void play("hop", true);
        },
        moving.length ? SETTLE : 0,
      );
    if (!moving.length) return;
    settling = true;
    await tick();
    moving.forEach((element, index) => {
      if (from[index] && from[index] !== "none" && element.animate)
        element.animate([{ transform: from[index] }, { transform: "none" }], {
          duration: SETTLE,
          easing: "cubic-bezier(0.2, 0, 0, 1)",
        });
    });
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => (settling = false), SETTLE);
  }
  // "Done" blinks only when this mark actually saw the work happen. A reply
  // loaded from history is simply still.
  let sawWork = false;
  $: if (WORKING_STATES.has(applied)) sawWork = true;
  $: shown = applied === "done" && !sawWork ? "idle" : applied;
  $: working = WORKING_STATES.has(shown);

  // Gestures: one move played once over the current state. Playing the same
  // one again restarts it.
  let gesture: KejtuGesture | null = null;
  let gestureTimer: ReturnType<typeof setTimeout> | undefined;
  // Whether the move playing now is the hop after long work.
  let celebrating = false;
  export async function play(next: KejtuGesture, celebration = false) {
    if (reducedMotion() || !moves) return;
    clearTimeout(gestureTimer);
    if (gesture) {
      gesture = null;
      await tick();
      // Let the browser see the move end before it starts again.
      svg?.getBoundingClientRect();
    }
    if (!moves) return;
    gesture = next;
    celebrating = celebration;
    gestureTimer = setTimeout(() => {
      gesture = null;
      celebrating = false;
    }, GESTURE_MS[next]);
  }
  function endMove() {
    clearTimeout(gestureTimer);
    gesture = null;
    celebrating = false;
  }
  // Animation turned off: nothing plays and nothing waits to play.
  $: if (!moves) stopMoves();
  function stopMoves() {
    clearTimeout(celebrateTimer);
    endMove();
  }
  // The hop turned off: a waiting or playing celebration ends; other moves
  // carry on.
  $: if (!celebrateAfter) cancelCelebration();
  function cancelCelebration() {
    clearTimeout(celebrateTimer);
    if (celebrating) endMove();
  }

  // Dozing: only from idle, never during work. Waking is a small start.
  let dozing = false;
  let sleepTimer: ReturnType<typeof setTimeout> | undefined;
  $: visible = dozing && shown === "idle" ? "sleeping" : shown;
  $: if (working || !sleepAfter) dozing = false;
  function arm() {
    clearTimeout(sleepTimer);
    if (sleepAfter > 0)
      sleepTimer = setTimeout(() => (dozing = !working), sleepAfter);
  }
  // Work keeps it awake; the quiet time counts again from when work ends.
  $: if (!working) arm();
  function onActivity() {
    if (!sleepAfter) return;
    if (dozing) {
      dozing = false;
      void play("wake");
    }
    arm();
  }

  // Following the pointer: the eyes lean up to 7 units toward it, less when
  // it is close. Work, gestures and sleep keep their own eyes.
  let gaze = "";
  let pointer: { x: number; y: number } | null = null;
  let frame = 0;
  $: lookFree = follow && !working && !gesture && visible !== "sleeping";
  $: if (!lookFree) gaze = "";
  function onPointer(event: PointerEvent) {
    onActivity();
    if (!follow) return;
    pointer = { x: event.clientX, y: event.clientY };
    if (!frame) frame = requestAnimationFrame(updateGaze);
  }
  function onPointerOut(event: PointerEvent) {
    if (event.relatedTarget) return;
    pointer = null;
    gaze = "";
  }
  function updateGaze() {
    frame = 0;
    if (!svg || !pointer || !lookFree || reducedMotion()) return;
    const box = svg.getBoundingClientRect();
    const dx = pointer.x - (box.left + box.width / 2);
    const dy = pointer.y - (box.top + box.height / 2);
    const distance = Math.hypot(dx, dy);
    if (!distance) return;
    const reach = 7 * Math.min(1, distance / (box.width * 1.5));
    gaze = `translate(${((dx / distance) * reach).toFixed(2)}px, ${((dy / distance) * reach).toFixed(2)}px)`;
  }

  // Window listeners only for marks that follow or doze.
  let listening = false;
  $: listen(follow || sleepAfter > 0);
  function listen(on: boolean) {
    if (typeof window === "undefined" || on === listening) return;
    listening = on;
    const method = on ? "addEventListener" : "removeEventListener";
    window[method]("pointermove", onPointer as EventListener);
    window[method]("pointerout", onPointerOut as EventListener);
    window[method]("pointerdown", onActivity);
    window[method]("keydown", onActivity);
    // Reading by scrolling is being here too.
    window[method]("wheel", onActivity, { passive: true });
    if (on) onActivity();
    else clearTimeout(sleepTimer);
  }

  // Lively: a blink every few seconds while idle, sometimes a glance up.
  let livelyTimer: ReturnType<typeof setTimeout> | undefined;
  $: liven(lively);
  function liven(on: boolean) {
    clearTimeout(livelyTimer);
    if (!on || typeof window === "undefined") return;
    livelyTimer = setTimeout(
      () => {
        if (visible === "idle" && !gesture)
          void play(Math.random() < 0.25 ? "glance" : "blink");
        liven(lively);
      },
      3200 + Math.random() * 4800,
    );
  }

  onDestroy(() => {
    clearTimeout(holdTimer);
    clearTimeout(settleTimer);
    clearTimeout(gestureTimer);
    clearTimeout(sleepTimer);
    clearTimeout(livelyTimer);
    clearTimeout(celebrateTimer);
    if (frame) cancelAnimationFrame(frame);
    listen(false);
  });

  const OUTLINE =
    "M48 8C70 6 88 23 87 47C86 70 70 88 46 87C23 86 7 69 9 45C11 22 27 10 48 8Z";
</script>

<svg
  bind:this={svg}
  class="kejtu-mark"
  class:working
  class:settling
  class:compact
  class:playing={gesture !== null}
  data-state={visible}
  data-gesture={gesture}
  viewBox="0 0 96 96"
  width={size}
  height={size}
  fill="none"
  aria-hidden="true"
>
  <defs><clipPath id={clipId}><path d={OUTLINE} /></clipPath></defs>
  <g class="prop p-sleep"
    ><path class="zee" d="M80 6h6l-6 7h6" /><path
      class="zee"
      d="M80 6h6l-6 7h6"
    /><path class="zee" d="M80 6h6l-6 7h6" /></g
  >
  <g class="prop p-dizzy"
    >{#each [0, 1, 2] as star}<g class="orbit-x" style:--i={star}
        ><g class="orbit-y"
          ><path
            class="star"
            d="M48 -10Q48.9 -4.9 54 -4Q48.9 -3.1 48 2Q47.1 -3.1 42 -4Q47.1 -4.9 48 -10Z"
          /></g
        ></g
      >{/each}</g
  >
  <g class="prop p-love"
    ><path
      class="heart"
      d="M82 16C76 12 74 8.5 74 6C74 3.6 75.8 2 77.8 2C79.6 2 81 3 82 4.6C83 3 84.4 2 86.2 2C88.2 2 90 3.6 90 6C90 8.5 88 12 82 16Z"
    /><path
      class="heart second"
      d="M93 29.9C89.4 27.5 88.2 25.4 88.2 23.9C88.2 22.46 89.28 21.5 90.48 21.5C91.56 21.5 92.4 22.1 93 23.06C93.6 22.1 94.44 21.5 95.52 21.5C96.72 21.5 97.8 22.46 97.8 23.9C97.8 25.4 96.6 27.5 93 29.9Z"
    /></g
  >
  <g class="prop p-think"
    ><circle class="soft-dot" cx="84" cy="9" r="2.8" /><circle
      class="soft-dot"
      cx="94"
      cy="-2"
      r="3.8"
    /><circle class="soft-dot" cx="106" cy="-14" r="5" /></g
  >
  <g class="prop p-remember"><path class="trail" d="M66 60 Q 84 40 91 41" /></g>
  <g class="figure">
    <path class="outline" d={OUTLINE} />
    <g class="face" clip-path="url(#{clipId})"
      ><g class="gaze" style:transform={gaze || null}
        ><g class="look"
          ><g class="move"
            ><circle class="eye big" cx="34" cy="34" r="6" /><circle
              class="eye small"
              cx="63"
              cy="63"
              r="3.5"
            /><g class="happy"
              ><path class="lid big" d="M28.5 36Q34 28.5 39.5 36" /><path
                class="lid small"
                d="M59.8 64.6Q63 60.2 66.2 64.6"
              /></g
            ><g class="sleepy"
              ><path class="lid big" d="M28.5 33.5Q34 39 39.5 33.5" /><path
                class="lid small"
                d="M59.8 62.4Q63 66.4 66.2 62.4"
              /></g
            ></g
          ></g
        ></g
      ></g
    >
  </g>
  <g class="prop p-book">
    <path class="sheet" d="M6 88 Q 27 81 48 91 L 48 117 Q 27 108 6 113 Z" />
    <path class="sheet" d="M48 91 Q 69 81 90 88 L 90 113 Q 69 108 48 117 Z" />
    <path class="line" d="M14 94 Q 27 90 40 96 M14 101 Q 27 97 40 103" />
    <path class="line" d="M56 96 Q 69 90 82 94 M56 103 Q 69 97 82 101" />
  </g>
  <g class="prop p-write"
    ><path
      class="line ink"
      pathLength="100"
      d="M2 106 c5 -7 10 -7 15 0 s10 7 15 0 s10 -7 15 0 s10 7 15 0 s10 -7 15 0 s10 7 15 0"
    /></g
  >
  <g class="prop p-desk">
    <rect class="sheet" x="10" y="58" width="76" height="40" rx="4" />
    <path class="sheet" d="M-2 98 H98 L92 107 H4 Z" />
    <path class="line tick" d="M-12 90 l-6 -4 M-12 98 h-8" />
    <path class="line tick" d="M108 90 l6 -4 M108 98 h8" />
  </g>
</svg>

<style>
  /* One companion mark for every place Kejtu appears. Colour comes from the
     surrounding `color`; small props sit just outside the outline's box. */
  .kejtu-mark {
    display: block;
    overflow: visible;
    flex: none;
  }
  .outline {
    stroke: currentColor;
    stroke-width: 2.5;
    stroke-linejoin: round;
  }
  .eye {
    fill: currentColor;
    transform-box: fill-box;
    transform-origin: center;
    transition: opacity 400ms var(--ke-motion-ease);
  }
  .eye.small {
    opacity: 0.58;
  }
  /* Closed eyes: happy (◠) for gestures, sleepy (◡) for dozing. */
  .lid {
    fill: none;
    stroke: currentColor;
    stroke-width: 3.4;
    stroke-linecap: round;
    opacity: 0;
    transition: opacity 400ms var(--ke-motion-ease);
  }
  .lid.small {
    stroke-width: 2.4;
    stroke-opacity: 0.58;
  }
  /* About a pixel wide at conversation size, or they vanish. */
  .compact .lid {
    stroke-width: 5;
  }
  .compact .lid.small {
    stroke-width: 3.8;
  }
  .move {
    /* Translations ignore the origin; the chase turns round the face's middle. */
    transform-box: view-box;
    transform-origin: 48.5px 48.5px;
  }
  .gaze {
    transition: transform 420ms var(--ke-motion-ease);
  }
  /* The eyes stay inside the outline: a gesture that sends them round the
     back or below the edge is cut by it. Only the remembering fetch at larger
     sizes reaches out. */
  .kejtu-mark:not(.compact)[data-state="remembering"] .face {
    clip-path: none;
  }
  .figure {
    transform-box: fill-box;
    transform-origin: center;
  }
  .look {
    transition: transform 900ms var(--ke-motion-ease);
  }
  .prop {
    opacity: 0;
    transition: opacity 700ms var(--ke-motion-ease);
  }
  .sheet {
    fill: var(--ke-surface);
    stroke: currentColor;
    stroke-width: 2.2;
    stroke-linejoin: round;
  }
  .line {
    fill: none;
    stroke: currentColor;
    stroke-opacity: 0.55;
    stroke-width: 2;
    stroke-linecap: round;
  }
  .soft-dot {
    fill: currentColor;
    opacity: 0.55;
    transform-box: fill-box;
    transform-origin: center;
  }
  .trail {
    fill: none;
    stroke: currentColor;
    stroke-opacity: 0.5;
    stroke-width: 1.6;
    stroke-dasharray: 0.1 5;
    stroke-linecap: round;
  }
  .zee {
    stroke: currentColor;
    stroke-opacity: 0.6;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    opacity: 0;
    transform-box: fill-box;
    transform-origin: center;
  }
  .star {
    fill: currentColor;
    opacity: 0.7;
  }
  .orbit-y {
    transform-box: fill-box;
    transform-origin: center;
  }
  .heart {
    fill: currentColor;
    opacity: 0;
    transform-box: fill-box;
    transform-origin: center bottom;
  }
  /* Gesture props show and hide with their gesture, not with a fade. */
  .p-dizzy,
  .p-love {
    transition: none;
  }

  /* Preparing: the first and often longest wait. Calm, but clearly alive at
     chat size: the eyes sway slowly from side to side while three thoughts
     build up one by one and fade together, slower than real thinking. */
  [data-state="preparing"] .look {
    transform: translate(3px, -4px);
  }
  [data-state="preparing"] .p-think {
    opacity: 1;
  }
  [data-state="preparing"] .move {
    animation: kejtu-ponder-eyes 7s ease-in-out infinite;
  }
  [data-state="preparing"] .p-think .soft-dot {
    animation: kejtu-ponder 6s ease-in-out infinite;
  }
  [data-state="preparing"] .p-think .soft-dot:nth-child(2) {
    animation-name: kejtu-ponder-2;
  }
  [data-state="preparing"] .p-think .soft-dot:nth-child(3) {
    animation-name: kejtu-ponder-3;
  }
  [data-state="preparing"] .figure {
    animation: kejtu-breathe-slow 5s ease-in-out infinite;
  }

  /* Thinking: eyes drift up to the side while small thoughts rise. */
  [data-state="thinking"] .look {
    transform: translate(6px, -6px);
  }
  [data-state="thinking"] .p-think {
    opacity: 1;
  }
  [data-state="thinking"] .move {
    animation: kejtu-drift 4s ease-in-out infinite;
  }
  [data-state="thinking"] .p-think .soft-dot {
    animation: kejtu-thought 2.6s var(--ke-motion-ease) infinite;
  }
  [data-state="thinking"] .p-think .soft-dot:nth-child(2) {
    animation-delay: 0.35s;
  }
  [data-state="thinking"] .p-think .soft-dot:nth-child(3) {
    animation-delay: 0.7s;
  }

  /* Small places: nothing outside the outline, eyes only. Searching, reading,
     writing and the desk already say enough with where the eyes look and how
     they move. Thinking: the small eye slowly turns around its place, like a
     thought being turned over. Remembering: it fetches only as far as the
     outline's edge. */
  .compact .prop {
    display: none;
  }
  .compact[data-state="thinking"] .eye.small {
    animation: kejtu-turn 3.2s linear infinite;
  }
  .compact[data-state="preparing"] .eye.small {
    animation: kejtu-turn 6s linear infinite;
  }
  .compact[data-state="remembering"] .eye.small {
    animation-name: kejtu-fetch-near;
  }

  /* Searching: a slow look to one side, then the other. */
  [data-state="looking"] .move {
    animation: kejtu-glance 3.6s infinite;
  }

  /* Reading: a page in front, eyes moving line by line. */
  [data-state="reading"] .look {
    transform: translate(0, 6px);
  }
  [data-state="reading"] .p-book {
    opacity: 1;
  }
  [data-state="reading"] .move {
    animation: kejtu-scan 3.2s infinite;
  }

  /* Writing: eyes down while a line of ink draws itself. */
  [data-state="writing"] .look {
    transform: translate(3px, 6px);
  }
  [data-state="writing"] .p-write {
    opacity: 1;
  }
  [data-state="writing"] .ink {
    stroke-dasharray: 100;
    animation: kejtu-ink 2.8s infinite;
  }
  [data-state="writing"] .move {
    animation: kejtu-follow 2.8s infinite;
  }

  /* Remembering: the small dot fetches something and brings it back. */
  [data-state="remembering"] .look {
    transform: translate(-2px, -1px);
  }
  [data-state="remembering"] .eye.small {
    animation: kejtu-fetch 3.4s infinite;
  }
  [data-state="remembering"] .eye.big {
    animation: kejtu-watch 3.4s infinite;
  }
  [data-state="remembering"] .p-remember {
    opacity: 1;
  }
  [data-state="remembering"] .trail {
    animation: kejtu-trail 3.4s infinite;
  }

  /* At the desk: long background work. */
  [data-state="desk"] .look {
    transform: translate(0, 4px);
  }
  [data-state="desk"] .p-desk {
    opacity: 1;
  }
  [data-state="desk"] .move {
    animation: kejtu-tap 1.6s ease-in-out infinite;
  }
  [data-state="desk"] .tick {
    animation: kejtu-tick 0.8s steps(1, end) infinite;
  }
  [data-state="desk"] .tick:last-child {
    animation-delay: 0.4s;
  }

  /* Breathing: work without a more specific signal. */
  [data-state="breathing"] .figure {
    animation: kejtu-breathe 3.2s ease-in-out infinite;
  }

  /* Chasing: the eyes chase each other round the inside of the outline, each
     half turn a little rush, stretched by their own speed. */
  [data-state="chasing"] .move {
    animation: kejtu-chase 2.4s infinite;
  }
  [data-state="chasing"] .eye {
    animation: kejtu-chase-smear 1.2s ease-in-out infinite;
  }
  [data-state="chasing"] .figure {
    animation: kejtu-breathe-slow 2.4s ease-in-out infinite;
  }

  /* Done: one blink and a small settle, then still. */
  [data-state="done"] .eye {
    animation: kejtu-blink 700ms var(--ke-motion-ease) 1 both;
  }
  [data-state="done"] .figure {
    animation: kejtu-settle 700ms var(--ke-motion-ease) 1 both;
  }

  /* Sleeping: eyes closed, slow breaths, a few z's drift up. */
  [data-state="sleeping"] .look {
    transform: translate(0, 3px);
  }
  [data-state="sleeping"] .eye {
    opacity: 0;
  }
  [data-state="sleeping"] .sleepy .lid {
    opacity: 1;
  }
  [data-state="sleeping"] .figure {
    animation: kejtu-doze 4.8s ease-in-out infinite;
  }
  [data-state="sleeping"] .p-sleep {
    opacity: 1;
  }
  [data-state="sleeping"] .zee {
    animation: kejtu-zee 4.8s ease-out infinite both;
  }
  [data-state="sleeping"] .zee:nth-child(2) {
    animation-delay: 1.6s;
  }
  [data-state="sleeping"] .zee:nth-child(3) {
    animation-delay: 3.2s;
  }

  /* While a change settles, the eye and body loops wait; the glide back to
     rest is a short script animation. Props start their own loops at once,
     from their invisible first frame. !important beats each state's loop. */
  .settling .move,
  .settling .eye,
  .settling .figure {
    animation: none !important;
  }

  /* Gestures play once over any state; its loop comes back after. Moves that
     squash the body do it from the ground, turns from the middle. */
  .kejtu-mark.playing:is(
      [data-gesture="hop"],
      [data-gesture="hello"],
      [data-gesture="squish"],
      [data-gesture="love"],
      [data-gesture="wake"],
      [data-gesture="spin"]
    )
    .figure {
    transform-box: view-box;
    transform-origin: 48px 87px;
  }
  .kejtu-mark.playing:is(
      [data-gesture="wink"],
      [data-gesture="nod"],
      [data-gesture="roll"]
    )
    .figure {
    transform-box: view-box;
    transform-origin: 48px 47.5px;
  }

  /* Blink: quick, both eyes. */
  .kejtu-mark.playing[data-gesture="blink"] .eye {
    animation: kejtu-blink-quick 380ms ease-in-out both;
  }

  /* Wink: the big eye closes happily while the head tilts. */
  .kejtu-mark.playing[data-gesture="wink"] .figure {
    animation: kejtu-tilt 800ms var(--ke-motion-ease) both;
  }
  .kejtu-mark.playing[data-gesture="wink"] .eye.big {
    animation: kejtu-wink 800ms var(--ke-motion-ease) both;
  }
  .kejtu-mark.playing[data-gesture="wink"] .happy .lid.big {
    animation: kejtu-wink-lid 800ms var(--ke-motion-ease) both;
  }

  /* Glance: something up there caught its eye, then a blink. */
  .kejtu-mark.playing[data-gesture="glance"] .move {
    animation: kejtu-glance-up 1600ms var(--ke-motion-ease) both;
  }
  .kejtu-mark.playing[data-gesture="glance"] .eye {
    animation: kejtu-glance-blink 1600ms both;
  }

  /* Nod: yes, twice. */
  .kejtu-mark.playing[data-gesture="nod"] .figure {
    animation: kejtu-nod 1000ms ease-in-out both;
  }
  .kejtu-mark.playing[data-gesture="nod"] .move {
    animation: kejtu-nod-eyes 1000ms ease-in-out both;
  }

  /* Hop, squish and love close the eyes happily (◠ ◠). */
  .kejtu-mark.playing[data-gesture="hop"] .figure {
    animation: kejtu-hop 1100ms both;
  }
  .kejtu-mark.playing[data-gesture="hop"] .eye {
    animation: kejtu-happy-eyes 1100ms both;
  }
  .kejtu-mark.playing[data-gesture="hop"] .happy .lid {
    animation: kejtu-happy-lids 1100ms both;
  }
  .kejtu-mark.playing[data-gesture="squish"] .figure {
    animation: kejtu-squish 900ms ease-out both;
  }
  .kejtu-mark.playing[data-gesture="squish"] .eye {
    animation: kejtu-happy-eyes 900ms both;
  }
  .kejtu-mark.playing[data-gesture="squish"] .happy .lid {
    animation: kejtu-happy-lids 900ms both;
  }
  .kejtu-mark.playing[data-gesture="love"] .figure {
    animation: kejtu-sway 1700ms ease-in-out both;
  }
  .kejtu-mark.playing[data-gesture="love"] .eye {
    animation: kejtu-happy-eyes 1700ms both;
  }
  .kejtu-mark.playing[data-gesture="love"] .happy .lid {
    animation: kejtu-happy-lids 1700ms both;
  }
  .kejtu-mark.playing[data-gesture="love"] .p-love {
    opacity: 1;
  }
  .kejtu-mark.playing[data-gesture="love"] .heart {
    animation: kejtu-heart 1400ms var(--ke-motion-ease) both;
  }
  .kejtu-mark.playing[data-gesture="love"] .heart.second {
    animation-delay: 300ms;
  }

  /* Hello: a hop up, a wink at the top, a soft landing. */
  .kejtu-mark.playing[data-gesture="hello"] .figure {
    animation: kejtu-hello 1600ms both;
  }
  .kejtu-mark.playing[data-gesture="hello"] .eye.big {
    animation: kejtu-hello-wink 1600ms both;
  }
  .kejtu-mark.playing[data-gesture="hello"] .happy .lid.big {
    animation: kejtu-hello-lid 1600ms both;
  }

  /* Wake: eyes pop open, a small start. */
  .kejtu-mark.playing[data-gesture="wake"] .figure {
    animation: kejtu-wake 900ms var(--ke-motion-ease) both;
  }
  .kejtu-mark.playing[data-gesture="wake"] .eye {
    animation: kejtu-wake-eye 900ms var(--ke-motion-ease) both;
  }

  /* Peekaboo: the eyes duck below the edge, one peeks, then boo. */
  .kejtu-mark.playing[data-gesture="peek"] .move {
    animation: kejtu-peek 2200ms both;
  }
  .kejtu-mark.playing[data-gesture="peek"] .eye {
    animation: kejtu-boo 2200ms both;
  }

  /* Spin: Kejtu turns all the way round. The eyes slide off one side,
     the back of its head goes by, and they come back from the other. */
  .kejtu-mark.playing[data-gesture="spin"] .figure {
    animation: kejtu-spin-body 1700ms ease-in-out both;
  }
  .kejtu-mark.playing[data-gesture="spin"] .move {
    animation: kejtu-spin-eyes 1700ms both;
  }
  .kejtu-mark.playing[data-gesture="spin"] .eye {
    animation: kejtu-spin-squeeze 1700ms both;
  }

  /* Roll: a wound-up full turn, a wobble, and dizzy eyes (and stars where
     there is room). */
  .kejtu-mark.playing[data-gesture="roll"] .figure {
    animation: kejtu-roll 2800ms both;
  }
  .kejtu-mark.playing[data-gesture="roll"] .eye.big {
    animation: kejtu-dizzy-big 2800ms linear both;
  }
  .kejtu-mark.playing[data-gesture="roll"] .eye.small {
    animation: kejtu-dizzy-small 2800ms linear both;
  }
  .kejtu-mark.playing[data-gesture="roll"] .p-dizzy {
    animation: kejtu-dizzy-show 2800ms both;
  }
  .kejtu-mark.playing[data-gesture="roll"] .orbit-x {
    animation: kejtu-orbit-x 0.6s ease-in-out infinite alternate;
    animation-delay: calc(var(--i) * -0.4s);
  }
  .kejtu-mark.playing[data-gesture="roll"] .orbit-y {
    animation: kejtu-orbit-y 0.6s ease-in-out infinite alternate;
    animation-delay: calc(var(--i) * -0.4s - 0.3s);
  }

  @keyframes kejtu-ponder-eyes {
    0%,
    100% {
      transform: none;
    }
    25% {
      transform: translate(-6px, 1px);
    }
    50% {
      transform: translate(0, -1px);
    }
    75% {
      transform: translate(6px, 1px);
    }
  }
  /* Three thoughts appear one after another, stay, then fade together. */
  @keyframes kejtu-ponder {
    0%,
    8% {
      opacity: 0;
      transform: translateY(3px) scale(0.6);
    }
    20%,
    78% {
      opacity: 0.75;
      transform: none;
    }
    92%,
    100% {
      opacity: 0;
      transform: translateY(-2px);
    }
  }
  @keyframes kejtu-ponder-2 {
    0%,
    24% {
      opacity: 0;
      transform: translateY(3px) scale(0.6);
    }
    36%,
    78% {
      opacity: 0.75;
      transform: none;
    }
    92%,
    100% {
      opacity: 0;
      transform: translateY(-2px);
    }
  }
  @keyframes kejtu-ponder-3 {
    0%,
    40% {
      opacity: 0;
      transform: translateY(3px) scale(0.6);
    }
    52%,
    78% {
      opacity: 0.75;
      transform: none;
    }
    92%,
    100% {
      opacity: 0;
      transform: translateY(-2px);
    }
  }
  @keyframes kejtu-breathe-slow {
    0%,
    100% {
      transform: none;
    }
    50% {
      transform: scale(1.03);
    }
  }
  @keyframes kejtu-drift {
    0%,
    100% {
      transform: none;
    }
    50% {
      transform: translate(1.5px, -1.5px);
    }
  }
  /* A small circle around the eye's own place; it stays inside the outline. */
  @keyframes kejtu-turn {
    from {
      transform: rotate(0deg) translateX(5px) rotate(0deg);
    }
    to {
      transform: rotate(360deg) translateX(5px) rotate(-360deg);
    }
  }
  @keyframes kejtu-thought {
    0%,
    8% {
      opacity: 0;
      transform: translateY(4px) scale(0.5);
    }
    30%,
    62% {
      opacity: 0.75;
      transform: none;
    }
    86%,
    100% {
      opacity: 0;
      transform: translateY(-2px);
    }
  }
  @keyframes kejtu-glance {
    0% {
      transform: none;
      animation-timing-function: var(--ke-motion-ease);
    }
    14%,
    34% {
      transform: translate(-8px, 1px);
      animation-timing-function: var(--ke-motion-ease);
    }
    50%,
    70% {
      transform: translate(7px, -3px);
      animation-timing-function: var(--ke-motion-ease);
    }
    86%,
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-scan {
    0% {
      transform: none;
      animation-timing-function: ease-out;
    }
    10% {
      transform: translate(-7px, -1px);
      animation-timing-function: linear;
    }
    44% {
      transform: translate(6px, -1px);
      animation-timing-function: ease-in-out;
    }
    50% {
      transform: translate(-7px, 2px);
      animation-timing-function: linear;
    }
    84% {
      transform: translate(6px, 2px);
      animation-timing-function: ease-in-out;
    }
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-ink {
    0% {
      stroke-dashoffset: 100;
      opacity: 1;
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    70%,
    84% {
      stroke-dashoffset: 0;
      opacity: 1;
    }
    100% {
      stroke-dashoffset: 0;
      opacity: 0;
    }
  }
  @keyframes kejtu-follow {
    0% {
      transform: none;
    }
    8% {
      transform: translate(-6px, 0);
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    70%,
    84% {
      transform: translate(6px, 0);
    }
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-fetch {
    0%,
    10% {
      transform: none;
      opacity: 0.58;
      animation-timing-function: var(--ke-motion-ease);
    }
    40%,
    58% {
      transform: translate(28px, -22px) scale(1.25);
      opacity: 1;
      animation-timing-function: var(--ke-motion-ease);
    }
    90%,
    100% {
      transform: none;
      opacity: 0.58;
    }
  }
  /* The same fetch, kept inside the outline for small places. */
  @keyframes kejtu-fetch-near {
    0%,
    10% {
      transform: none;
      opacity: 0.58;
      animation-timing-function: var(--ke-motion-ease);
    }
    40%,
    58% {
      transform: translate(9px, -14px) scale(1.15);
      opacity: 1;
      animation-timing-function: var(--ke-motion-ease);
    }
    90%,
    100% {
      transform: none;
      opacity: 0.58;
    }
  }
  @keyframes kejtu-watch {
    0%,
    12% {
      transform: none;
      animation-timing-function: var(--ke-motion-ease);
    }
    40%,
    60% {
      transform: translate(4px, -2px);
      animation-timing-function: var(--ke-motion-ease);
    }
    90%,
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-trail {
    0%,
    22% {
      opacity: 0;
    }
    40%,
    60% {
      opacity: 1;
    }
    84%,
    100% {
      opacity: 0;
    }
  }
  @keyframes kejtu-tap {
    0%,
    100% {
      transform: translate(-2px, 0);
    }
    50% {
      transform: translate(2px, 0);
    }
  }
  @keyframes kejtu-tick {
    0% {
      opacity: 1;
    }
    50% {
      opacity: 0;
    }
  }
  @keyframes kejtu-breathe {
    0%,
    100% {
      transform: none;
    }
    50% {
      transform: scale(1.045);
    }
  }
  @keyframes kejtu-blink {
    0% {
      transform: none;
    }
    35% {
      transform: scaleY(0.12);
    }
    70%,
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-settle {
    0% {
      transform: none;
    }
    40% {
      transform: translateY(-2px);
    }
    100% {
      transform: none;
    }
  }

  @keyframes kejtu-chase {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0.55, 0, 0.45, 1);
    }
    50% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0.55, 0, 0.45, 1);
    }
    100% {
      transform: rotate(360deg);
    }
  }
  /* Stretched along the way they travel (the diagonal both eyes sit on). */
  @keyframes kejtu-chase-smear {
    0%,
    100% {
      transform: rotate(-45deg) scale(1, 1) rotate(45deg);
    }
    50% {
      transform: rotate(-45deg) scale(1.4, 0.82) rotate(45deg);
    }
  }
  @keyframes kejtu-doze {
    0%,
    100% {
      transform: scale(1, 1);
    }
    50% {
      transform: scale(1.035, 0.97);
    }
  }
  @keyframes kejtu-zee {
    0% {
      opacity: 0;
      transform: translate(0, 4px) scale(0.6);
    }
    18% {
      opacity: 1;
    }
    70% {
      opacity: 0.8;
    }
    100% {
      opacity: 0;
      transform: translate(9px, -16px) scale(1.4);
    }
  }
  @keyframes kejtu-blink-quick {
    0%,
    100% {
      transform: none;
    }
    45%,
    55% {
      transform: scaleY(0.1);
    }
  }
  @keyframes kejtu-tilt {
    0%,
    100% {
      transform: rotate(0deg);
    }
    30%,
    62% {
      transform: rotate(-7deg);
    }
  }
  @keyframes kejtu-wink {
    0%,
    100% {
      transform: none;
    }
    25%,
    68% {
      transform: scaleY(0);
    }
  }
  @keyframes kejtu-wink-lid {
    0%,
    20% {
      opacity: 0;
    }
    30%,
    64% {
      opacity: 1;
    }
    74%,
    100% {
      opacity: 0;
    }
  }
  @keyframes kejtu-glance-up {
    0% {
      transform: none;
    }
    22%,
    52% {
      transform: translate(4px, -7px);
    }
    64%,
    80% {
      transform: translate(-5px, -5px);
    }
    100% {
      transform: none;
    }
  }
  @keyframes kejtu-glance-blink {
    0%,
    84%,
    100% {
      transform: none;
    }
    90% {
      transform: scaleY(0.1);
    }
  }
  @keyframes kejtu-nod {
    0%,
    100% {
      transform: translateY(0) rotate(0deg);
    }
    20%,
    56% {
      transform: translateY(3px) rotate(2deg);
    }
    38%,
    74% {
      transform: translateY(-1px) rotate(0deg);
    }
  }
  @keyframes kejtu-nod-eyes {
    0%,
    100% {
      transform: translateY(0);
    }
    20%,
    56% {
      transform: translateY(6px);
    }
    38%,
    74% {
      transform: translateY(-1px);
    }
  }
  /* Two small hops with squash and stretch: rise easing out, fall easing in. */
  @keyframes kejtu-hop {
    0%,
    52%,
    100% {
      transform: translateY(0) scale(1, 1);
    }
    10% {
      transform: translateY(0) scale(1.08, 0.9);
      animation-timing-function: cubic-bezier(0.2, 0.6, 0.4, 1);
    }
    25% {
      transform: translateY(-11px) scale(0.95, 1.06);
      animation-timing-function: cubic-bezier(0.6, 0, 0.8, 0.4);
    }
    40% {
      transform: translateY(0) scale(1.08, 0.91);
    }
    60% {
      transform: translateY(0) scale(1.05, 0.93);
      animation-timing-function: cubic-bezier(0.2, 0.6, 0.4, 1);
    }
    74% {
      transform: translateY(-7px) scale(0.97, 1.04);
      animation-timing-function: cubic-bezier(0.6, 0, 0.8, 0.4);
    }
    88% {
      transform: translateY(0) scale(1.05, 0.95);
    }
  }
  @keyframes kejtu-happy-eyes {
    0%,
    100% {
      transform: none;
    }
    10%,
    84% {
      transform: scaleY(0);
    }
  }
  @keyframes kejtu-happy-lids {
    0%,
    6% {
      opacity: 0;
    }
    14%,
    80% {
      opacity: 1;
    }
    90%,
    100% {
      opacity: 0;
    }
  }
  /* Jelly: a squeeze that wobbles out. */
  @keyframes kejtu-squish {
    0%,
    100% {
      transform: scale(1, 1);
    }
    12% {
      transform: scale(1.16, 0.84);
    }
    28% {
      transform: scale(0.9, 1.1);
    }
    44% {
      transform: scale(1.07, 0.94);
    }
    60% {
      transform: scale(0.97, 1.03);
    }
    76% {
      transform: scale(1.015, 0.985);
    }
  }
  @keyframes kejtu-sway {
    0%,
    100% {
      transform: rotate(0deg);
    }
    22% {
      transform: rotate(-6deg);
    }
    48% {
      transform: rotate(5deg);
    }
    72% {
      transform: rotate(-3deg);
    }
  }
  @keyframes kejtu-heart {
    0% {
      opacity: 0;
      transform: translateY(4px) scale(0.3);
    }
    22% {
      opacity: 0.8;
      transform: translateY(0) scale(1.15);
    }
    36% {
      transform: translateY(-2px) scale(1);
    }
    80% {
      opacity: 0.8;
    }
    100% {
      opacity: 0;
      transform: translateY(-14px) scale(1);
    }
  }
  @keyframes kejtu-hello {
    0%,
    100% {
      transform: translateY(0) rotate(0deg) scale(1, 1);
    }
    12% {
      transform: translateY(0) rotate(0deg) scale(1.08, 0.9);
      animation-timing-function: cubic-bezier(0.2, 0.6, 0.4, 1);
    }
    30%,
    52% {
      transform: translateY(-12px) rotate(-8deg) scale(0.97, 1.04);
      animation-timing-function: cubic-bezier(0.6, 0, 0.8, 0.4);
    }
    70% {
      transform: translateY(0) rotate(0deg) scale(1.07, 0.92);
    }
    84% {
      transform: translateY(0) rotate(0deg) scale(0.98, 1.02);
    }
  }
  @keyframes kejtu-hello-wink {
    0%,
    30%,
    58%,
    100% {
      transform: none;
    }
    36%,
    52% {
      transform: scaleY(0);
    }
  }
  @keyframes kejtu-hello-lid {
    0%,
    32% {
      opacity: 0;
    }
    38%,
    52% {
      opacity: 1;
    }
    58%,
    100% {
      opacity: 0;
    }
  }
  @keyframes kejtu-wake {
    0% {
      transform: translateY(0) scale(1.04, 0.96);
    }
    22% {
      transform: translateY(-8px) scale(0.95, 1.07);
      animation-timing-function: cubic-bezier(0.6, 0, 0.8, 0.4);
    }
    45% {
      transform: translateY(0) scale(1.05, 0.95);
    }
    65%,
    100% {
      transform: translateY(0) scale(1, 1);
    }
  }
  @keyframes kejtu-wake-eye {
    0% {
      transform: scale(1, 0.1);
    }
    22% {
      transform: scale(1.35, 1.35);
    }
    55% {
      transform: scale(1.15, 1.15);
    }
    100% {
      transform: scale(1, 1);
    }
  }
  /* Down below the edge, a pause, the big eye peeks up, then boo. */
  @keyframes kejtu-peek {
    0% {
      transform: translateY(0);
      animation-timing-function: cubic-bezier(0.5, 0, 0.9, 0.5);
    }
    22%,
    48% {
      transform: translateY(62px);
      animation-timing-function: cubic-bezier(0.2, 0.7, 0.3, 1);
    }
    60%,
    72% {
      transform: translateY(38px);
      animation-timing-function: cubic-bezier(0.3, 1.6, 0.5, 1);
    }
    84%,
    100% {
      transform: translateY(0);
    }
  }
  @keyframes kejtu-boo {
    0%,
    76%,
    100% {
      transform: none;
    }
    86% {
      transform: scale(1.3);
    }
  }
  /* Off to the right, round the back (hidden while they jump sides), and in
     from the left. Far enough out that the outline hides both eyes. */
  @keyframes kejtu-spin-eyes {
    0% {
      transform: translateX(0);
      opacity: 1;
      animation-timing-function: cubic-bezier(0.55, 0, 1, 0.6);
    }
    25% {
      transform: translateX(58px);
      opacity: 1;
    }
    26% {
      transform: translateX(58px);
      opacity: 0;
    }
    27%,
    48% {
      transform: translateX(-58px);
      opacity: 0;
    }
    49% {
      transform: translateX(-58px);
      opacity: 1;
      animation-timing-function: cubic-bezier(0, 0.4, 0.3, 1);
    }
    84% {
      transform: translateX(3px);
      opacity: 1;
    }
    100% {
      transform: translateX(0);
      opacity: 1;
    }
  }
  /* Eyes narrow near the edge, as if seen from the side. */
  @keyframes kejtu-spin-squeeze {
    0% {
      transform: scaleX(1);
      animation-timing-function: cubic-bezier(0.55, 0, 1, 0.6);
    }
    25%,
    49% {
      transform: scaleX(0.35);
      animation-timing-function: cubic-bezier(0, 0.4, 0.3, 1);
    }
    84%,
    100% {
      transform: scaleX(1);
    }
  }
  @keyframes kejtu-spin-body {
    0%,
    100% {
      transform: translateY(0) scale(1, 1);
    }
    10% {
      transform: translateY(0) scale(1.06, 0.93);
    }
    30% {
      transform: translateY(-6px) scale(0.96, 1.05);
    }
    60% {
      transform: translateY(-5px) scale(0.97, 1.04);
    }
    82% {
      transform: translateY(0) scale(1.05, 0.95);
    }
    92% {
      transform: translateY(0) scale(0.99, 1.01);
    }
  }
  /* Wind up, one full turn with a little too much, wobble to a stop. */
  @keyframes kejtu-roll {
    0% {
      transform: rotate(0deg);
      animation-timing-function: ease-in-out;
    }
    12% {
      transform: rotate(-24deg);
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    44% {
      transform: rotate(378deg);
      animation-timing-function: ease-in-out;
    }
    52% {
      transform: rotate(354deg);
    }
    60% {
      transform: rotate(364deg);
    }
    70% {
      transform: rotate(356deg);
    }
    82% {
      transform: rotate(362deg);
    }
    100% {
      transform: rotate(360deg);
    }
  }
  /* Each eye circles its own place, the two in opposite directions. */
  @keyframes kejtu-dizzy-big {
    0%,
    46% {
      transform: rotate(0deg) translateX(0) rotate(0deg);
    }
    56% {
      transform: rotate(-200deg) translateX(3px) rotate(200deg);
    }
    88% {
      transform: rotate(-1000deg) translateX(3px) rotate(1000deg);
    }
    100% {
      transform: rotate(-1200deg) translateX(0) rotate(1200deg);
    }
  }
  @keyframes kejtu-dizzy-small {
    0%,
    46% {
      transform: rotate(0deg) translateX(0) rotate(0deg);
    }
    56% {
      transform: rotate(240deg) translateX(4px) rotate(-240deg);
    }
    88% {
      transform: rotate(1200deg) translateX(4px) rotate(-1200deg);
    }
    100% {
      transform: rotate(1440deg) translateX(0) rotate(-1440deg);
    }
  }
  @keyframes kejtu-dizzy-show {
    0%,
    46% {
      opacity: 0;
    }
    56%,
    86% {
      opacity: 1;
    }
    100% {
      opacity: 0;
    }
  }
  /* Stars on a flat ellipse over the head: two half-period swings a quarter
     apart, smaller at the back. */
  @keyframes kejtu-orbit-x {
    from {
      transform: translateX(-20px);
    }
    to {
      transform: translateX(20px);
    }
  }
  @keyframes kejtu-orbit-y {
    from {
      transform: translateY(-4px) scale(0.7);
    }
    to {
      transform: translateY(4px) scale(1.1);
    }
  }
</style>
