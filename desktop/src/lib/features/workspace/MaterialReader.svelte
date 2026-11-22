<script lang="ts">
  import { documentViewportHeight } from "./document-dock";
  import { getContext, onDestroy, onMount, tick } from "svelte";
  import { readable } from "svelte/store";
  import { MATERIAL_MARK, type MaterialMark } from "./material-mark";
  import { workspaceRequest, type Source } from "./workspace";
  import { practiceRequest } from "$lib/features/chat/study-requests";
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import "./document-chrome.css";
  import PdfReader from "./PdfReader.svelte";
  import type { MaterialPage as Page } from "./material-reader";
  export let courseId: string;
  export let initialFile = "";
  export let tabId = "";
  onMount(() => {
    if (initialFile) void select(initialFile);
  });
  export let folder: string | null = null;
  export let onSource: (source: Source) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let requestedSource: Source | null = null;
  export let active = true;
  export let busy = false;
  export let onFileChosen: () => void = () => {};
  export let onOriginal: ((path: string) => void) | undefined = undefined;
  export let onSelected: (file: string) => void = () => {};
  let zoom = "100%";
  // The chat draft owns a marked passage; without a chat there is no marking.
  const marking = getContext<MaterialMark | undefined>(MATERIAL_MARK);
  const markedPassage = marking?.passage ?? readable(null);
  function fitDocument(node: HTMLElement) {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!node.getClientRects().length) return;
        const toolbar = node.querySelector<HTMLElement>(".reader-toolbar");
        const top = toolbar
          ? toolbar.getBoundingClientRect().bottom + 8
          : node.getBoundingClientRect().top;
        node.style.setProperty(
          "--ke-reader-height",
          `${documentViewportHeight(node, top)}px`,
        );
      });
    };
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(node);
    window.addEventListener("resize", update);
    update();
    return {
      destroy() {
        cancelAnimationFrame(frame);
        observer?.disconnect();
        window.removeEventListener("resize", update);
      },
    };
  }
  let file = "",
    page = 1,
    view = "image",
    loading = false,
    error = "";
  let body: Page | null = null,
    current: Page | null = null;
  let retained: Source | null = null;
  let selectedSignature = "";
  let readGeneration = 0;
  let reader: PdfReader | undefined;
  let sourceDetails: HTMLDetailsElement | undefined;
  function dismissDetails(event: PointerEvent | KeyboardEvent) {
    if (!sourceDetails?.open) return;
    if (event instanceof KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      if (sourceDetails.contains(document.activeElement))
        sourceDetails.querySelector("summary")?.focus({ preventScroll: true });
    } else if (
      event.target instanceof Node &&
      sourceDetails.contains(event.target)
    )
      return;
    sourceDetails.open = false;
  }
  $: pdf = /\.pdf$/i.test(file);
  $: count = body?.page_count;
  $: source = current?.source;
  $: canUse = !!source && !loading && !busy;
  $: pageLabel = source?.page
    ? `Page ${source.page}${count ? ` of ${count}` : ""}`
    : "Selected material";
  function accept(value: Page | null, number = page) {
    current = value;
    page = number;
    if (value) onSource(value.source);
  }
  async function open(pathname: string, append = false, sameVersion = false) {
    const generation = ++readGeneration;
    const previous = body;
    const expected = current?.source;
    // Text continuations use the delivered immutable projection, never the live file.
    const source = append ? previous?.source : retained;
    const nextOffset = append ? previous?.next_offset : 0;
    loading = true;
    error = "";
    try {
      if (append && (!previous || nextOffset == null)) return;
      const result = source
        ? await workspaceRequest<Page>("materials.snapshot", {
            course_id: courseId,
            artifact_id: source.artifact_id,
            offset: nextOffset,
          })
        : await workspaceRequest<Page>("materials.read", {
            course_id: courseId,
            path: pathname,
            page,
            view,
            offset: nextOffset,
          });
      if (generation !== readGeneration) return;
      if (
        append &&
        (result.source.artifact_id !== previous!.source.artifact_id ||
          result.source.sha256 !== previous!.source.sha256 ||
          result.source.path !== previous!.source.path ||
          result.source.page !== previous!.source.page ||
          result.offset !== nextOffset ||
          result.representation !== previous!.representation)
      )
        throw new Error(
          "The continuation does not match the displayed source. No pages were combined.",
        );
      if (
        sameVersion &&
        expected &&
        (result.source.path !== expected.path ||
          result.source.sha256 !== expected.sha256)
      )
        throw new Error(
          "This document changed. Reopen the file to read its current version.",
        );
      body = append
        ? {
            ...result,
            content: (previous?.content || "") + (result.content || ""),
          }
        : result;
      accept(body, result.source.page || 1);
    } catch (caught) {
      if (generation === readGeneration) {
        error = String(caught);
        if (body) view = body.image ? "image" : "text";
      }
    } finally {
      if (generation === readGeneration) loading = false;
    }
  }
  export async function select(pathname: string) {
    onSource(null);
    readGeneration++;
    retained = null;
    requestedSource = null;
    selectedSignature = "";
    page = 1;
    body = null;
    current = null;
    file = pathname;
    onSelected(file);
    view = /\.(pdf|png|jpe?g)$/i.test(file) ? "image" : "text";
    void open(file);
    onFileChosen();
  }
  export function sourceDocument() {
    if (retained)
      throw new Error(
        "Open the current original PDF before using it as an editing source.",
      );
    if (!pdf || loading || !current || !body?.page_count)
      throw new Error(
        "Wait for the PDF to finish opening before using it as a source.",
      );
    return {
      file,
      path: current.source.path,
      sha256: current.source.sha256,
      page: current.source.page,
      pages: body.page_count,
    };
  }
  export async function close() {
    // Late reads belong to the closed document and must not restore its source.
    readGeneration++;
    requestedSource = null;
    retained = null;
    selectedSignature = "";
    file = "";
    body = null;
    current = null;
    page = 1;
    loading = false;
    error = "";
    onSource(null);
    onSelected("");
    return true;
  }
  function navigate(number: number) {
    if (
      !Number.isInteger(number) ||
      number < 1 ||
      number > (count || 100000) ||
      loading
    ) {
      page = current?.source.page || 1;
      return;
    }
    page = number;
    if (view === "image" && reader && !retained) void reader.go(number);
    else void open(file, false, true);
  }
  function changeView(next: "image" | "text") {
    if (retained || loading || (!current && !error) || view === next) return;
    view = next;
    void open(file, false, !!current);
  }
  async function original() {
    if (retained && onOriginal) {
      onOriginal(file);
      return;
    }
    retained = null;
    page = 1;
    body = null;
    current = null;
    view = /\.(pdf|png|jpe?g)$/i.test(file) ? "image" : "text";
    await open(file);
  }
  function explain() {
    if (!source || !canUse) return;
    onDiscuss(
      `Explain this module resource. Module ${courseId}; retained source ${source.artifact_id}, original ${source.path}, page ${source.page}. Read the relevant exact source through university materials.snapshot before explaining.`,
      source,
    );
  }
  // Practice happens in the conversation: ask it for one source-linked question.
  function practise() {
    if (!source || !canUse) return;
    onDiscuss(
      practiceRequest({
        courseId,
        artifactId: source.artifact_id,
        path: source.path,
        page: source.page,
      }),
      source,
    );
  }
  onDestroy(() => {
    readGeneration++;
  });
  let sourceSwitch = false;
  async function selectRetained() {
    sourceSwitch = true;
    // This can start inside a reactive flush. Move source/file assignments to
    // the next microtask so dependent PDF mode updates run for the saved path.
    await tick();
    if (!requestedSource) {
      sourceSwitch = false;
      return;
    }
    selectedSignature = requestedSource.artifact_id;
    retained = requestedSource;
    body = null;
    current = null;
    page = requestedSource.page || 1;
    file =
      folder && requestedSource.path.startsWith(folder + "/")
        ? requestedSource.path.slice(folder.length + 1)
        : requestedSource.path;
    onSelected(file);
    sourceSwitch = false;
    void open(file);
  }
  $: if (
    requestedSource &&
    requestedSource.artifact_id !== selectedSignature &&
    !sourceSwitch
  )
    void selectRetained();
</script>

<svelte:window on:pointerdown={dismissDetails} on:keydown={dismissDetails} />

<div class="material-layout">
  <section use:fitDocument class="document" aria-label="Resources reader">
    {#if file}
      <h2 class="reader-only">{file.split("/").pop()}</h2>
      <div
        class="reader-toolbar document-toolbar"
        aria-label="Resources reader controls"
      >
        {#if !retained && pdf}
          <div class="mode-rail" role="group" aria-label="Document view">
            <button
              type="button"
              aria-label="Rendered page"
              title="Rendered page"
              aria-pressed={view === "image"}
              disabled={loading || (!current && !error)}
              on:click={() => changeView("image")}
              ><Icon name="image" size={17} /></button
            >
            <button
              type="button"
              aria-label="Exact text"
              title="Exact text"
              aria-pressed={view === "text"}
              disabled={loading || (!current && !error)}
              on:click={() => changeView("text")}
              ><Icon name="text" size={17} /></button
            >
          </div>
          <form
            class="page-navigation"
            on:submit|preventDefault={() => navigate(page)}
          >
            <button
              class="icon"
              type="button"
              aria-label="Previous page"
              title="Previous page"
              disabled={loading || page <= 1}
              on:click={() => navigate(page - 1)}
              ><Icon name="left" size={16} /></button
            >
            <input
              aria-label="PDF page"
              type="number"
              min="1"
              max={count || 100000}
              bind:value={page}
              on:change={(event) => navigate(Number(event.currentTarget.value))}
              disabled={loading}
            />
            {#if count}<span class="page-total" aria-hidden="true"
                >/ {count}</span
              >{/if}
            <button
              class="icon"
              type="button"
              aria-label="Next page"
              title="Next page"
              disabled={loading || !count || page >= count}
              on:click={() => navigate(page + 1)}
              ><Icon name="right" size={16} /></button
            >
          </form>
        {/if}
        {#if body?.representation === "pdf_image" && !retained}<div
            class="page-size"
          >
            <ChoicePicker
              id={`reader-size-${courseId}-${tabId}`}
              label="Page size"
              compact
              bind:value={zoom}
              options={[
                { value: "width", label: "Fit width" },
                { value: "page", label: "Fit page" },
                { value: "75%", label: "75%" },
                { value: "100%", label: "100%" },
                { value: "125%", label: "125%" },
              ]}
            />
          </div>{/if}
        {#if retained}<span class="state"
            >Saved source{retained.page ? ` · page ${retained.page}` : ""}</span
          ><button type="button" disabled={loading} on:click={original}
            >Read current original <span aria-hidden="true">↗</span></button
          >{/if}
        <span class="action-source reader-only" aria-live="polite"
          >{current ? pageLabel : `Reading page ${page}…`}</span
        >
        <div
          class="source-actions"
          aria-label="Actions for the displayed material"
        >
          <button
            type="button"
            class="source-action"
            aria-label={pdf ? "Explain this page" : "Explain this material"}
            disabled={!canUse}
            on:click={explain}><Icon name="comment" size={16} />Explain</button
          >
          <button
            type="button"
            class="source-action"
            aria-label={pdf ? "Practise this page" : "Practise this material"}
            disabled={!canUse}
            on:click={practise}><Icon name="book" size={16} />Practise</button
          >
        </div>
        {#if current}<details class="source-details" bind:this={sourceDetails}>
            <summary title="Source details" aria-label="Source details"
              ><Icon name="info" size={16} /></summary
            >
            <div class="source-popover">
              <strong>{file.split("/").pop()}</strong>
              <p>
                {current.source.path}{current.source.page
                  ? ` · page ${current.source.page}`
                  : ""}
              </p>
              <p>Source SHA-256: {current.source.sha256}</p>
            </div>
          </details>{/if}
      </div>
    {/if}
    {#if error}<div class="reader-error">
        <p class="error" role="alert">{error}</p>
        <button class="quiet" disabled={loading} on:click={original}
          >Reopen file</button
        ><button
          class="quiet"
          on:click={() =>
            onDiscuss(
              `Use the currently permitted document tools to read relevant material for course ${courseId}, file ${file}. Preserve original file/page references; report unavailable access.`,
            )}>Ask Kejtu to use document tools</button
        >
      </div>{/if}
    {#if current}
      {#if current.current_file_status && current.current_file_status !== "unchanged"}<p
          class="notice"
        >
          Retained exact source. Current file: {current.current_file_status}.
        </p>{/if}
      {#if current.notice}<p class="notice">{current.notice}</p>{/if}
    {/if}
    {#if loading}<p class="muted" role="status">Reading…</p>{/if}
    {#if body}
      {#if !retained && body.representation === "pdf_image" && body.page_count && body.image}
        {#key body.source.artifact_id}<PdfReader
            bind:this={reader}
            {courseId}
            {file}
            {zoom}
            first={body}
            {active}
            onPage={accept}
            marked={$markedPassage}
            onMark={marking?.mark}
            markDisabled={busy}
          />{/key}
      {:else if body.image}<!-- Keyboard access to native scrolling. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <div
          class="single-image"
          use:quietScrollbar
          tabindex="0"
          role="region"
          aria-label="Original image"
        >
          <img
            alt={`Original ${body.source.path}, page ${body.source.page || 1}`}
            src={`data:${body.image.mime_type};base64,${body.image.data}`}
          />
        </div>
      {:else}<!-- Keyboard access to native scrolling. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <pre
          use:quietScrollbar
          tabindex="0"
          aria-label="Exact material text">{body.content}</pre>{/if}
      {#if body.next_offset !== null && body.next_offset !== undefined}<button
          class="quiet continuation"
          disabled={loading}
          on:click={() => open(file, true)}
          >Read next exact page <span aria-hidden="true">↓</span></button
        >{/if}
    {/if}
  </section>
</div>

<style>
  .material-layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    align-items: start;
  }
  .document {
    min-width: 0;
  }
  .reader-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  /* The shared document toolbar (document-chrome.css), wrapping when narrow. */
  :global(.workspace) .reader-toolbar {
    flex-wrap: wrap;
    height: auto;
  }
  .page-navigation {
    display: flex;
    align-items: center;
    gap: 2px;
    margin: 0 0 0 6px;
  }
  .material-layout .page-navigation input {
    width: 28px;
    height: 32px;
    text-align: center;
    font: 11px var(--ke-font-ui);
    font-variant-numeric: tabular-nums;
    color: var(--ke-text-primary);
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    appearance: textfield;
    -moz-appearance: textfield;
  }
  .page-navigation input::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  .page-total {
    padding-right: 2px;
    font: 11px var(--ke-font-ui);
    color: var(--ke-text-tertiary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  /* Page size keeps its list, dressed as a toolbar button. */
  .page-size {
    flex: none;
    margin-left: 6px;
  }
  .page-size :global(.choice-picker .choice-trigger) {
    height: 32px;
    min-height: 32px;
    padding: 0 8px 0 10px;
    gap: 6px;
    color: var(--ke-text-secondary);
    font: 11px var(--ke-font-ui);
    background: transparent;
  }
  .page-size :global(.choice-picker .choice-trigger:hover:not(:disabled)),
  .page-size :global(.choice-picker .choice-trigger[aria-expanded="true"]) {
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  .page-size :global(.choice-picker .choice-trigger:focus-visible) {
    outline-offset: -2px;
  }
  .source-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 2px;
    margin-left: auto;
  }
  :global(.workspace) .document-toolbar .source-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .material-layout button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  .source-details {
    position: relative;
    flex-shrink: 0;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
  }
  .source-details summary {
    display: grid;
    place-items: center;
    width: 30px;
    height: 32px;
    cursor: pointer;
    list-style: none;
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
  }
  .source-details summary:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -2px;
  }
  .source-details summary::-webkit-details-marker {
    display: none;
  }
  .source-details summary:hover,
  .source-details[open] summary {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .source-popover {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    width: min(320px, calc(100vw - 48px));
    padding: 14px;
    box-sizing: border-box;
    z-index: 4;
    background: var(--ke-surface-raised);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    box-shadow: var(--ke-overlay-shadow);
    overflow-wrap: anywhere;
    transform-origin: top right;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .source-popover strong {
    color: var(--ke-text-secondary);
    font-weight: 500;
  }
  .source-popover p {
    margin: 8px 0 0;
    font-size: var(--ke-text-2xs);
  }
  .document pre {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font:
      13px/1.8 ui-monospace,
      monospace;
    background: var(--ke-surface-inset);
    padding: 18px;
    border-radius: var(--ke-radius-sm);
    max-height: var(--ke-reader-height, 64vh);
    overflow-y: auto;
  }
  .single-image {
    max-height: var(--ke-reader-height, 64vh);
    overflow: auto;
  }
  .single-image img {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
  }
  .continuation {
    margin-top: 12px;
  }
  .reader-error {
    margin-bottom: 16px;
    font-size: var(--ke-text-xs);
  }
  @media (max-width: 800px) {
    /* A second row: Explain and Practise, with source details at its end. */
    .source-actions {
      margin-left: 0;
      order: 3;
      flex: 1 0 calc(100% - 34px);
    }
    .source-details {
      order: 4;
    }
  }
</style>
