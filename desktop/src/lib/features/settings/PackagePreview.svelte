<script lang="ts">
  import type {
    ShelfCatalogueEntry,
    ShelfDescription,
    ShelfInstallation,
  } from "$lib/api/wire.generated";
  import {
    formatNames,
    partFacts,
    size,
    type PackageOverlap,
  } from "./packages";
  import Icon from "$lib/design/Icon.svelte";
  import { partIcon, partTitle, readableName } from "./shelf-library";
  import ShelfItemHeading from "./ShelfItemHeading.svelte";
  import PackageIcon from "./PackageIcon.svelte";

  /**
   * What a package folder holds, before anything is installed: the parts it
   * brings with what Kejtu enforces for each, what is left out, and why it
   * cannot be installed when it cannot. With `target` it is shown as an update
   * of that installation. It decides nothing itself.
   */
  export let preview: ShelfDescription;
  export let shelfEntry: ShelfCatalogueEntry | null = null;
  export let onRefresh: () => void = () => {};
  export let target: ShelfInstallation | null = null;
  export let collision = "";
  export let overlaps: PackageOverlap[] = [];
  export let unread: string[] = [];
  export let onOpenOverlap: (item: PackageOverlap) => void = () => {};
  export let onRecheck: () => void = () => {};
  $: blocked =
    overlaps.some((item) => item.blocking) || unread.length > 0 || !!collision;
  export let busy = false;
  export let ownerBusy = false;
  export let onInstall: () => void = () => {};
  export let onUpdate: () => void = () => {};
  export let onDismiss: () => void = () => {};

  type Part = ShelfDescription["components"][number];
  // A skill and a tool may share a name; they are different parts.
  const same = (part: Part, kind: string, key: string) =>
    part.name === key && (part.kind === "skill") === (kind === "skill");
  $: held =
    target?.components.filter(
      (component) =>
        component.kind === "skill" || component.kind === "connection",
    ) ?? [];
  $: leftOut = preview.unsupported.filter((item) => !item.essential);
  $: blocking = preview.unsupported.filter((item) => item.essential);
  // Parts of the installed version that the new one no longer has.
  $: dropped = held
    .filter(
      (component) =>
        !preview.components.some((part) =>
          same(part, component.kind, component.key),
        ),
    )
    .map((component) =>
      partTitle(
        component.kind === "skill" ? "skill" : "connection",
        component.key,
        previewOwner,
      ),
    );
  $: added = (part: Part) =>
    !!target &&
    !held.some((component) => same(part, component.kind, component.key));
  // A Shelf package that is not installed yet gets the full page: its own
  // heading, and the publisher's facts in a details card beside the content.
  $: page = !!shelfEntry && !target;
  // Parts are named by the same rule as on the Shelf, from this package.
  $: previewConnections = preview.components.filter(
    (part) => part.kind !== "skill",
  );
  $: previewOwner = {
    name: preview.name,
    title: shelfEntry?.title ?? readableName(preview.name),
    soleConnection:
      previewConnections.length === 1 ? previewConnections[0].name : undefined,
  };
  $: skills = preview.components.filter((part) => part.kind === "skill").length;
  $: tools = preview.components.length - skills;
</script>

<div class="preview" role="group" aria-label="Package preview">
  {#if shelfEntry}
    <div class="shelf-heading">
      <div class="shelf-source">
        <PackageIcon name={shelfEntry.icon} category={shelfEntry.category} />
        <div>
          <strong class:title={page}>{shelfEntry.title}</strong>
          {#if page}<p>{shelfEntry.summary}</p>{:else}
            <p>
              {shelfEntry.publisher} · {shelfEntry.license} · {size(
                shelfEntry.bytes,
              )} download
            </p>
            <a href={shelfEntry.source_url} target="_blank" rel="noreferrer"
              >View original package ↗</a
            >{/if}
        </div>
      </div>
      {#if preview.installable && !target}
        <button
          class="cp-action primary"
          disabled={busy || ownerBusy || blocked}
          on:click={onInstall}>Install &amp; enable</button
        >
      {/if}
    </div>
  {:else if !target}
    <ShelfItemHeading
      title={preview.name}
      icon={preview.format === "skill"
        ? partIcon("skill", preview.name)
        : "package"}
    />
  {/if}
  <div class:cp-item={page} class:stack={!page}>
    <div class:cp-main={page} class:stack-main={!page}>
      <section class="cp-section">
        {#if shelfEntry || target}<div class="preview-heading">
            <h3>{preview.name}</h3>
            {#if shelfEntry}<button
                class="cp-icon"
                aria-label="Refresh"
                title="Refresh"
                disabled={busy}
                on:click={onRefresh}><Icon name="refresh" size={14} /></button
              >{/if}
          </div>{/if}
        <p class="cp-hint">
          {preview.version} · {formatNames[preview.format]}{target
            ? ` · installed now: ${target.version}`
            : ""}
        </p>
        {#if preview.description !== preview.name}<p>
            {preview.description}
          </p>{/if}
        {#if preview.components.length}
          <h4 class="cp-cap">It brings</h4>
          <ul>
            {#each preview.components as part (part.kind + part.name)}
              <li>
                <strong
                  >{partTitle(
                    part.kind === "skill" ? "skill" : "connection",
                    part.name,
                    previewOwner,
                  )}{added(part) ? " (new)" : ""}</strong
                >
                <span class="cp-hint">{partFacts(part)}</span>
              </li>
            {/each}
          </ul>
        {/if}
        {#if dropped.length}
          <h4 class="cp-cap">No longer in this version</h4>
          <ul>
            {#each dropped as name}<li>
                <strong>{name}</strong>
                <span class="cp-hint">The update removes it.</span>
              </li>{/each}
          </ul>
        {/if}
        {#if leftOut.length}
          <h4 class="cp-cap">Left out</h4>
          <ul>
            {#each leftOut as item}<li>
                <strong>{item.name}</strong>
                <span class="cp-hint">{item.reason}</span>
              </li>{/each}
          </ul>
        {/if}
        {#if overlaps.length}
          <h4 class="cp-cap">Already installed</h4>
          <ul aria-label="Existing integrations">
            {#each overlaps as item (item.kind + item.key)}
              <li>
                <strong>{item.name}</strong>
                <span>{item.message}</span>
                <div class="actions">
                  <button
                    disabled={busy}
                    on:click={() => onOpenOverlap(item)}
                    aria-label={`Open existing ${item.kind} ${item.name}`}
                    >Open existing {item.kind}</button
                  >
                </div>
              </li>
            {/each}
          </ul>
        {/if}
        {#if unread.length}<p role="alert" class="cp-note">
            Could not check existing {unread.join(" and ")}. Check again before
            installing or updating.
          </p>{/if}
        {#if collision}<p role="alert" class="cp-note">{collision}</p>{/if}
        {#if blocked || overlaps.length}<div class="actions">
            <button disabled={busy} on:click={onRecheck}>Check again</button>
          </div>{/if}
        {#if !preview.installable}
          <h4 class="cp-cap">Cannot be installed</h4>
          <ul>
            {#each blocking as item}<li>
                <strong>{item.name}</strong>
                <span class="cp-hint">{item.reason}</span>
              </li>{/each}
          </ul>
          <div class="actions">
            <button disabled={busy} on:click={onDismiss}>Close</button>
          </div>
        {:else if target}
          <p class="cp-hint">
            A tool that changed is switched off until you check it and turn it
            on again. One that is the same keeps its settings. Its skills are
            switched off. The data it kept stays.
          </p>
          <div class="actions">
            <button
              class="cp-action primary"
              disabled={busy || ownerBusy || blocked}
              on:click={onUpdate}>Update to {preview.version}</button
            >
            {#if !shelfEntry}<button disabled={busy} on:click={onDismiss}
                >Not now</button
              >{/if}
          </div>
        {:else}
          <p class="cp-hint">
            Enables the included skills and all available tools. If an account
            is needed, sign in during setup. You can limit individual tools in
            Advanced.
          </p>
          {#if !shelfEntry}
            <div class="actions">
              <button
                class="cp-action primary"
                disabled={busy || ownerBusy || blocked}
                on:click={onInstall}>Install &amp; enable</button
              >
              <button disabled={busy} on:click={onDismiss}>Not now</button>
            </div>
          {/if}
        {/if}
      </section>
      {#if shelfEntry}<section class="cp-section">
          <h4 class="cp-cap first">Before you install</h4>
          <p>{shelfEntry.requirements}</p>
          <p class="cp-hint">
            Downloads the exact upstream version shown and sets up its skills
            and tools. Account sign-in, if needed, is part of setup.
          </p>
        </section>{/if}
    </div>
    {#if page && shelfEntry}<dl class="cp-side facts">
        <div class="cp-row">
          <dt class="cp-key">Publisher</dt>
          <dd>{shelfEntry.publisher}</dd>
        </div>
        <div class="cp-row">
          <dt class="cp-key">Contains</dt>
          <dd>
            {[
              skills ? `${skills} skill${skills === 1 ? "" : "s"}` : "",
              tools ? `${tools} connection${tools === 1 ? "" : "s"}` : "",
            ]
              .filter(Boolean)
              .join(", ")}
          </dd>
        </div>
        <div class="cp-row">
          <dt class="cp-key">Licence</dt>
          <dd>{shelfEntry.license}</dd>
        </div>
        <div class="cp-row">
          <dt class="cp-key">Size</dt>
          <dd>{size(shelfEntry.bytes)} download</dd>
        </div>
        <div class="cp-row">
          <dt class="cp-key">Source</dt>
          <dd>
            <a href={shelfEntry.source_url} target="_blank" rel="noreferrer"
              >View original package ↗</a
            >
          </dd>
        </div>
      </dl>{/if}
  </div>
</div>

<style>
  .preview {
    min-width: 0;
  }
  .stack {
    margin-top: 12px;
  }
  .stack-main {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .shelf-heading,
  .preview-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px 16px;
  }
  .shelf-heading {
    margin-top: 10px;
  }
  .shelf-source {
    display: flex;
    flex: 1 1 280px;
    align-items: center;
    gap: 14px;
    min-width: 0;
  }
  .shelf-source > div {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .shelf-source strong {
    font-size: var(--ke-text-sm);
    font-weight: 500;
  }
  .shelf-source .title {
    display: block;
    font: 400 28px/1.25 var(--ke-font-heading);
    letter-spacing: -0.5px;
  }
  .shelf-source p {
    margin: 2px 0 0;
    color: var(--ke-text-secondary);
  }
  .preview-heading h3 {
    flex: 1 1 12rem;
    min-width: 0;
    margin: 0;
    font: 500 var(--ke-text-sm) / 1.4 var(--ke-font-ui);
    overflow-wrap: anywhere;
  }
  p {
    max-width: 68ch;
    margin: 6px 0;
    overflow-wrap: anywhere;
  }
  h4 {
    margin: 16px 0 6px;
  }
  h4.first {
    margin-top: 0;
  }
  ul {
    display: grid;
    gap: 8px;
    max-width: 68ch;
    margin: 0 0 8px;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    gap: 2px;
    overflow-wrap: anywhere;
  }
  li strong {
    font-weight: 500;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin: 12px 0 2px;
  }
  .facts {
    margin: 0;
  }
  .facts .cp-row {
    align-items: baseline;
    min-height: 0;
    padding: 8px 14px;
  }
  .facts dd {
    flex: 1;
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
  }
  a {
    color: inherit;
    text-decoration: underline;
    text-decoration-color: var(--ke-border-strong);
    text-underline-offset: 3px;
  }
  @media (max-width: 650px) {
    .shelf-source .title {
      font-size: 24px;
    }
  }
</style>
