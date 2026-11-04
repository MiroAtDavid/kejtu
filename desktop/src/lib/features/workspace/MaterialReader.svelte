<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import { workspaceRequest, type Source } from "./workspace";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import CraftReader from "./CraftReader.svelte";
  import PdfReader from "./PdfReader.svelte";
  import type { MaterialPage as Page } from "./material-reader";
  export let courseId: string;
  export let initialFile = "";
  export let tabId = "";
  export let onChange: (dirty: boolean) => void = () => {};
  onMount(() => {
    if (initialFile) void select(initialFile);
  });
  export let folder: string | null = null;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => folder;
  export let onSource: (source: Source) => void = () => {};
  export let onPractice: (source: Source) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let requestedSource: Source | null = null;
  export let active = true;
  export let onFocus: () => void = () => {};
  export let busy = false;
  export let onFileChosen: () => void = () => {};
  export let onOriginal: ((path: string) => void) | undefined = undefined;
  export let onSelected: (file: string) => void = () => {};
  let zoom = "100%";
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
          `${Math.max(260, window.innerHeight - top - 12)}px`,
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
  let craft: CraftReader | undefined;
  let pdfTools = true;
  $: craftMode = pdfTools && pdf && !retained;
  async function simpleReader() {
    if (craft && !(await craft.confirmDiscard())) return;
    pdfTools = false;
    page = 1;
    await open(file);
  }
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
        view = body?.image ? "image" : "text";
      }
    } finally {
      if (generation === readGeneration) loading = false;
    }
  }
  export async function select(pathname: string) {
    if (craft && !(await craft.confirmDiscard())) return;
    onSource(null);
    readGeneration++;
    retained = null;
    requestedSource = null;
    selectedSignature = "";
    page = 1;
    body = null;
    current = null;
    file = pathname;
    // Every newly opened PDF starts in PDF Craft. The simple reader remains
    // an explicit fallback for this file, without becoming the next file's default.
    pdfTools = true;
    onSelected(file);
    view = /\.(pdf|png|jpe?g)$/i.test(file) ? "image" : "text";
    if (!pdfTools || !/\.pdf$/i.test(file)) void open(file);
    onFileChosen();
  }
  export function sourceDocument() {
    if (craftMode) {
      if (!craft) throw new Error("Wait for the PDF to finish opening.");
      return craft.sourceDocument();
    }
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
  export async function confirmDiscard() {
    return !craft || (await craft.confirmDiscard());
  }
  export async function close() {
    if (!(await confirmDiscard())) return false;
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
    if (retained || loading || !current || view === next) return;
    view = next;
    void open(file, false, true);
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
    pdfTools = true;
    if (!/\.pdf$/i.test(file)) await open(file);
  }
  function explain() {
    if (!source || !canUse) return;
    onDiscuss(
      `Explain this section resource. Section ${courseId}; retained source ${source.artifact_id}, original ${source.path}, page ${source.page}. Read the relevant exact source through university materials.snapshot before explaining.`,
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
    if (craft && !(await craft.confirmDiscard())) {
      requestedSource = null;
      sourceSwitch = false;
      return;
    }
    // A newer source may arrive while the discard question is open.
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
    {#if craftMode && file}
      {#key file}<CraftReader
          {chooseSaveFolder}
          bind:this={craft}
          {courseId}
          {file}
          {active}
          {onFocus}
          {busy}
          {onSource}
          {onPractice}
          {onDiscuss}
          {onChange}
          onFallback={simpleReader}
        />{/key}
    {:else}
      {#if file}
        <h2 class="reader-only">{file.split("/").pop()}</h2>
        <div class="reader-toolbar" aria-label="Resources reader controls">
          {#if !retained && pdf}
            <div class="view-switch" role="group" aria-label="Document view">
              <button
                class="icon-button"
                type="button"
                aria-label="Rendered page"
                title="Rendered page"
                aria-pressed={view === "image"}
                disabled={loading || !current}
                on:click={() => changeView("image")}
              >
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.2"
                  aria-hidden="true"
                  ><rect x="4" y="2" width="12" height="16" rx="1" /><path
                    d="m6 14 3-4 2 2 2-3 2 5"
                  /><circle cx="8" cy="6" r="1" /></svg
                >
              </button>
              <button
                class="icon-button"
                type="button"
                aria-label="Exact text"
                title="Exact text"
                aria-pressed={view === "text"}
                disabled={loading || !current}
                on:click={() => changeView("text")}
              >
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.2"
                  aria-hidden="true"
                  ><path d="M3 4h14M10 4v12M7 16h6M3 7V4m14 3V4" /></svg
                >
              </button>
            </div>
            <form
              class="page-navigation"
              on:submit|preventDefault={() => navigate(page)}
            >
              <button
                class="icon-button"
                type="button"
                aria-label="Previous page"
                title="Previous page"
                disabled={loading || page <= 1}
                on:click={() => navigate(page - 1)}>‹</button
              >
              <input
                aria-label="PDF page"
                type="number"
                min="1"
                max={count || 100000}
                bind:value={page}
                on:change={(event) =>
                  navigate(Number(event.currentTarget.value))}
                disabled={loading}
              />
              {#if count}<span class="page-total" aria-hidden="true"
                  >/ {count}</span
                >{/if}
              <button
                class="icon-button"
                type="button"
                aria-label="Next page"
                title="Next page"
                disabled={loading || !count || page >= count}
                on:click={() => navigate(page + 1)}>›</button
              >
            </form>
          {/if}
          {#if body?.representation === "pdf_image" && !retained}<ChoicePicker
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
            />{/if}
          {#if pdf && !retained}<button
              class="quiet"
              title="Open PDF selection, comments and highlighting tools"
              disabled={loading}
              on:click={() => {
                pdfTools = true;
                onSource(null);
              }}>PDF tools</button
            >{/if}
          {#if retained}<span class="retained-label"
              >Saved source{retained.page
                ? ` · page ${retained.page}`
                : ""}</span
            ><button class="quiet" disabled={loading} on:click={original}
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
              class="source-action explain"
              aria-label={pdf ? "Explain this page" : "Explain this material"}
              disabled={!canUse}
              on:click={explain}
              ><svg
                width="15"
                height="15"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                stroke-width="1.2"
                aria-hidden="true"><path d="M3 3h14v10H8l-4 4v-4H3V3Z" /></svg
              >Explain</button
            >
            <button
              class="source-action practice"
              aria-label={pdf ? "Practise this page" : "Practise this material"}
              disabled={!canUse}
              on:click={() => {
                if (source && canUse) onPractice(source);
              }}
              ><svg
                width="15"
                height="15"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                stroke-width="1.2"
                aria-hidden="true"
                ><path
                  d="M10 4C7 2 4 3 2 4v12c3-1 5-1 8 1 3-2 5-2 8-1V4c-2-1-5-2-8 0Zm0 0v13"
                /></svg
              >Practise</button
            >
          </div>
          {#if current}<details
              class="source-details"
              bind:this={sourceDetails}
            >
              <summary title="Source details" aria-label="Source details"
                ><svg
                  width="16"
                  height="16"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.2"
                  aria-hidden="true"
                  ><circle cx="10" cy="10" r="7" /><path
                    d="M10 9v5m0-8v1"
                  /></svg
                ></summary
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
            />{/key}
        {:else if body.image}<!-- Keyboard access to native scrolling. -->
          <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
          <div
            class="single-image"
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
            tabindex="0"
            aria-label="Exact material text">{body.content}</pre>{/if}
        {#if body.next_offset !== null && body.next_offset !== undefined}<button
            class="quiet continuation"
            disabled={loading}
            on:click={() => open(file, true)}
            >Read next exact page <span aria-hidden="true">↓</span></button
          >{/if}
      {:else if !loading && !error}<div class="reader-empty" tabindex="-1">
          <span aria-hidden="true">▤</span>
          <h3>Your material, ready to read</h3>
          <p>Choose a file. PDFs open at page 1; scroll to keep reading.</p>
        </div>{/if}
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
  .material-layout .icon-button {
    display: inline-grid;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 0;
    background: transparent;
    border-radius: 4px;
    flex-shrink: 0;
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
  .reader-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0 0 8px;
    min-height: 38px;
  }
  .view-switch {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .material-layout .view-switch button[aria-pressed="true"] {
    color: var(--ke-text-primary);
    background: color-mix(in srgb, var(--ke-marker-sage) 12%, transparent);
  }
  .page-navigation {
    display: flex;
    align-items: center;
    gap: 2px;
    margin: 0;
  }
  .material-layout .page-navigation input {
    width: 32px;
    text-align: center;
    font-size: 11px;
    padding: 5px 0;
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
    font-size: 11px;
    color: var(--ke-text-tertiary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .source-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-left: auto;
  }
  .material-layout .source-action {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 10px;
    font-size: 11px;
    border: 0;
    background: color-mix(in srgb, var(--ke-marker-blue) 10%, transparent);
  }
  .material-layout .source-action.practice {
    background: color-mix(in srgb, var(--ke-marker-sage) 12%, transparent);
  }
  .material-layout button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
  }
  .source-details {
    position: relative;
    flex-shrink: 0;
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .source-details summary {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    cursor: pointer;
    list-style: none;
    border-radius: 4px;
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
    border-radius: 6px;
    box-shadow: 0 5px 20px #0001;
    overflow-wrap: anywhere;
  }
  .source-popover strong {
    color: var(--ke-text-secondary);
    font-weight: 500;
  }
  .source-popover p {
    margin: 8px 0 0;
    font-size: 11px;
  }
  .retained-label {
    color: var(--ke-text-tertiary);
    font-size: 11px;
    align-self: center;
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
    border-radius: 4px;
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
  .reader-empty {
    padding: 56px 16px;
    color: var(--ke-text-tertiary);
    text-align: center;
    font-size: 12px;
  }
  .reader-empty > span {
    color: var(--ke-marker-sand);
    font-size: 32px;
  }
  .reader-empty h3 {
    color: var(--ke-text-secondary);
    font-size: 15px;
  }
  .reader-error {
    margin-bottom: 16px;
    font-size: 12px;
  }
  @media (max-width: 800px) {
    .reader-toolbar {
      gap: 6px;
    }
    .source-actions {
      margin-left: 0;
      order: 3;
      width: 100%;
    }
    .source-details {
      margin-left: auto;
    }
  }
</style>
