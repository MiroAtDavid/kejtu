<script lang="ts">
  import type { DocumentReference } from "./document-assist";
  import { tick, onMount, onDestroy } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import MaterialReader from "./MaterialReader.svelte";
  import MarkdownEditor from "./MarkdownEditor.svelte";
  import { prepareDocumentSource } from "./document-source";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import WordEditor from "./WordEditor.svelte";
  import MaterialFiles from "./MaterialFiles.svelte";
  import { materialPortal } from "./material-portal";
  import { workspaceRequest, type Source } from "./workspace";
  import { projectBrief } from "./project-brief";
  import {
    showDocument,
    closeDocument,
    splitDocuments,
    reorderDocuments,
    placeDocument,
    type DocumentLayout,
    type OpenDocument,
  } from "./document-tabs";
  export let courseId: string;
  export let courseTitle = "";
  export let projectPeriod = "";
  export let startProjectBrief = false;
  export let fileSidebar: HTMLElement | null = null;
  export let folder: string | null = null;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => folder;
  export let onSource: (source: Source) => void = () => {};
  export let onPractice: (source: Source) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let requestedSource: Source | null = null;
  export let active = true;
  export let busy = false;
  export let onFileChosen: () => void = () => {};
  export let onWideChange: (wide: boolean) => void = () => {};
  let fullWidth = false;
  let filesOpen = false;
  let documents: OpenDocument[] = [];
  // Reorder the tab strip alone. Moving an iframe's DOM node can reload it,
  // even when its keyed component survives, losing unsaved document work.
  let tabOrder: number[] = [];
  let draggedTab: number | null = null;
  let tabDrop: { id: number; after: boolean } | null = null;
  let paneDrop: "left" | "right" | null = null;
  let serial = 0,
    changing = false;
  let layout: DocumentLayout = { active: null, left: null, right: null };
  let wide = false,
    ratio = 50,
    dragging = false;
  let readers: Record<number, MaterialReader> = {};
  let editors: Record<
    number,
    {
      confirmDiscard: () => Promise<boolean>;
      openDiscussion?: (reference?: DocumentReference) => Promise<void>;
    }
  > = {};
  let sources: Record<number, Source | null> = {};
  let lastPdf: number | null = null;
  $: onWideChange(fullWidth);
  $: if (active) onSource(sources[layout.active ?? -1] ?? null);
  $: sourceDocument = documents.find(
    (doc) =>
      doc.id === lastPdf && doc.kind === "reader" && /\.pdf$/i.test(doc.file),
  );
  $: selectedDocument = documents.find((doc) => doc.id === layout.active);
  $: selected = selectedDocument?.lookupFile ?? selectedDocument?.file ?? "";
  function patch(id: number, values: Partial<OpenDocument>) {
    if (
      !documents.some(
        (doc) =>
          doc.id === id &&
          Object.entries(values).some(
            ([key, value]) => doc[key as keyof OpenDocument] !== value,
          ),
      )
    )
      return;
    documents = documents.map((doc) =>
      doc.id === id ? { ...doc, ...values } : doc,
    );
  }
  function savedDocument(doc: OpenDocument, path: string) {
    // A Save As receipt names this mounted document's current copy. Keep its
    // opening bytes stable, and only deduplicate paths inside this course root.
    const root = folder?.replace(/\/$/, "");
    const lookupFile =
      root && path.startsWith(root + "/") ? path.slice(root.length + 1) : "";
    patch(doc.id, { label: path.split("/").pop(), lookupFile });
    browser.refreshFiles();
  }
  function revealTab() {
    if (!active || layout.active === null) return;
    void tick().then(() =>
      document
        .getElementById(`document-tab-${courseId}-${layout.active}`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" }),
    );
  }
  function selectTab(id: number) {
    layout = showDocument(layout, id);
    revealTab();
    if (
      documents.some(
        (doc) =>
          doc.id === id && doc.kind === "reader" && /\.pdf$/i.test(doc.file),
      )
    )
      lastPdf = id;
  }
  function addDocument(values: Omit<OpenDocument, "id" | "dirty">) {
    const doc = { ...values, id: ++serial, dirty: false };
    documents = [...documents, doc];
    tabOrder = [...tabOrder, doc.id];
    selectTab(doc.id);
    onFileChosen();
    filesOpen = false;
    return doc;
  }
  let newKind = "";
  let mounted = false,
    starterAttempted = false,
    disposed = false,
    navigation = 0;
  const briefKey = () => `kejtu-next:project-brief:${courseId}`;
  onMount(() => {
    mounted = true;
  });
  onDestroy(() => {
    disposed = true;
  });
  $: if (mounted && active && startProjectBrief && !starterAttempted && !busy) {
    starterAttempted = true;
    void firstResourcesVisit();
  }
  async function firstResourcesVisit() {
    try {
      if (localStorage.getItem(briefKey())) return;
    } catch {
      /* The editor still works without browser storage. */
    }
    if (requestedSource || documents.length || changing) return;
    const run = navigation;
    const brief = projectBrief(courseTitle, folder, projectPeriod);
    let existing = false;
    if (folder) {
      try {
        let offset: number | null = 0;
        const seen = new Set<number>();
        while (offset !== null && !seen.has(offset)) {
          seen.add(offset);
          const page: {
            items: { name: string; path: string; kind: string }[];
            next_offset: number | null;
          } = await workspaceRequest("materials.list", {
            course_id: courseId,
            path: "",
            offset,
            limit: 100,
          });
          if (
            disposed ||
            run !== navigation ||
            !active ||
            documents.length ||
            requestedSource
          )
            return;
          if (
            page.items.some(
              (item) => item.kind === "file" && item.path === brief.name,
            )
          ) {
            existing = true;
            break;
          }
          offset = page.next_offset;
        }
      } catch {
        /* No file is written: a starter can be drafted before folder access is configured. */
      }
    }
    if (
      disposed ||
      run !== navigation ||
      !active ||
      documents.length ||
      requestedSource
    )
      return;
    if (existing) await openEditor(brief.name, "md");
    else {
      addDocument({
        file: "",
        kind: "md",
        label: brief.name,
        initialContent: brief.text,
      });
    }
    try {
      localStorage.setItem(briefKey(), "opened");
    } catch {
      /* No persistence claim when browser storage is unavailable. */
    }
  }
  let panes: HTMLDivElement;
  let browser: MaterialFiles;
  $: fileTarget = active && !fullWidth ? fileSidebar : null;
  $: divided = wide && layout.right !== null;
  $: if (requestedSource) {
    const source = requestedSource;
    requestedSource = null;
    const existing = documents.find(
      (doc) => doc.source?.artifact_id === source.artifact_id,
    );
    if (existing) selectTab(existing.id);
    else
      addDocument({
        kind: "reader",
        file:
          folder && source.path.startsWith(folder + "/")
            ? source.path.slice(folder.length + 1)
            : source.path,
        source,
      });
  }
  $: orderedDocuments = tabOrder
    .map((id) => documents.find((doc) => doc.id === id))
    .filter((doc): doc is OpenDocument => doc !== undefined);
  function measure(node: HTMLElement) {
    const update = () => {
      wide = node.getBoundingClientRect().width >= 820;
      revealTab();
    };
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(node);
    update();
    return { destroy: () => observer?.disconnect() };
  }
  async function openEditor(file = "", kind: "docx" | "md" = "docx") {
    if (changing) return;
    navigation++;
    const existing =
      file &&
      documents.find(
        (doc) => (doc.lookupFile ?? doc.file) === file && doc.kind === kind,
      );
    if (existing) {
      selectTab(existing.id);
      return existing;
    }
    return addDocument({ file, kind });
  }
  export async function openDiscussion(reference: DocumentReference) {
    if (reference.course_id !== courseId || !reference.file)
      throw new Error("This document is unavailable.");
    const doc = await openEditor(reference.file, "md");
    if (!doc) return;
    await tick();
    await editors[doc.id]?.openDiscussion?.(reference);
  }
  async function select(path: string, readOriginal = false) {
    if (changing) return;
    navigation++;
    if (!readOriginal && /\.(docx|md|markdown)$/i.test(path)) {
      await openEditor(path, /\.docx$/i.test(path) ? "docx" : "md");
      filesOpen = false;
      return;
    }
    const existing = documents.find(
      (doc) => doc.file === path && doc.kind === "reader" && !doc.source,
    );
    if (existing) selectTab(existing.id);
    else addDocument({ file: path, kind: "reader" });
    onFileChosen();
    filesOpen = false;
  }
  async function closeTab(doc: OpenDocument) {
    if (changing) return;
    changing = true;
    navigation++;
    try {
      const owner = doc.kind === "reader" ? readers[doc.id] : editors[doc.id];
      if (!owner || !(await owner.confirmDiscard())) return;
      layout = closeDocument(layout, doc.id, tabOrder);
      documents = documents.filter((item) => item.id !== doc.id);
      tabOrder = tabOrder.filter((id) => id !== doc.id);
      delete readers[doc.id];
      delete editors[doc.id];
      delete sources[doc.id];
      if (lastPdf === doc.id)
        lastPdf =
          documents.findLast(
            (item) => item.kind === "reader" && /\.pdf$/i.test(item.file),
          )?.id ?? null;
      await tick();
      if (layout.active !== null)
        document
          .getElementById(`document-tab-${courseId}-${layout.active}`)
          ?.focus();
      else
        document
          .getElementById(`empty-reader-${courseId}`)
          ?.querySelector<HTMLElement>(".reader-empty")
          ?.focus();
    } finally {
      changing = false;
    }
  }
  function toggleSplit() {
    layout = splitDocuments(layout, tabOrder);
  }
  function endTabDrag() {
    draggedTab = null;
    tabDrop = null;
    paneDrop = null;
  }
  function startTabDrag(event: DragEvent, id: number) {
    if (changing || !event.dataTransfer) {
      event.preventDefault();
      return;
    }
    draggedTab = id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-kejtu-document-tab", String(id));
  }
  function overTab(event: DragEvent, id: number) {
    if (draggedTab === null) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    const target = event.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    tabDrop = { id, after: event.clientX > rect.left + rect.width / 2 };
    const strip = target.closest<HTMLElement>(".document-tabs");
    if (strip) {
      const bounds = strip.getBoundingClientRect();
      if (event.clientX < bounds.left + 28) strip.scrollLeft -= 18;
      else if (event.clientX > bounds.right - 28) strip.scrollLeft += 18;
    }
  }
  function dropOnTab(event: DragEvent, id: number) {
    if (draggedTab === null) return;
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    tabOrder = reorderDocuments(
      tabOrder,
      draggedTab,
      id,
      event.clientX > rect.left + rect.width / 2,
    );
    endTabDrag();
  }
  function overPane(event: DragEvent, side: "left" | "right") {
    if (draggedTab === null) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    tabDrop = null;
    paneDrop = side;
  }
  function dropInPane(event: DragEvent, side: "left" | "right") {
    if (draggedTab === null) return;
    event.preventDefault();
    layout = placeDocument(layout, draggedTab, side, tabOrder);
    selectTab(draggedTab);
    endTabDrag();
  }
  function startDrag(e: PointerEvent) {
    if (e.button !== 0) return;
    dragging = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    if (!dragging) return;
    const rect = panes.getBoundingClientRect();
    ratio = Math.max(
      30,
      Math.min(70, (100 * (e.clientX - rect.left)) / rect.width),
    );
  }
  function resizeKeys(e: KeyboardEvent) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    ratio =
      e.key === "Home"
        ? 30
        : e.key === "End"
          ? 70
          : Math.max(
              30,
              Math.min(70, ratio + (e.key === "ArrowRight" ? 2 : -2)),
            );
  }
  function tabKeys(e: KeyboardEvent, id: number) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const index = tabOrder.indexOf(id);
    if (e.altKey && e.shiftKey && e.key.startsWith("Arrow")) {
      const next = index + (e.key === "ArrowRight" ? 1 : -1);
      if (next >= 0 && next < tabOrder.length) {
        tabOrder = reorderDocuments(tabOrder, id, tabOrder[next], next > index);
        void tick().then(() =>
          document.getElementById(`document-tab-${courseId}-${id}`)?.focus(),
        );
      }
      return;
    }
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? tabOrder.length - 1
          : (index + (e.key === "ArrowRight" ? 1 : -1) + tabOrder.length) %
            tabOrder.length;
    selectTab(tabOrder[next]);
    document
      .getElementById(`document-tab-${courseId}-${tabOrder[next]}`)
      ?.focus();
  }
</script>

<svelte:window
  on:dragend={endTabDrag}
  on:keydown={(event) => {
    if (event.key === "Escape") endTabDrag();
  }}
/>

<div class="workspace" use:measure>
  <div
    class="file-browser"
    hidden={!active || (fullWidth && !filesOpen)}
    use:materialPortal={fileTarget}
  >
    <MaterialFiles
      bind:this={browser}
      {courseId}
      {courseTitle}
      {selected}
      sidebar={!!fileTarget}
      onSelect={select}
    />
  </div>
  <div class="document-strip">
    {#if fullWidth}<button
        class="icon"
        aria-expanded={filesOpen}
        on:click={() => (filesOpen = !filesOpen)}>Files</button
      >{/if}
    <div class="document-tabs" role="tablist" aria-label="Open documents">
      {#each orderedDocuments as doc (doc.id)}
        <div
          class="editor-tab"
          class:chosen={layout.active === doc.id}
          class:tab-dragging={draggedTab === doc.id}
          class:drop-before={tabDrop?.id === doc.id && !tabDrop.after}
          class:drop-after={tabDrop?.id === doc.id && tabDrop.after}
        >
          <button
            id={`document-tab-${courseId}-${doc.id}`}
            role="tab"
            aria-selected={layout.active === doc.id}
            aria-controls={`document-pane-${courseId}-${doc.id}`}
            tabindex={layout.active === doc.id ? 0 : -1}
            title={`${doc.label || doc.file || `Untitled.${doc.kind}`} · Drag to reorder or move to a pane. Alt+Shift+Left/Right reorders.`}
            draggable={!changing}
            on:dragstart={(event) => startTabDrag(event, doc.id)}
            on:dragover={(event) => overTab(event, doc.id)}
            on:dragleave={() => (tabDrop = null)}
            on:drop={(event) => dropOnTab(event, doc.id)}
            on:keydown={(event) => tabKeys(event, doc.id)}
            on:click={() => selectTab(doc.id)}
          >
            <Icon name="file" size={14} /><span
              >{doc.label ||
                doc.file.split("/").pop() ||
                `Untitled.${doc.kind}`}{doc.source
                ? " · Saved source"
                : ""}</span
            >
            {#if doc.dirty}<span aria-label="Unsaved changes" class="dirty"
                >•</span
              >{/if}
          </button>
          <button
            class="close"
            title={`Close ${doc.label || doc.file || "document"}`}
            aria-label={doc.kind === "reader"
              ? "Close document"
              : "Close editable document"}
            disabled={changing}
            on:click={() => closeTab(doc)}
            ><Icon name="close" size={12} /></button
          >
        </div>
      {/each}
    </div>
    <div class="new-document">
      <ChoicePicker
        id={`new-document-${courseId}`}
        label="New document"
        placeholder="+ New"
        compact
        options={[
          {
            value: "md",
            label: "Markdown",
            detail: "Write and preview Markdown",
          },
          {
            value: "docx",
            label: "Word document",
            detail: "Edit with WordCraft",
          },
        ]}
        value={newKind}
        disabled={changing}
        onChange={async (kind) => {
          newKind = kind;
          await tick();
          newKind = "";
          void openEditor("", kind as "docx" | "md");
        }}
      />
    </div>
    <div class="view-controls">
      {#if documents.length > 1}<button
          class="icon"
          aria-label="Show documents side by side"
          aria-pressed={divided}
          title={wide
            ? "Show documents side by side"
            : "Use Wide view for side by side"}
          disabled={!wide}
          on:click={toggleSplit}><Icon name="sidebar" size={15} /></button
        >{/if}
      <button
        class="icon"
        aria-pressed={fullWidth}
        on:click={() => (fullWidth = !fullWidth)}
        >{fullWidth ? "Exit wide view" : "Wide view"}</button
      >
    </div>
  </div>
  {#if divided}<div class="split-choice">
      <button
        class="icon"
        on:click={() => {
          layout = { ...layout, left: layout.right, right: layout.left };
        }}>Swap sides</button
      >
      <ChoicePicker
        id={`beside-document-${courseId}`}
        label="Right document"
        compact
        value={String(layout.right)}
        options={orderedDocuments
          .filter((doc) => doc.id !== layout.left)
          .map((doc) => ({
            value: String(doc.id),
            label:
              doc.label || doc.file.split("/").pop() || `Untitled.${doc.kind}`,
          }))}
        onChange={(id) =>
          (layout = { ...layout, right: Number(id), active: layout.left })}
      />
    </div>{/if}
  <div
    bind:this={panes}
    class="panes"
    class:divided
    class:dragging
    class:tab-drag-active={draggedTab !== null}
    style:--split={`${ratio}%`}
  >
    {#if !documents.length}<div id={`empty-reader-${courseId}`}>
        <MaterialReader {courseId} {folder} {active} />
      </div>{/if}
    {#each documents as doc (doc.id)}
      {@const visible = divided
        ? doc.id === layout.left || doc.id === layout.right
        : doc.id === layout.active}
      <div
        id={`document-pane-${courseId}-${doc.id}`}
        class="document-pane"
        role="tabpanel"
        tabindex="0"
        aria-labelledby={`document-tab-${courseId}-${doc.id}`}
        hidden={!visible}
        style:grid-column={divided
          ? doc.id === layout.right
            ? "3"
            : "1"
          : undefined}
        on:focusin={() => {
          selectTab(doc.id);
        }}
        on:pointerdown={() => {
          selectTab(doc.id);
        }}
      >
        {#if doc.kind === "reader"}
          <MaterialReader
            bind:this={readers[doc.id]}
            {chooseSaveFolder}
            {courseId}
            {folder}
            tabId={String(doc.id)}
            initialFile={doc.source ? "" : doc.file}
            requestedSource={doc.source ?? null}
            {busy}
            onSource={(source) => (sources = { ...sources, [doc.id]: source })}
            {onPractice}
            {onDiscuss}
            onOriginal={(path) => void select(path, true)}
            onChange={(dirty) => patch(doc.id, { dirty })}
            onFocus={() => selectTab(doc.id)}
            active={active && visible}
          />
        {:else if doc.kind === "md"}
          <MarkdownEditor
            bind:this={editors[doc.id]}
            {chooseSaveFolder}
            {courseId}
            file={doc.file}
            initialContent={doc.initialContent ?? ""}
            initialName={doc.label ?? ""}
            courseFolder={folder}
            sourceName={sourceDocument?.file.split("/").pop() ?? ""}
            prepareSource={(signal) =>
              prepareDocumentSource(
                courseId,
                () => {
                  if (!sourceDocument || !readers[sourceDocument.id])
                    throw new Error("Open a PDF to use it as a source.");
                  return readers[sourceDocument.id].sourceDocument();
                },
                signal,
              )}
            active={active && visible}
            focused={layout.active === doc.id}
            onChange={(dirty) => patch(doc.id, { dirty })}
            onReadOriginal={(file) => void select(file, true)}
            onSaved={(path) => savedDocument(doc, path)}
          />
        {:else}
          <WordEditor
            bind:this={editors[doc.id]}
            {chooseSaveFolder}
            {courseId}
            file={doc.file}
            active={active && visible}
            onFocus={() => selectTab(doc.id)}
            onChange={(dirty) => patch(doc.id, { dirty })}
            onSaved={(path, format) => {
              if (format === "docx") savedDocument(doc, path);
              else browser.refreshFiles();
            }}
          />
        {/if}
      </div>
    {/each}
    {#if divided}
      <!-- Keyboard-operable splitter; pointer capture keeps dragging on its narrow rail. -->
      <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
      <div
        class="divider"
        role="separator"
        aria-label="Resize document panes"
        aria-orientation="vertical"
        aria-valuemin="30"
        aria-valuemax="70"
        aria-valuenow={Math.round(ratio)}
        tabindex="0"
        on:pointerdown={startDrag}
        on:pointermove={move}
        on:pointerup={() => (dragging = false)}
        on:lostpointercapture={() => (dragging = false)}
        on:keydown={resizeKeys}
      ></div>
    {/if}
    {#if draggedTab !== null && wide && documents.length > 1}
      <div
        class="pane-drop-zones"
        style:--drop-split={divided ? `${ratio}%` : "50%"}
      >
        {#each ["left", "right"] as side}
          <button
            class="pane-drop-zone"
            class:drop-ready={paneDrop === side}
            tabindex="-1"
            aria-label={`Drop document on ${side}`}
            on:dragover={(event) => overPane(event, side as "left" | "right")}
            on:dragleave={() => (paneDrop = null)}
            on:drop={(event) => dropInPane(event, side as "left" | "right")}
            ><span>Move to {side}</span></button
          >
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .workspace {
    min-width: 0;
  }
  .document-strip {
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 34px;
    margin: 0 0 8px;
    border-bottom: 1px solid var(--ke-border);
  }
  .document-tabs {
    display: flex;
    align-items: stretch;
    gap: 4px;
    overflow-x: auto;
    scrollbar-width: thin;
    min-width: 0;
  }
  .document-tabs button,
  .icon {
    display: flex;
    align-items: center;
    gap: 7px;
    background: none;
    border: 0;
    border-radius: var(--ke-radius-sm) var(--ke-radius-sm) 0 0;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: var(--ke-text-2xs);
    min-height: 32px;
    padding: 6px 10px;
    white-space: nowrap;
  }
  .document-tabs button[role="tab"] span:not(.dirty) {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .document-tabs button[aria-selected="true"],
  .editor-tab.chosen {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .document-tabs button:hover,
  .icon:hover {
    background: var(--ke-surface-inset);
  }
  .editor-tab {
    display: flex;
    align-items: center;
    flex: none;
    border-radius: var(--ke-radius-sm) var(--ke-radius-sm) 0 0;
    position: relative;
  }
  .editor-tab.tab-dragging {
    opacity: 0.55;
  }
  .editor-tab.drop-before::before,
  .editor-tab.drop-after::after {
    content: "";
    position: absolute;
    top: 5px;
    bottom: 5px;
    width: 2px;
    background: var(--ke-accent);
    border-radius: 2px;
  }
  .editor-tab.drop-before::before {
    left: -2px;
  }
  .editor-tab.drop-after::after {
    right: -2px;
  }
  .document-tabs button[draggable="true"] {
    cursor: grab;
  }
  .editor-tab .close {
    padding: 5px;
    min-height: 24px;
    margin-right: 4px;
  }
  .icon {
    padding: 7px;
    flex: none;
    border-radius: var(--ke-radius-sm);
  }
  .new-document {
    flex: none;
    width: 100px;
  }
  .new-document :global(.choice-options) {
    width: 220px;
  }
  .view-controls {
    display: flex;
    margin-left: auto;
    flex: none;
  }
  .split-choice {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 8px;
  }
  .split-choice :global(.choice) {
    max-width: 280px;
  }
  .icon:disabled {
    opacity: 0.4;
  }
  .dirty {
    font-size: var(--ke-text-base);
    color: var(--ke-text-secondary);
  }
  .panes {
    min-width: 0;
    position: relative;
  }
  .pane-drop-zones {
    position: absolute;
    inset: 0;
    z-index: 8;
    display: grid;
    grid-template-columns: var(--drop-split) 1fr;
    gap: 4px;
    pointer-events: none;
  }
  .pane-drop-zone {
    pointer-events: auto;
    background: color-mix(in srgb, var(--ke-surface) 75%, transparent);
    color: var(--ke-text-secondary);
    border: 1px dashed var(--ke-border-strong);
    border-radius: var(--ke-radius-md);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 24px;
    font: inherit;
    font-size: var(--ke-text-sm);
  }
  .pane-drop-zone.drop-ready {
    background: color-mix(in srgb, var(--ke-accent-soft) 85%, transparent);
    border-color: var(--ke-accent);
  }
  .panes.divided {
    display: grid;
    grid-template-columns: minmax(0, calc(var(--split) - 4.5px)) 9px minmax(
        0,
        1fr
      );
  }
  .document-pane {
    grid-row: 1;
    min-width: 0;
  }
  .divider {
    grid-column: 2;
    grid-row: 1;
    cursor: col-resize;
    touch-action: none;
    position: relative;
  }
  .divider::after {
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    left: 4px;
    background: var(--ke-border);
  }
  .divider:hover::after,
  .divider:focus-visible::after {
    background: var(--ke-text-tertiary);
  }
  .dragging :global(iframe),
  .tab-drag-active :global(iframe) {
    pointer-events: none;
  }
  [hidden] {
    display: none !important;
  }
</style>
