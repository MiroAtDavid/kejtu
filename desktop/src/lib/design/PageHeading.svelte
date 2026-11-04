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
    font:
      400 32px/1.25 Georgia,
      "Times New Roman",
      serif;
    letter-spacing: -0.7px;
    margin: 0 0 10px;
  }
  .page-context,
  .page-subtitle {
    color: var(--ke-text-secondary);
    font-size: 12px;
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
