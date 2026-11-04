<script lang="ts">
  import Wordmark from "./Wordmark.svelte";
  export let subtitle = "Your quiet space";
</script>

<div class="sidebar-identity">
  <svg class="companion-mark" viewBox="0 0 96 96" fill="none" aria-hidden="true"
    ><path
      d="M48 8C70 6 88 23 87 47C86 70 70 88 46 87C23 86 7 69 9 45C11 22 27 10 48 8Z"
      stroke="currentColor"
      stroke-width="2.5"
    /><circle cx="34" cy="34" r="6" fill="currentColor" /><circle
      cx="63"
      cy="63"
      r="3.5"
      fill="currentColor"
      opacity=".58"
    /></svg
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
