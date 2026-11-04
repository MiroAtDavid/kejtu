<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { workspaceRequest } from "./workspace";
  export let courseId: string;
  export let path = "";
  export let selected = "";
  export let refresh = 0;
  export let onSelect: (path: string) => void;
  type Item = { name: string; path: string; kind: string };
  type Listing = { items: Item[]; next_offset: number | null };
  let listing: Listing = { items: [], next_offset: null };
  let loading = false,
    error = "",
    generation = 0;
  let expanded = new Set<string>();
  function reveal(file: string) {
    const relative = path
      ? file.startsWith(path + "/")
        ? file.slice(path.length + 1)
        : ""
      : file;
    if (relative.includes("/"))
      expanded = new Set([
        ...expanded,
        (path ? path + "/" : "") + relative.split("/")[0],
      ]);
  }
  $: reveal(selected);
  function toggle(folder: string) {
    const next = new Set(expanded);
    if (next.has(folder)) next.delete(folder);
    else next.add(folder);
    expanded = next;
  }
  async function load(append = false) {
    const run = ++generation;
    loading = true;
    error = "";
    try {
      const result = await workspaceRequest<Listing>("materials.list", {
        course_id: courseId,
        path,
        offset: append ? listing.next_offset || 0 : 0,
      });
      if (run !== generation) return;
      listing = {
        ...result,
        items: append ? [...listing.items, ...result.items] : result.items,
      };
    } catch (caught) {
      if (run === generation) error = String(caught);
    } finally {
      if (run === generation) loading = false;
    }
  }
  let mounted = false;
  onMount(() => {
    mounted = true;
  });
  $: if (mounted) {
    refresh;
    void load();
  }
  onDestroy(() => {
    generation++;
  });
</script>

<ul
  class="material-folder"
  aria-label={path || "Section folder contents"}
  aria-busy={loading}
>
  {#each listing.items as item (item.path)}
    <li>
      <button
        class:selected={item.kind === "file" && selected === item.path}
        class:folder={item.kind === "folder"}
        aria-current={item.kind === "file" && selected === item.path
          ? "true"
          : undefined}
        aria-expanded={item.kind === "folder"
          ? expanded.has(item.path)
          : undefined}
        title={item.path}
        on:click={() =>
          item.kind === "folder" ? toggle(item.path) : onSelect(item.path)}
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          stroke-width="1.2"
          aria-hidden="true"
        >
          {#if item.kind === "folder"}
            <path
              d={expanded.has(item.path) ? "m5 8 5 5 5-5" : "m8 5 5 5-5 5"}
            />
          {:else}<path d="M5 2h7l4 4v12H5V2Zm7 0v5h4M8 10h5m-5 3h5" />{/if}
        </svg><span>{item.name}</span>
      </button>
      {#if item.kind === "folder" && expanded.has(item.path)}
        <div class="children">
          <svelte:self
            {courseId}
            path={item.path}
            {selected}
            {refresh}
            {onSelect}
          />
        </div>
      {/if}
    </li>
  {/each}
  {#if error}<li class="error" role="alert">
      {error}<button class="more" on:click={() => load()}>Retry folder</button>
    </li>
  {:else if !listing.items.length}<li class="empty">
      {loading ? "Reading folder…" : "No material listed yet."}
    </li>{/if}
  {#if listing.next_offset !== null}<li>
      <button class="more" disabled={loading} on:click={() => load(true)}
        >More files <span aria-hidden="true">↓</span></button
      >
    </li>{/if}
</ul>

<style>
  .material-folder {
    list-style: none;
    margin: 0;
    padding: 0;
    min-width: 0;
  }
  li {
    list-style: none;
    min-width: 0;
  }
  .material-folder button {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    width: 100%;
    margin: 2px 0;
    padding: 7px 8px;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: 12px;
    line-height: 1.55;
    text-align: left;
    cursor: pointer;
  }
  button svg {
    flex-shrink: 0;
    margin-top: 2px;
    color: var(--ke-text-tertiary);
  }
  button span {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .material-folder button.selected {
    color: var(--ke-text-primary);
    background: color-mix(in srgb, var(--ke-marker-sand) 12%, transparent);
  }
  .material-folder button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  .material-folder button:focus-visible {
    outline: 2px solid var(--ke-accent);
    outline-offset: 2px;
  }
  .material-folder button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .children {
    margin-left: 14px;
    padding-left: 3px;
    border-left: 1px solid var(--ke-border);
  }
  .empty,
  .error {
    padding: 8px;
    color: var(--ke-text-tertiary);
    font-size: 11px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .error {
    color: var(--ke-text-secondary);
  }
  .material-folder .more {
    font-size: 11px;
  }
</style>
