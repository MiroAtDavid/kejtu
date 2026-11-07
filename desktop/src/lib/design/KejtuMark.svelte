<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { WORKING_STATES, type KejtuState } from "./kejtu-state";

  /** What Kejtu is doing. See kejtu-state.ts. */
  export let state: KejtuState = "idle";
  /** Rendered size of the outline itself; props may reach slightly beyond it. */
  export let size = 24;

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

  $: request(state);
  function request(next: KejtuState) {
    clearTimeout(holdTimer);
    if (next === applied) return;
    const wait = changedAt + HOLD - performance.now();
    if (wait > 0) holdTimer = setTimeout(() => void apply(next), wait);
    else void apply(next);
  }
  async function apply(next: KejtuState) {
    const reduced = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const moving =
      svg && !reduced
        ? [...svg.querySelectorAll<SVGElement>(".move, .eye, .figure")]
        : [];
    const from = moving.map((element) => getComputedStyle(element).transform);
    applied = next;
    changedAt = performance.now();
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
  onDestroy(() => {
    clearTimeout(holdTimer);
    clearTimeout(settleTimer);
  });

  // "Done" blinks only when this mark actually saw the work happen. A reply
  // loaded from history is simply still.
  let sawWork = false;
  $: if (WORKING_STATES.has(applied)) sawWork = true;
  $: shown = applied === "done" && !sawWork ? "idle" : applied;
  $: working = WORKING_STATES.has(shown);
</script>

<svg
  bind:this={svg}
  class="kejtu-mark"
  class:working
  class:settling
  data-state={shown}
  viewBox="0 0 96 96"
  width={size}
  height={size}
  fill="none"
  aria-hidden="true"
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
    <path
      class="outline"
      d="M48 8C70 6 88 23 87 47C86 70 70 88 46 87C23 86 7 69 9 45C11 22 27 10 48 8Z"
    />
    <g class="look"
      ><g class="move"
        ><circle class="eye big" cx="34" cy="34" r="6" /><circle
          class="eye small"
          cx="63"
          cy="63"
          r="3.5"
        /></g
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
  }
  .eye.small {
    opacity: 0.58;
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

  /* Done: one blink and a small settle, then still. */
  [data-state="done"] .eye {
    animation: kejtu-blink 700ms var(--ke-motion-ease) 1 both;
  }
  [data-state="done"] .figure {
    animation: kejtu-settle 700ms var(--ke-motion-ease) 1 both;
  }

  /* While a change settles, the eye and body loops wait; the glide back to
     rest is a short script animation. Props start their own loops at once,
     from their invisible first frame. !important beats each state's loop. */
  .settling .move,
  .settling .eye,
  .settling .figure {
    animation: none !important;
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
</style>
