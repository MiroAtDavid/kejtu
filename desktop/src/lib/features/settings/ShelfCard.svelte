<script lang="ts">
  import PackageIcon from "./PackageIcon.svelte";

  /**
   * One installed thing on the Shelf: a package, a skill or a connection.
   * The card opens the item's own page. A tool that is not set up yet has no
   * page; its card offers the one action instead, like Install on Browse.
   */
  export let title: string;
  export let state: string;
  export let summary: string;
  export let icon: string;
  export let category = "";
  /** Back from an item's page finds its card again by these. */
  export let part: string | undefined = undefined;
  export let packageId: string | undefined = undefined;
  export let disabled = false;
  export let onOpen: (() => void) | undefined = undefined;
  export let action = "";
  export let actionLabel = "";
  export let actionDisabled = false;
  export let onAction: () => void = () => {};
</script>

{#snippet identity()}
  <span class="row-identity"
    ><PackageIcon name={icon} {category} />
    <span class="names"
      ><strong>{title}</strong><span class="status-tag">{state}</span></span
    >
  </span>
{/snippet}

{#if onOpen}
  <button
    class="package-row cp-card"
    data-package={packageId}
    data-part={part}
    {disabled}
    on:click={onOpen}
  >
    {@render identity()}
    <span class="row-summary">{summary}</span>
  </button>
{:else}
  <div class="package-row cp-card" data-part={part}>
    {@render identity()}
    <span class="card-bottom"
      ><span class="row-summary">{summary}</span><button
        class="cp-mini"
        aria-label={actionLabel || undefined}
        disabled={actionDisabled}
        on:click={onAction}>{action}</button
      ></span
    >
  </div>
{/if}

<style>
  .package-row {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
    min-height: 96px;
    padding: 12px 14px;
    color: var(--ke-text-primary);
    text-align: left;
  }
  button.package-row:hover:not(:disabled) {
    border-color: var(--ke-border-strong);
    background: var(--ke-surface-raised);
  }
  .row-identity {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .package-row :global(.package-icon) {
    width: 32px;
    height: 32px;
    border-radius: 9px;
  }
  .names {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .names strong {
    overflow: hidden;
    font-size: var(--ke-text-sm);
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status-tag,
  .row-summary {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 24px;
  }
</style>
