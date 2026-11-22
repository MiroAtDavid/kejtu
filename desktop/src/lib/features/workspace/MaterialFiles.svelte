<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import MaterialFolder from "./MaterialFolder.svelte";
  export let courseId: string;
  export let courseTitle = "";
  export let selected = "";
  export let sidebar = false;
  export let disabled = false;
  export let onNewMarkdown: () => void;
  export let onSelect: (path: string, code?: boolean) => void;
  let expanded = true,
    refresh = 0;
  export function refreshFiles() {
    refresh++;
  }
  // WebKit resets scrollTop when a mounted subtree becomes display:none.
  // Keep its last visible offset when hiding the sidebar or collapsing the file tree.
  function retainScroll(node: HTMLElement) {
    let visible = node.clientHeight > 0;
    let offset = node.scrollTop;
    function remember() {
      if (visible && node.clientHeight > 0) offset = node.scrollTop;
    }
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            const nextVisible = node.clientHeight > 0;
            if (nextVisible && !visible) node.scrollTop = offset;
            visible = nextVisible;
          });
    node.addEventListener("scroll", remember);
    observer?.observe(node);
    return {
      destroy() {
        node.removeEventListener("scroll", remember);
        observer?.disconnect();
      },
    };
  }
</script>

<section class="material-files" class:sidebar aria-label="Module files">
  {#if courseTitle}<div class="sidebar-module">
      <span>Current module</span>
      <p>{courseTitle}</p>
    </div>{/if}
  <div class="heading">
    <button
      class="toggle"
      aria-label="Module files"
      aria-expanded={expanded}
      on:click={() => (expanded = !expanded)}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.3"
        aria-hidden="true"
        ><path d={expanded ? "m5 8 5 5 5-5" : "m8 5 5 5-5 5"} /></svg
      >
      Resources
    </button>
    <button
      class="refresh"
      {disabled}
      title="New Markdown document"
      aria-label="New Markdown document"
      on:click={onNewMarkdown}><Icon name="plus" size={14} /></button
    >
    <button
      class="refresh"
      title="Refresh folder"
      aria-label="Refresh folder"
      on:click={() => refresh++}
    >
      <svg
        width="15"
        height="15"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.3"
        aria-hidden="true"><path d="M16 7a6 6 0 1 0 0 6M16 3v4h-4" /></svg
      >
    </button>
  </div>
  <div class="contents" hidden={!expanded} use:retainScroll>
    <MaterialFolder {disabled} {courseId} {selected} {refresh} {onSelect} />
  </div>
</section>

<style>
  .material-files {
    min-width: 0;
    color: var(--ke-text-secondary);
    padding-bottom: 12px;
  }
  .material-files.sidebar {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    padding-top: calc(18px * var(--ke-density));
  }
  .sidebar-module,
  .heading {
    flex: none;
  }
  /* The shared "Current module" block (shell.css) above the documents. */
  .material-files .sidebar-module {
    margin: 0 0 calc(12px * var(--ke-density));
  }
  .heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 7px;
  }
  .material-files button {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    border: 0;
    background: transparent;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: var(--ke-text-xs);
    cursor: pointer;
    border-radius: var(--ke-radius-sm);
    padding: 6px 8px;
  }
  .material-files .toggle {
    flex: 1;
    text-align: left;
  }
  .material-files .refresh {
    padding: 6px;
  }
  .material-files button:hover {
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  .material-files button:focus-visible {
    outline: 2px solid var(--ke-accent);
    outline-offset: 2px;
  }
  .contents {
    max-height: 220px;
    overflow-y: auto;
    scrollbar-width: thin;
  }
  .sidebar .contents {
    flex: 1;
    min-height: 0;
    max-height: none;
    overscroll-behavior-y: contain;
  }
  [hidden] {
    display: none;
  }
</style>
