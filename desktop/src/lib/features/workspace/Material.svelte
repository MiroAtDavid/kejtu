<script lang="ts">
  import { tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import MaterialReader from "./MaterialReader.svelte";
  import MarkdownEditor from "./MarkdownEditor.svelte";
  import { prepareDocumentSource } from "./document-source";
  import WordReader from "./WordReader.svelte";
  import CodeEditor from "./CodeEditor.svelte";
  import MaterialFiles from "./MaterialFiles.svelte";
  import { materialPortal } from "./material-portal";
  import type { Source } from "./workspace";
  import { reorderDocuments, type OpenDocument } from "./document-tabs";
  import type { DocumentDockBounds } from "./document-dock";
  export let dockBounds: DocumentDockBounds | null = null;
  export let onDocumentsChange: (count: number) => void = () => {};
  export let courseId: string;
  export let courseTitle = "";
  export let fileSidebar: HTMLElement | null = null;
  export let folder: string | null = null;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => folder;
  export let onSource: (source: Source) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let requestedSource: Source | null = null;
  export let active = true;
  /** Show this module's tree in the sidebar while the reader stays inactive. */
  export let showFiles = false;
  export let onShowReader: () => boolean = () => true;
  export let busy = false;
  let documents: OpenDocument[] = [];
  // Reorder the tab strip alone. Moving an iframe's DOM node can reload it,
  // even when its keyed component survives, losing unsaved document work.
  $: onDocumentsChange(documents.length);
  $: dockStyle = dockBounds
    ? `position:fixed;left:${dockBounds.left}px;top:${dockBounds.top}px;width:${dockBounds.width}px;height:${dockBounds.height}px;`
    : undefined;
  let tabOrder: number[] = [];
  let draggedTab: number | null = null;
  let tabDrop: { id: number; after: boolean } | null = null;
  let serial = 0,
    changing = false;
  let activeTab: number | null = null;
  let readers: Record<number, MaterialReader> = {};
  let editors: Record<
    number,
    {
      confirmDiscard: () => Promise<boolean>;
    }
  > = {};
  let sources: Record<number, Source | null> = {};
  let lastPdf: number | null = null;
  $: if (active) onSource(sources[activeTab ?? -1] ?? null);
  $: sourceDocument = documents.find(
    (doc) =>
      doc.id === lastPdf && doc.kind === "reader" && /\.pdf$/i.test(doc.file),
  );
  $: selectedDocument = documents.find((doc) => doc.id === activeTab);
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
    if (!active || activeTab === null) return;
    void tick().then(() =>
      document
        .getElementById(`document-tab-${courseId}-${activeTab}`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" }),
    );
  }
  function selectTab(id: number) {
    activeTab = id;
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
    return doc;
  }
  let browser: MaterialFiles;
  $: fileTarget = active || showFiles ? fileSidebar : null;
  function chooseFile(path: string, code = false) {
    if (changing || !onShowReader()) return;
    if (code) openDocument(path, "code");
    else select(path);
    void tick().then(revealTab);
  }
  function newMarkdown() {
    if (!changing && onShowReader()) openDocument("", "md");
  }
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
  // The tab strip scrolls without a visible bar: a vertical wheel moves it
  // sideways, the chosen tab is revealed, and soft edges show hidden tabs.
  let moreBefore = false,
    moreAfter = false;
  function tabScroller(node: HTMLElement, _tabs: unknown) {
    let frame = 0;
    const edges = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const end = node.scrollWidth - node.clientWidth;
        moreBefore = node.scrollLeft > 1;
        moreAfter = node.scrollLeft < end - 1;
      });
    };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY))
        return;
      const end = node.scrollWidth - node.clientWidth;
      const step =
        event.deltaMode === 1
          ? event.deltaY * 16
          : event.deltaMode === 2
            ? event.deltaY * node.clientWidth
            : event.deltaY;
      // At either end the wheel belongs to the page again.
      if (
        end <= 0 ||
        (step < 0 && node.scrollLeft <= 0) ||
        (step > 0 && node.scrollLeft >= end)
      )
        return;
      event.preventDefault();
      node.scrollLeft += step;
    };
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(edges);
    observer?.observe(node);
    node.addEventListener("scroll", edges, { passive: true });
    node.addEventListener("wheel", wheel, { passive: false });
    edges();
    return {
      update: edges,
      destroy() {
        cancelAnimationFrame(frame);
        observer?.disconnect();
        node.removeEventListener("scroll", edges);
        node.removeEventListener("wheel", wheel);
      },
    };
  }
  function openDocument(file: string, kind: "docx" | "md" | "code") {
    if (changing) return;
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
  function select(path: string, readOriginal = false) {
    if (changing) return;
    if (!readOriginal && /\.(docx|md|markdown)$/i.test(path)) {
      openDocument(path, /\.docx$/i.test(path) ? "docx" : "md");
      return;
    }
    const existing = documents.find(
      (doc) => doc.file === path && doc.kind === "reader" && !doc.source,
    );
    if (existing) selectTab(existing.id);
    else addDocument({ file: path, kind: "reader" });
  }
  async function closeTab(doc: OpenDocument) {
    if (changing) return;
    changing = true;
    try {
      if (
        (doc.kind === "md" || doc.kind === "code") &&
        !(await editors[doc.id]?.confirmDiscard())
      )
        return;
      const index = tabOrder.indexOf(doc.id);
      documents = documents.filter((item) => item.id !== doc.id);
      tabOrder = tabOrder.filter((id) => id !== doc.id);
      if (activeTab === doc.id)
        activeTab = tabOrder[Math.min(index, tabOrder.length - 1)] ?? null;
      delete readers[doc.id];
      delete editors[doc.id];
      delete sources[doc.id];
      if (lastPdf === doc.id)
        lastPdf =
          documents.findLast(
            (item) => item.kind === "reader" && /\.pdf$/i.test(item.file),
          )?.id ?? null;
      await tick();
      if (activeTab !== null)
        document
          .getElementById(`document-tab-${courseId}-${activeTab}`)
          ?.focus();
    } finally {
      changing = false;
    }
  }
  function endTabDrag() {
    draggedTab = null;
    tabDrop = null;
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

<div class="workspace docked" style={dockStyle}>
  <div
    class="file-browser"
    class:in-sidebar={!!fileTarget}
    hidden={!active && !showFiles}
    use:materialPortal={fileTarget}
  >
    <MaterialFiles
      bind:this={browser}
      {courseId}
      {courseTitle}
      {selected}
      sidebar={!!fileTarget}
      disabled={busy || changing}
      onSelect={chooseFile}
      onNewMarkdown={newMarkdown}
    />
  </div>
  <div class="document-strip">
    <div
      class="document-tabs"
      class:more-before={moreBefore}
      class:more-after={moreAfter}
      role="tablist"
      aria-label="Open documents"
      use:tabScroller={orderedDocuments}
    >
      {#each orderedDocuments as doc (doc.id)}
        <div
          class="editor-tab"
          class:chosen={activeTab === doc.id}
          class:tab-dragging={draggedTab === doc.id}
          class:drop-before={tabDrop?.id === doc.id && !tabDrop.after}
          class:drop-after={tabDrop?.id === doc.id && tabDrop.after}
        >
          <button
            id={`document-tab-${courseId}-${doc.id}`}
            role="tab"
            aria-selected={activeTab === doc.id}
            aria-controls={`document-pane-${courseId}-${doc.id}`}
            tabindex={activeTab === doc.id ? 0 : -1}
            title={`${doc.label || doc.file || `Untitled.${doc.kind}`} · Drag to reorder. Alt+Shift+Left/Right reorders.`}
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
            aria-label={doc.kind === "md" || doc.kind === "code"
              ? "Close editable document"
              : "Close document"}
            disabled={changing}
            on:click={() => closeTab(doc)}
            ><Icon name="close" size={12} /></button
          >
        </div>
      {/each}
    </div>
  </div>
  <div class="panes" class:tab-drag-active={draggedTab !== null}>
    {#each documents as doc (doc.id)}
      {@const visible = doc.id === activeTab}
      <div
        id={`document-pane-${courseId}-${doc.id}`}
        class="document-pane"
        role="tabpanel"
        tabindex="0"
        aria-labelledby={`document-tab-${courseId}-${doc.id}`}
        hidden={!visible}
      >
        {#if doc.kind === "reader"}
          <MaterialReader
            bind:this={readers[doc.id]}
            {courseId}
            {folder}
            tabId={String(doc.id)}
            initialFile={doc.source ? "" : doc.file}
            requestedSource={doc.source ?? null}
            {busy}
            onSource={(source) => (sources = { ...sources, [doc.id]: source })}
            {onDiscuss}
            onOriginal={(path) => void select(path, true)}
            active={active && visible}
          />
        {:else if doc.kind === "code"}
          <CodeEditor
            bind:this={editors[doc.id]}
            {courseId}
            file={doc.file}
            active={active && visible}
            {busy}
            {onDiscuss}
            onChange={(dirty) => patch(doc.id, { dirty })}
            onOriginal={() => void select(doc.file, true)}
          />
        {:else if doc.kind === "md"}
          <MarkdownEditor
            bind:this={editors[doc.id]}
            {chooseSaveFolder}
            {courseId}
            file={doc.file}
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
            focused={activeTab === doc.id}
            onChange={(dirty) => patch(doc.id, { dirty })}
            onReadOriginal={(file) => void select(file, true)}
            onSaved={(path) => savedDocument(doc, path)}
          />
        {:else}
          <WordReader
            {courseId}
            file={doc.file}
            {busy}
            {onDiscuss}
            active={active && visible}
            onFocus={() => selectTab(doc.id)}
          />
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .workspace {
    min-width: 0;
    /* The reader owns scrolling; its viewport is fitted to these dock bounds. */
    overflow: clip;
    overscroll-behavior: contain;
    /* Composer pickers remain above the adjacent document. */
    z-index: 5;
    background: var(--ke-surface);
  }
  .file-browser.in-sidebar {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }
  .document-strip {
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 34px;
    margin: 0 0 2px;
    padding-right: 34px;
    border-bottom: 1px solid var(--ke-border);
  }
  .document-tabs {
    display: flex;
    align-items: stretch;
    gap: 4px;
    overflow-x: auto;
    overflow-y: hidden;
    overscroll-behavior-x: contain;
    /* No bar under the tabs on any engine; revealed tabs clear the soft edge. */
    scrollbar-width: none;
    scroll-padding-inline: 24px;
    min-width: 0;
    --tab-fade: 24px;
  }
  .document-tabs::-webkit-scrollbar {
    display: none;
    width: 0;
    height: 0;
  }
  .document-tabs.more-before {
    -webkit-mask-image: linear-gradient(
      to right,
      transparent,
      #000 var(--tab-fade)
    );
    mask-image: linear-gradient(to right, transparent, #000 var(--tab-fade));
  }
  .document-tabs.more-after {
    -webkit-mask-image: linear-gradient(
      to left,
      transparent,
      #000 var(--tab-fade)
    );
    mask-image: linear-gradient(to left, transparent, #000 var(--tab-fade));
  }
  .document-tabs.more-before.more-after {
    -webkit-mask-image: linear-gradient(
      to right,
      transparent,
      #000 var(--tab-fade),
      #000 calc(100% - var(--tab-fade)),
      transparent
    );
    mask-image: linear-gradient(
      to right,
      transparent,
      #000 var(--tab-fade),
      #000 calc(100% - var(--tab-fade)),
      transparent
    );
  }
  .document-tabs button {
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
  .document-tabs button:hover {
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
  .dirty {
    font-size: var(--ke-text-base);
    color: var(--ke-text-secondary);
  }
  .panes {
    min-width: 0;
    position: relative;
  }
  .document-pane {
    min-width: 0;
  }
  .tab-drag-active :global(iframe) {
    pointer-events: none;
  }
  [hidden] {
    display: none !important;
  }
</style>
