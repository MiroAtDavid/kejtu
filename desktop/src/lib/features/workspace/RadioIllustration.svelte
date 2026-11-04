<script lang="ts">
  export let playing = false;
  const dots = Array.from({ length: 81 }, (_, i) => ({
    x: (i % 9) - 4,
    y: Math.floor(i / 9) - 4,
  })).filter(({ x, y }) => x * x + y * y <= 17);
</script>

<!-- Decorative playback indicator, not an audio-level measurement or control. -->
<div class="radio-illustration" class:playing aria-hidden="true">
  <svg viewBox="0 0 300 170" fill="none">
    <path class="line" d="M79 41V27q0-9 9-9h87q9 0 9 9v14M233 42l25-29" />
    <circle cx="259" cy="12" r="2" fill="var(--ke-border-strong)" />
    <rect class="body" x="32" y="41" width="236" height="111" rx="17" />
    <path class="line" d="M52 153v4m196-4v4" />
    <circle class="inset" cx="91" cy="96" r="37" />
    {#each dots as { x, y }}
      <circle class="speaker-dot" cx={91 + x * 6} cy={96 + y * 6} r="1.15" />
    {/each}
    <rect class="inset" x="147" y="60" width="100" height="36" rx="6" />
    <path
      class="line ticks"
      d="M157 71v6m10-6v4m10-4v6m10-6v4m10-4v6m10-6v4m10-4v6m10-6v4m10-4v6"
    />
    <path class="needle" d="M191 65v18" />
    <g class="levels" transform="translate(155 109)">
      <rect x="0" y="5" width="3" height="10" rx="1.5" />
      <rect x="7" y="1" width="3" height="14" rx="1.5" />
      <rect x="14" y="3" width="3" height="12" rx="1.5" />
      <rect x="21" y="0" width="3" height="15" rx="1.5" />
      <rect x="28" y="6" width="3" height="9" rx="1.5" />
    </g>
    <!-- Kejtu's existing companion mark doubles as the tuning knob. -->
    <svg x="220" y="104" width="28" height="28" viewBox="0 0 96 96">
      <path
        class="knob"
        d="M48 8C70 6 88 23 87 47C86 70 70 88 46 87C23 86 7 69 9 45C11 22 27 10 48 8Z"
      />
      <circle cx="34" cy="34" r="6" fill="var(--ke-accent)" />
      <circle cx="63" cy="63" r="3.5" fill="var(--ke-accent)" opacity=".58" />
    </svg>
  </svg>
</div>

<style>
  .radio-illustration {
    padding: 10px 8px 0;
    margin: 2px 0 6px;
    border-radius: var(--ke-radius-lg);
    background: var(--ke-surface-inset);
  }
  .radio-illustration > svg {
    display: block;
    width: 100%;
    max-width: 300px;
    height: auto;
    margin: 0 auto;
  }
  .line {
    stroke: var(--ke-border-strong);
    stroke-width: 1.4;
    stroke-linecap: round;
  }
  .body {
    fill: color-mix(in srgb, var(--ke-surface-inset) 86%, var(--ke-accent));
    stroke: var(--ke-border-strong);
    stroke-width: 1.2;
  }
  .inset,
  .knob {
    fill: var(--ke-surface);
  }
  .speaker-dot {
    fill: var(--ke-accent);
    opacity: 0.45;
  }
  .ticks {
    opacity: 0.7;
  }
  .needle,
  .knob {
    stroke: var(--ke-accent);
    stroke-linecap: round;
    stroke-width: 2;
  }
  .knob {
    stroke-width: 2.5;
  }
  .levels rect {
    fill: var(--ke-accent);
    opacity: 0.3;
    transform: scaleY(0.35);
    transform-box: fill-box;
    transform-origin: center bottom;
  }
  .playing .levels rect {
    animation: radio-level 1.8s ease-in-out infinite alternate;
  }
  .playing .levels rect:nth-child(2) {
    animation-delay: -0.8s;
    animation-duration: 2.1s;
  }
  .playing .levels rect:nth-child(3) {
    animation-delay: -1.3s;
    animation-duration: 1.5s;
  }
  .playing .levels rect:nth-child(4) {
    animation-delay: -0.4s;
    animation-duration: 2.3s;
  }
  .playing .levels rect:nth-child(5) {
    animation-delay: -1s;
    animation-duration: 1.9s;
  }
  @keyframes radio-level {
    from {
      transform: scaleY(0.35);
      opacity: 0.55;
    }
    to {
      transform: scaleY(1);
      opacity: 0.95;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .playing .levels rect {
      animation: none;
      transform: none;
      opacity: 0.7;
    }
  }
</style>
