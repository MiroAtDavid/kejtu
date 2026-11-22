<script lang="ts">
  import PackageIcon from "./PackageIcon.svelte";
  export let title: string;
  export let icon: string;
  export let category = "";
  export let subtitle = "";
  // Installed packages already expose their identity as a heading in the details below.
  export let tag: "h3" | "strong" = "h3";
</script>

<div class="item-heading">
  <div class="identity">
    <PackageIcon name={icon} {category} />
    <div class="caption">
      <svelte:element this={tag} class="item-title">{title}</svelte:element>
      {#if subtitle}<p class="cp-hint">{subtitle}</p>{/if}
    </div>
  </div>
  <slot />
</div>

<style>
  .item-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 12px 16px;
    margin-top: 10px;
  }
  .identity {
    display: flex;
    flex: 1 1 280px;
    align-items: center;
    min-width: 0;
    gap: 14px;
  }
  .caption {
    min-width: 0;
  }
  .item-heading .item-title {
    display: block;
    margin: 0;
    font: 400 28px/1.25 var(--ke-font-heading);
    letter-spacing: -0.5px;
    overflow-wrap: anywhere;
  }
  .item-heading p {
    margin: 2px 0 0;
  }
  @media (max-width: 650px) {
    .item-heading .item-title {
      font-size: 24px;
    }
  }
</style>
