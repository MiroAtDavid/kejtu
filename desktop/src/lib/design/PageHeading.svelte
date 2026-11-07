<script lang="ts">
  export let title: string;
  export let subtitle = "";
  export let context = "";
</script>

<header class="page-heading">
  {#if context}<p class="page-context">{context}</p>{/if}
  <h1>{title}</h1>
  {#if subtitle}<p class="page-subtitle">{subtitle}</p>{/if}
</header>

<style>
  .page-heading {
    margin: 0 0 calc(30px * var(--ke-density));
  }
  .page-heading h1 {
    font: var(--ke-title-page);
    letter-spacing: var(--ke-title-page-tracking);
    margin: 0 0 10px;
  }
  .page-context,
  .page-subtitle {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    line-height: 1.7;
    margin: 0;
  }
  .page-context {
    margin-bottom: 12px;
  }
  @media (max-width: 650px) {
    .page-heading h1 {
      font-size: 27px;
    }
  }
</style>
