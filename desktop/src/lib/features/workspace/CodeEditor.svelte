<script lang="ts">
  import { onMount, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { identity } from "$lib/api/client";
  import { readCourseDocument } from "./document-bytes";
  import { documentViewportHeight } from "./document-dock";
  import { registerReader, refreshReaderDrafts } from "./reader-drafts";
  import { loadCodeEditor, saveCodeFile, type CodeView } from "./code-file";
  import "./document-chrome.css";

  export let courseId: string;
  export let file: string;
  export let active = true;
  export let busy = false;
  export let onChange: (dirty: boolean) => void = () => {};
  export let onDiscuss: (text: string) => void = () => {};
  export let onOriginal: () => void = () => {};

  let host: HTMLElement;
  let view: CodeView | undefined;
  let original: { path: string; sha256: string; writable: boolean } | undefined;
  let text = "",
    saved = "",
    error = "",
    notice = "";
  let dirty = false,
    loading = true,
    saving = false,
    selected = false,
    menu = false;
  // The "•••" actions close on a click anywhere outside them, or on Escape.
  let menuElement: HTMLDetailsElement | undefined;
  function dismissMenu(event: PointerEvent | KeyboardEvent) {
    if (!menuElement?.open) return;
    if (event instanceof KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (menuElement?.contains(document.activeElement))
        menuElement.querySelector("summary")?.focus({ preventScroll: true });
    } else if (
      event.target instanceof Node &&
      menuElement?.contains(event.target)
    )
      return;
    menuElement.open = false;
    menu = false;
  }
  let generation = 0,
    disposed = false,
    height = 400;
  $: dirty = !!view && text !== saved;
  $: {
    onChange(dirty || saving);
    refreshReaderDrafts();
  }
  $: if (active && host) {
    void tick().then(measure);
  }

  function measure() {
    if (disposed || !active || !host?.getClientRects().length) return;
    height = documentViewportHeight(host);
    view?.measure();
  }
  export async function confirmDiscard() {
    return (
      !saving &&
      (text === saved ||
        window.confirm("Discard unsaved changes to " + file + "?"))
    );
  }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (dirty || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  }

  async function open() {
    menu = false;
    if (saving || (dirty && !(await confirmDiscard()))) return;
    const turn = ++generation,
      startingText = text;
    const current = () => !disposed && turn === generation;
    loading = true;
    error = notice = "";
    try {
      const [data, create] = await Promise.all([
        readCourseDocument(courseId, file, current, true),
        loadCodeEditor(),
      ]);
      if (!current()) return;
      if (text !== startingText)
        throw new Error(
          "Your draft changed while reloading. Your edits are still here.",
        );
      const initial = new TextDecoder("utf-8", {
        fatal: true,
        ignoreBOM: true,
      }).decode(data.bytes);
      const container = document.createElement("div");
      const next = create(
        container,
        initial,
        file,
        data.writable === true,
        (value) => {
          text = value;
          notice = "";
        },
        (value) => (selected = value),
        () => void save(),
        () => discuss(false),
      );
      view?.destroy();
      host.replaceChildren(container);
      view = next;
      original = {
        path: data.path,
        sha256: data.sha256,
        writable: data.writable === true,
      };
      saved = text = initial;
      selected = false;
      await tick();
      measure();
    } catch (cause) {
      if (current())
        error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (current()) loading = false;
    }
  }
  async function save() {
    if (!original?.writable || !view || saving || loading || text === saved)
      return;
    const snapshot = text;
    saving = true;
    error = notice = "";
    try {
      const sha256 = await saveCodeFile(
        courseId,
        file,
        original.path,
        original.sha256,
        snapshot,
      );
      if (disposed) return;
      original = { ...original, sha256 };
      saved = snapshot;
      if (text === snapshot) notice = "Saved";
    } catch (cause) {
      if (!disposed)
        error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (!disposed) saving = false;
    }
  }
  function discuss(whole: boolean) {
    menu = false;
    if (busy || !view || !original) return;
    const part = whole ? null : view.selection();
    if (!whole && !part) return;
    const where = part
      ? part.first === part.last
        ? "line " + part.first
        : "lines " + part.first + "–" + part.last
      : "whole file";
    onDiscuss(
      original.path +
        " (" +
        where +
        (text !== saved ? ", unsaved draft" : "") +
        ")\n\n" +
        (part ? part.text : text),
    );
  }
  onMount(() => {
    const unregister = registerReader(identity(), {
      check: confirmDiscard,
      dirty: () => text !== saved || saving,
    });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    if (host.parentElement) observer?.observe(host.parentElement);
    void open();
    return () => {
      disposed = true;
      generation++;
      observer?.disconnect();
      view?.destroy();
      unregister();
    };
  });
</script>

<svelte:window
  on:resize={measure}
  on:beforeunload={beforeUnload}
  on:pointerdown={dismissMenu}
  on:keydown={dismissMenu}
/>
<section class="source-pane" aria-label="Code file">
  <div class="document-toolbar">
    <button
      class="icon"
      type="button"
      title="Save file (Ctrl/⌘+S)"
      aria-label="Save code file"
      disabled={!original?.writable || !dirty || saving || loading}
      on:click={save}><Icon name="save" size={16} /></button
    >
    <button
      type="button"
      title={selected
        ? "Discuss the selected lines"
        : "Select lines to ask in chat"}
      disabled={!selected || busy}
      on:click={() => discuss(false)}>Ask in chat</button
    >
    {#if original && !original.writable}<span class="state">Read only</span
      >{/if}
    <span class="state" role="status"
      >{loading ? "Opening…" : saving ? "Saving…" : notice}</span
    >
    <details bind:open={menu} bind:this={menuElement}>
      <summary
        aria-label="More code file actions"
        title="More code file actions">•••</summary
      >
      <div class="actions">
        <button
          type="button"
          disabled={!view || busy}
          on:click={() => discuss(true)}>Use whole file in chat</button
        >
        <button type="button" disabled={loading || saving} on:click={open}
          >Reload from disk</button
        >
        <button
          type="button"
          on:click={() => {
            menu = false;
            onOriginal();
          }}>Read original as a source</button
        >
      </div>
    </details>
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="code-surface" bind:this={host} style:height={height + "px"}></div>
</section>

<style>
  .source-pane {
    min-width: 0;
    color: var(--ke-text-primary);
    --ke-code-keyword: light-dark(#674579, #c7a8da);
    --ke-code-string: light-dark(#32624a, #9fc6ad);
    --ke-code-literal: light-dark(#835822, #d8b487);
  }
  .code-surface {
    min-width: 0;
    overflow: hidden;
  }
  .code-surface > :global(div),
  .code-surface :global(.cm-editor) {
    height: 100%;
  }
  .code-surface :global(.cm-editor) {
    background: transparent;
    color: var(--ke-text-primary);
  }
  .code-surface :global(.cm-editor.cm-focused) {
    outline: none;
  }
  .code-surface :global(.cm-scroller) {
    overflow: auto;
    font: 13px/1.65 var(--ke-font-mono, ui-monospace, monospace);
  }
  .code-surface :global(.cm-content) {
    padding: 8px 0;
    caret-color: var(--ke-text-primary);
  }
  .code-surface :global(.cm-gutters) {
    background: var(--ke-surface);
    color: var(--ke-text-tertiary);
    border: 0;
    padding-right: 6px;
  }
  .code-surface :global(.cm-lineNumbers .cm-gutterElement) {
    min-width: 2.5em;
    font-variant-numeric: tabular-nums;
  }
  .code-surface :global(.cm-cursor) {
    border-left-color: var(--ke-text-primary);
  }
  .code-surface :global(.cm-editor .cm-selectionBackground),
  .code-surface :global(.cm-editor.cm-focused .cm-selectionBackground) {
    background: color-mix(
      in srgb,
      var(--ke-text-primary) 18%,
      var(--ke-surface)
    );
  }
  .code-surface :global(.cm-panels) {
    color: var(--ke-text-primary);
    background: var(--ke-surface);
    font: 12px var(--ke-font-ui);
  }
  .code-surface :global(.cm-textfield),
  .code-surface :global(.cm-button) {
    color: inherit;
    background: var(--ke-surface-inset);
    border: 1px solid var(--ke-border);
  }
  details {
    position: relative;
    margin-left: auto;
  }
  summary {
    cursor: pointer;
    list-style: none;
    color: var(--ke-text-secondary);
    padding: 7px;
    font: 11px var(--ke-font-ui);
  }
  summary::-webkit-details-marker {
    display: none;
  }
  .actions {
    position: absolute;
    right: 0;
    top: 100%;
    z-index: 8;
    display: grid;
    width: max-content;
    padding: 4px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface);
  }
  .actions button {
    text-align: left;
  }
  .error {
    margin: 3px 8px;
    color: var(--ke-danger);
    font: 12px var(--ke-font-ui);
  }
</style>
