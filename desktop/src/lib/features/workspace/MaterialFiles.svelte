<script lang="ts">
  import MaterialFolder from "./MaterialFolder.svelte";
  export let courseId: string;
  export let courseTitle = "";
  export let selected = "";
  export let sidebar = false;
  export let onSelect: (path: string) => void;
  let expanded = true,
    refresh = 0;
  export function refreshFiles() {
    refresh++;
  }
</script>

<section class="material-files" class:sidebar aria-label="Section files">
  {#if !sidebar && courseTitle}<p class="course-name">{courseTitle}</p>{/if}
  <div class="heading">
    <button
      class="toggle"
      aria-label="Section files"
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
  <div class="contents" hidden={!expanded}>
    <MaterialFolder {courseId} {selected} {refresh} {onSelect} />
  </div>
</section>

<style>
  .material-files {
    min-width: 0;
    color: var(--ke-text-secondary);
    padding-bottom: 12px;
  }
  .material-files.sidebar {
    padding-top: calc(18px * var(--ke-density));
  }
  .course-name {
    margin: 0 8px 10px;
    font-size: 11px;
    line-height: 1.6;
    color: var(--ke-text-tertiary);
    overflow-wrap: anywhere;
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
    font-size: 12px;
    cursor: pointer;
    border-radius: 4px;
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
    max-height: none;
    overflow: visible;
  }
  [hidden] {
    display: none;
  }
</style>
