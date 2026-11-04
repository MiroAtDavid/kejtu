<!-- Original, decorative line work; follows the current palette and stays still. -->
<svg viewBox="0 0 340 120" fill="none" aria-hidden="true" focusable="false">
  <path
    class="orbit"
    d="M16 81C60 108 154 108 243 76S333 14 281 16C230 18 189 36 168 58"
  />
  <path class="orbit" d="M69 16C25 30 69 69 154 88S313 103 328 76" />
  <path
    class="thread"
    d="M37 83C78 53 110 62 146 60M214 58C252 59 271 42 301 40"
  />
  <circle class="wash" cx="180" cy="58" r="36" />
  <circle class="face" cx="180" cy="58" r="27" />
  <circle class="eye" cx="170" cy="50" r="2.7" />
  <circle class="eye" cx="188" cy="66" r="1.6" />
  <circle class="point" cx="37" cy="83" r="3" />
  <circle class="point" cx="301" cy="40" r="3" />
  <path class="spark" d="M90 27v10m-5-5h10M259 87v8m-4-4h8" />
</svg>

<style>
  svg {
    display: block;
    width: 100%;
    height: 100%;
    color: var(--ke-accent);
  }
  .orbit {
    stroke: currentColor;
    stroke-width: 0.7;
    opacity: 0.3;
  }
  .thread {
    stroke: currentColor;
    stroke-width: 0.8;
    opacity: 0.45;
  }
  .wash {
    fill: currentColor;
    opacity: 0.07;
  }
  .face {
    fill: var(--ke-surface);
    stroke: currentColor;
    stroke-width: 1;
  }
  .eye {
    fill: currentColor;
  }
  .point {
    fill: var(--ke-surface);
    stroke: currentColor;
    stroke-width: 0.8;
  }
  .spark {
    stroke: currentColor;
    stroke-width: 0.8;
    opacity: 0.6;
  }
</style>
