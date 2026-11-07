<script lang="ts">
  import "./document-chrome.css";
  import { onMount } from "svelte";
  import ReaderNotice from "./ReaderNotice.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { identity } from "$lib/api/client";
  import { workspaceRequest, type Source } from "./workspace";
  import {
    readCoursePdf,
    readerState,
    MAX_PDF_BYTES,
    type ReaderState,
    type DocumentBytes,
  } from "./craft-document";
  import { registerReader, refreshReaderDrafts } from "./reader-drafts";
  import { documentDirectory } from "./document-save";
  import type { MaterialPage } from "./material-reader";
  export let courseId: string;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => null;
  export let file: string;
  export let active = true;
  export let onFocus: () => void = () => {};
  export let onChange: (dirty: boolean) => void = () => {};
  export let busy = false;
  export let onSource: (source: Source | null) => void = () => {};
  export let onPractice: (source: Source) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let onFallback: () => void = () => {};
  const token = identity();
  let frame: HTMLIFrameElement;
  let container: HTMLDivElement;
  let documentBytes: DocumentBytes | null = null;
  let savedPath: string | null = null;
  let state: ReaderState | null = null;
  let ready = false,
    loaded = false,
    painted = false,
    disposed = false,
    dirty = false,
    saving = false,
    acting = false;
  let error = "",
    notice = "",
    height = 600;
  let sourceNoticeDismissed = false;
  let pending: { bytes: ArrayBuffer; name: string } | null = null;
  let startTimer: ReturnType<typeof setTimeout>;
  let inspecting: {
    id: string;
    resolve: (s: ReaderState) => void;
    reject: (e: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  } | null = null;
  $: {
    dirty;
    pending;
    saving;
    refreshReaderDrafts();
    onChange(dirty || !!pending || saving);
  }
  $: bound = state?.token === token && !state.modified && state.page > 0;
  $: if (bound) sourceNoticeDismissed = false;
  $: {
    notice;
    error;
    sourceNoticeDismissed;
    resize();
  }
  $: usable = loaded && bound && !busy && !acting && !saving;
  $: if (active) resize();
  let detachFrame: (() => void) | undefined;
  function frameLoaded() {
    detachFrame?.();
    const doc = frame?.contentDocument;
    if (!doc) return;
    const interact = () => {
      if (active) onFocus();
      window.dispatchEvent(new Event("kejtu-reader-interaction"));
    };
    const events = ["keydown", "pointerdown", "wheel", "touchstart"];
    events.forEach((event) =>
      doc.addEventListener(event, interact, { capture: true, passive: true }),
    );
    detachFrame = () =>
      events.forEach((event) => doc.removeEventListener(event, interact, true));
  }
  let resizeFrame = 0;
  function resize() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      if (disposed || !active || !container?.getClientRects().length) return;
      const next = Math.max(
        260,
        innerHeight - container.getBoundingClientRect().top - 12,
      );
      if (height !== next) height = next;
    });
  }
  function command(command: string) {
    if (loaded && ready && !saving) post({ type: "pdfcraft:command", command });
  }
  function post(value: object, transfer: Transferable[] = []) {
    frame?.contentWindow?.postMessage(value, location.origin, transfer);
  }
  function theme() {
    const root = document.documentElement;
    const chosen = root.dataset.theme;
    const style = getComputedStyle(root);
    // The canvas cannot inherit CSS. Share interface colours, never PDF paper/content.
    const colors = Object.fromEntries(
      [
        "surface",
        "surface-raised",
        "surface-inset",
        "border",
        "text-primary",
        "text-secondary",
        "text-tertiary",
        "accent",
        "on-accent",
        "accent-soft",
      ].map((name) => [name, style.getPropertyValue(`--ke-${name}`).trim()]),
    );
    post({
      type: "pdfcraft:theme",
      colors,
      theme:
        chosen === "dark" ||
        (!chosen && matchMedia("(prefers-color-scheme: dark)").matches)
          ? "dark"
          : "light",
    });
  }
  function send() {
    if (!ready || !documentBytes || loaded) return;
    // Transfer once; the verified identity remains here while PdfCraft owns the bytes.
    post(
      {
        type: "pdfcraft:open",
        token,
        name: file.split("/").pop(),
        bytes: documentBytes.bytes,
      },
      [documentBytes.bytes],
    );
    loaded = true;
    theme();
  }
  async function inspect(): Promise<ReaderState> {
    if (!ready || !loaded) throw new Error("The reader is not ready.");
    if (inspecting) throw new Error("Wait for the current reader action.");
    return new Promise((resolve, reject) => {
      const id = identity();
      const timer = setTimeout(() => {
        inspecting = null;
        reject(
          new Error("The reader did not respond. Your document remains open."),
        );
      }, 3000);
      inspecting = { id, resolve, reject, timer };
      post({ type: "pdfcraft:inspect", request: id });
    });
  }
  export async function confirmDiscard(): Promise<boolean> {
    if (saving || acting) {
      notice = "Finish the current reader action first.";
      return false;
    }
    if (loaded && ready) {
      try {
        const value = await inspect();
        dirty = value.dirty;
      } catch {
        return window.confirm(
          "The reader is not responding. Closing it may discard unsaved document edits. Continue?",
        );
      }
    }
    return (
      !(dirty || pending) ||
      window.confirm(
        "This reader has unsaved edits or a PDF copy waiting to be saved. Discard them and continue?",
      )
    );
  }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (dirty || pending || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  }
  async function saveCopy() {
    if (!pending || saving) return;
    saving = true;
    error = "";
    notice = "";
    const copy = pending;
    try {
      const initial =
        documentDirectory(savedPath ?? documentBytes?.path) ??
        (await chooseSaveFolder());
      if (initial === undefined || disposed) return;
      const array = new Uint8Array(copy.bytes);
      let binary = "";
      for (let i = 0; i < array.length; i += 32768)
        binary += String.fromCharCode(...array.subarray(i, i + 32768));
      const path = await invoke<string | null>("save_pdf_copy", {
        name: copy.name,
        data: btoa(binary),
        initial,
      });
      if (path) {
        savedPath = path;
        if (pending === copy) {
          pending = null;
          post({ type: "pdfcraft:saved" });
        }
        notice = "PDF copy saved. The course original is unchanged.";
      } else notice = "Save cancelled. Your PDF copy is still here.";
    } catch (caught) {
      error = String(caught);
    } finally {
      saving = false;
    }
  }
  function message(event: MessageEvent) {
    if (
      event.origin !== location.origin ||
      event.source !== frame?.contentWindow ||
      !event.data ||
      typeof event.data !== "object"
    )
      return;
    const value = event.data;
    if (value.type === "pdfcraft:ready") {
      if (value.bridgeVersion !== 1 || value.version !== "0.9.0") {
        error = "This reader bundle is incompatible. Use the simple reader.";
        return;
      }
      ready = true;
      send();
    } else if (value.type === "pdfcraft:state") {
      const next = readerState(value);
      if (!next) return;
      if (
        !state ||
        state.token !== next.token ||
        state.page !== next.page ||
        next.modified
      )
        onSource(null);
      state = next;
      dirty = next.dirty;
      if (next.token === token) {
        clearTimeout(startTimer);
        if (!painted)
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              if (!disposed) painted = true;
            }),
          );
      }
      if (next.token !== token || next.modified) onSource(null);
      if (inspecting && next.request === inspecting.id) {
        const wait = inspecting;
        inspecting = null;
        clearTimeout(wait.timer);
        wait.resolve(next);
      }
    } else if (
      value.type === "pdfcraft:dirty" &&
      typeof value.dirty === "boolean"
    )
      dirty = value.dirty;
    else if (value.type === "pdfcraft:failed") {
      error =
        typeof value.error === "string"
          ? value.error
          : "The reader could not start.";
      clearTimeout(startTimer);
    } else if (value.type === "pdfcraft:save") {
      if (
        !(value.bytes instanceof ArrayBuffer) ||
        value.bytes.byteLength > MAX_PDF_BYTES ||
        value.bytes.byteLength < 5 ||
        new TextDecoder().decode(new Uint8Array(value.bytes, 0, 5)) !== "%PDF-"
      ) {
        error = "The reader returned an unsupported PDF copy.";
        return;
      }
      if (saving) {
        error = "Finish saving the current copy before saving again.";
        return;
      }
      pending = {
        bytes: value.bytes,
        name: typeof value.name === "string" ? value.name : "document.pdf",
      };
      void saveCopy();
    }
  }
  // Hidden iframe paint loops can pause. Use its last reported displayed page;
  // identity is checked again after projection without requiring a hidden repaint.
  export function sourceDocument() {
    if (
      !loaded ||
      !painted ||
      !state ||
      !documentBytes ||
      state.token !== token ||
      !state.page
    )
      throw new Error(
        "Wait for the PDF to finish opening before using it as a source.",
      );
    if (state.modified || dirty || pending || saving)
      throw new Error(
        "This PDF has edits. Save a copy and open that copy before using it as a source.",
      );
    return {
      file,
      path: documentBytes.path,
      sha256: documentBytes.sha256,
      page: state.page,
      pages: state.pages,
    };
  }
  async function useSource(practice: boolean) {
    if (!usable || !documentBytes) return;
    acting = true;
    error = "";
    try {
      const before = await inspect();
      if (before.token !== token || before.modified || !before.page)
        throw new Error(
          "Open the unchanged course document to use its study actions.",
        );
      const result = await workspaceRequest<MaterialPage>("materials.read", {
        course_id: courseId,
        path: file,
        page: before.page,
        view: "image",
      });
      const after = await inspect();
      if (
        after.token !== token ||
        after.modified ||
        after.page !== before.page ||
        result.source.sha256 !== documentBytes.sha256 ||
        result.source.path !== documentBytes.path ||
        result.source.page !== before.page
      )
        throw new Error(
          "The page or source changed. Choose the page again before using its study actions.",
        );
      onSource(result.source);
      if (practice) onPractice(result.source);
      else
        onDiscuss(
          `Explain this project resource. Project ${courseId}; retained source ${result.source.artifact_id}, original ${result.source.path}, page ${result.source.page}. Read the relevant exact source through university materials.snapshot before explaining.`,
          result.source,
        );
    } catch (caught) {
      error = String(caught);
    } finally {
      acting = false;
    }
  }
  onMount(() => {
    try {
      if (!localStorage.getItem("pdfcraft"))
        localStorage.setItem(
          "pdfcraft",
          JSON.stringify({
            theme: "Light",
            default_mode: "read",
            default_zoom: "fit-width",
            default_layout: "continuous",
            comments_panel_closed: true,
            right_panel_closed: true,
            language: "en",
          }),
        );
    } catch {
      /* Reading still works when preference storage is unavailable. */
    }
    const unregister = registerReader(token, {
      check: confirmDiscard,
      dirty: () => !!(dirty || pending || saving),
    });
    const observer = new MutationObserver(theme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-palette"],
    });
    const scheme = matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", theme);
    startTimer = setTimeout(() => {
      error =
        "The reader is taking too long to open. You can use the simple reader.";
    }, 45000);
    void readCoursePdf(courseId, file, () => !disposed)
      .then((value) => {
        documentBytes = value;
        send();
      })
      .catch((caught) => {
        if (!disposed) {
          error = String(caught);
          clearTimeout(startTimer);
        }
      });
    resize();
    const layout = new ResizeObserver(resize);
    layout.observe(container.parentElement!);
    return () => {
      disposed = true;
      cancelAnimationFrame(resizeFrame);
      layout.disconnect();
      detachFrame?.();
      clearTimeout(startTimer);
      unregister();
      observer.disconnect();
      scheme.removeEventListener("change", theme);
      if (inspecting) {
        clearTimeout(inspecting.timer);
        inspecting.reject(new Error("Reader closed."));
      }
    };
  });
</script>

<svelte:window
  on:message={message}
  on:resize={resize}
  on:beforeunload={beforeUnload}
/>
<div class="craft-material">
  <div
    class="study-actions document-toolbar"
    aria-label="Actions for the displayed PDF"
  >
    <div class="document-tools" role="group" aria-label="PDF tools">
      <button
        class="tool"
        title="Save a PDF copy"
        aria-label="Save a PDF copy"
        disabled={!loaded || saving}
        on:click={() => (pending ? saveCopy() : command("file.save"))}
        ><Icon name="save" /></button
      >
      <button
        class="tool"
        title="Undo"
        aria-label="Undo PDF edit"
        disabled={!loaded || saving}
        on:click={() => command("edit.undo")}><Icon name="undo" /></button
      >
      <button
        class="tool"
        title="Redo"
        aria-label="Redo PDF edit"
        disabled={!loaded || saving}
        on:click={() => command("edit.redo")}><Icon name="redo" /></button
      >
      <button
        class="tool"
        title="Find in document"
        aria-label="Find in document"
        disabled={!loaded}
        on:click={() => command("edit.find")}><Icon name="search" /></button
      >
    </div>
    <span class="page-label" aria-live="polite"
      >{bound
        ? `Page ${state?.page} of ${state?.pages}`
        : loaded
          ? "Document reader"
          : "Opening PDF…"}</span
    >
    {#if saving || pending}<span class="save-status" role="status"
        >{saving ? "Saving copy…" : "Unsaved copy"}</span
      >{/if}
    <button
      class="quiet"
      title="Open the original PDF with the simple image/text reader"
      on:click={onFallback}>Simple reader</button
    >
    <div class="actions">
      <button
        class="explain"
        aria-label="Explain this page"
        disabled={!usable}
        on:click={() => useSource(false)}>Explain</button
      >
      <button
        class="practice"
        aria-label="Practise this page"
        disabled={!usable}
        on:click={() => useSource(true)}>Practise</button
      >
    </div>
  </div>
  {#if state && !bound && !sourceNoticeDismissed}
    <ReaderNotice
      label="Dismiss source message"
      onDismiss={() => (sourceNoticeDismissed = true)}
    >
      {state.token === token
        ? "Edited copy. Save it in the project folder and reopen it to use Explain or Practise."
        : "Choose a course file to use Explain or Practise."}
    </ReaderNotice>
  {/if}
  {#if error}<ReaderNotice
      error
      label="Dismiss reader error"
      onDismiss={() => (error = "")}>{error}</ReaderNotice
    >{/if}
  {#if notice}<ReaderNotice
      label="Dismiss save message"
      onDismiss={() => (notice = "")}>{notice}</ReaderNotice
    >{/if}
  <div class="reader" bind:this={container} style:height={`${height}px`}>
    <iframe
      on:load={frameLoaded}
      bind:this={frame}
      src="/private/pdfcraft/index.html?host=parent&appearance=kejtu&author=Kejtu"
      title={`PDF reader: ${file.split("/").pop()}`}
      sandbox="allow-scripts allow-same-origin allow-downloads allow-modals allow-popups"
    ></iframe>
    {#if !painted}<div class="opening" role="status">
        {error ? "PDF unavailable" : "Opening document…"}
      </div>{/if}
  </div>
</div>

<style>
  .document-tools,
  .actions {
    display: flex;
    align-items: center;
    gap: 2px;
    flex: none;
  }
  .actions {
    margin-left: auto;
    gap: 8px;
  }
  .page-label,
  .save-status {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
    margin-left: 8px;
  }
  .study-actions .quiet {
    color: var(--ke-text-tertiary);
  }
  .study-actions .explain {
    background: color-mix(in srgb, var(--ke-marker-blue) 10%, transparent);
  }
  .study-actions .practice {
    background: color-mix(in srgb, var(--ke-marker-sage) 12%, transparent);
  }
  .opening {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: var(--ke-surface);
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }
  .reader {
    position: relative;
    min-width: 0;
    overflow: hidden;
  }
  iframe {
    width: 100%;
    height: 100%;
    border: 0;
    display: block;
  }
</style>
