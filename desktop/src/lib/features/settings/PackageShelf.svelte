<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import type {
    ShelfCatalogueEntry,
    ShelfInstallation,
  } from "$lib/api/wire.generated";
  import PackageIcon from "./PackageIcon.svelte";
  export let entries: ShelfCatalogueEntry[] = [];
  export let installed: ShelfInstallation[] = [];
  export let busy = false;
  /** Installing waits for the current reply; looking never does. */
  export let ownerBusy = false;
  /** The Find field lives in the page's bar, beside the Browse / Installed pills. */
  export let query = "";
  export let onChoose: (entry: ShelfCatalogueEntry) => void = () => {};
  export let onInstall: (entry: ShelfCatalogueEntry) => void = () => {};
  export let onInstalled: (item: ShelfInstallation) => void = () => {};
  /** Kept by the page, so it survives a visit to a package's own page. */
  export let category = "All";
  $: categories = ["All", ...new Set(entries.map((e) => e.category))];
  $: visible = entries.filter(
    (e) =>
      (category === "All" || e.category === category) &&
      `${e.title} ${e.summary} ${e.publisher}`
        .toLowerCase()
        .includes(query.toLowerCase().trim()),
  );
  function existing(entry: ShelfCatalogueEntry, items: ShelfInstallation[]) {
    return items.find((item) => item.origin.catalogue_id === entry.id);
  }
</script>

<div class="cp-chips" role="group" aria-label="Package categories">
  {#each categories as value}<button
      class="cp-chip"
      aria-pressed={category === value}
      on:click={() => (category = value)}>{value}</button
    >{/each}
</div>
<div class="shelf-grid cp-cards">
  {#each visible as entry (entry.id)}
    <!-- Keep the asynchronously loaded installed record an explicit dependency. -->
    {@const held = existing(entry, installed)}
    {@const skills = entry.package.components.filter(
      (part) => part.kind === "skill",
    ).length}
    {@const tools = entry.package.components.length - skills}
    <article class="shelf-card cp-card" data-entry={entry.id}>
      <!-- The card opens the package's own page; Install starts from here too. -->
      <button
        class="open"
        disabled={busy}
        on:click={() => (held ? onInstalled(held) : onChoose(entry))}
        aria-label={`${held ? "Open installed" : "View"} ${entry.title}`}
      >
        <span class="card-heading">
          <PackageIcon name={entry.icon} category={entry.category} />
          <span class="names">
            <span class="title">{entry.title}</span>
            <span class="publisher">{entry.publisher}</span>
          </span>
        </span>
        <span class="description">{entry.summary}</span>
      </button>
      <div class="card-bottom">
        <span class="facts"
          >{skills ? `${skills} skill${skills === 1 ? "" : "s"}` : ""}{skills &&
          tools
            ? " · "
            : ""}{tools
            ? `${tools} connection${tools === 1 ? "" : "s"}`
            : ""}</span
        >
        {#if held}<span class="held"
            ><Icon name="check" size={11} />Installed</span
          >{:else}<button
            class="cp-mini"
            disabled={busy || ownerBusy}
            aria-label={`Install ${entry.title}`}
            on:click={() => onInstall(entry)}>Install</button
          >{/if}
      </div>
    </article>
  {/each}
</div>
{#if !visible.length}<p class="cp-empty">
    No packages match. Try another word or category.
  </p>{/if}
<p class="cp-hint shelf-note">
  Choose a package, then install and enable it in one place. Sign in if needed;
  individual skill and tool choices are available under Advanced. Packages are
  optional and come from their original publishers.
</p>

<style>
  .shelf-card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
    min-height: 122px;
    padding: 12px 14px;
  }
  .shelf-card:hover {
    border-color: var(--ke-border-strong);
  }
  .open {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    min-height: 0;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .open:hover:not(:disabled) {
    background: transparent;
  }
  .card-heading {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .card-heading :global(.package-icon) {
    width: 32px;
    height: 32px;
    border-radius: 9px;
  }
  .names {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .title,
  .publisher {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    font-size: var(--ke-text-sm);
    font-weight: 500;
  }
  .publisher {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .description {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    color: var(--ke-text-secondary);
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 24px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .facts {
    white-space: nowrap;
  }
  .held {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--ke-text-secondary);
    white-space: nowrap;
  }
  .shelf-note {
    margin-top: 20px;
  }
</style>
