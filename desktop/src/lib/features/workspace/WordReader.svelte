<script lang="ts">
  import { onDestroy } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { readCourseDocument } from "./document-bytes";
  import { documentViewportHeight } from "./document-dock";
  import "./document-chrome.css";

  export let courseId: string;
  export let file: string;
  export let active = true;
  export let busy = false;
  export let onFocus: () => void = () => {};
  export let onDiscuss: (text: string) => void = () => {};

  const framePage = `<!doctype html><html><head><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
    <style id="format"></style><style>html{background:transparent;color:#202020;font:16px serif}
    body{margin:0}section.docx{background:white;max-width:52rem;box-sizing:border-box;
    margin:0 auto 8px;padding:clamp(16px,5vw,48px) !important;overflow-wrap:anywhere}
    img{max-width:100%;object-fit:contain}</style></head><body><main></main></body></html>`;
  let frame: HTMLIFrameElement;
  let previewDocument: Document | null = null;
  let loading = true,
    error = "",
    path = "",
    pointerSelection = "";
  let revision = 0,
    generation = 0,
    height = 480;
  let observer: ResizeObserver | undefined;
  function measure() {
    if (active && frame?.getClientRects().length)
      height = documentViewportHeight(frame);
  }
  function selection() {
    return previewDocument?.getSelection()?.toString().trim() ?? "";
  }
  function focusDocument() {
    onFocus();
  }
  function loaded() {
    const doc = frame.contentDocument;
    if (!doc?.querySelector("main") || previewDocument === doc) return;
    previewDocument = doc;
    doc.addEventListener("pointerdown", focusDocument);
    doc.addEventListener("focusin", focusDocument);
    observer?.disconnect();
    if (typeof ResizeObserver !== "undefined" && frame.parentElement) {
      observer = new ResizeObserver(measure);
      observer.observe(frame.parentElement);
    }
    measure();
  }
  $: if (active && frame) measure();
  $: if (previewDocument) void open(courseId, file, previewDocument, revision);
  async function open(
    course: string,
    name: string,
    doc: Document,
    _revision: number,
  ) {
    const turn = ++generation;
    const current = () => generation === turn;
    loading = true;
    error = "";
    pointerSelection = "";
    path = "";
    const main = doc.querySelector("main")!;
    main.replaceChildren();
    try {
      const input = await readCourseDocument(course, name, current);
      const { wordPreview } = await import("./word-preview");
      if (!current()) return;
      const result = await wordPreview(input.bytes).catch(() => {
        throw new Error(
          "This Word document could not be displayed. Check that it opens as a DOCX file.",
        );
      });
      if (!current()) return;
      doc.querySelector("#format")!.textContent = result.styles;
      main.replaceChildren(result.body);
      path = input.path;
    } catch (caught) {
      if (current())
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (current()) loading = false;
    }
  }
  function discuss() {
    if (!path || busy) return;
    // WebKit allows the host to read a script-free frame's selection, but
    // does not dispatch its selectionchange listener in that frame.
    const selected = selection() || pointerSelection;
    pointerSelection = "";
    onDiscuss(
      selected
        ? `In the Word document ${path}, I selected this passage:\n\n${selected}`
        : `About the Word document ${path}: `,
    );
  }
  onDestroy(() => {
    generation++;
    observer?.disconnect();
    previewDocument?.removeEventListener("pointerdown", focusDocument);
    previewDocument?.removeEventListener("focusin", focusDocument);
  });
</script>

<svelte:window on:resize={measure} />
<section class="word-reader" aria-label="Word document">
  <div class="document-toolbar">
    <button
      class="icon"
      type="button"
      title="Reload document"
      aria-label="Reload document"
      disabled={loading}
      on:click={() => revision++}><Icon name="refresh" size={16} /></button
    >
    <button
      type="button"
      disabled={busy || !path}
      on:mousedown|preventDefault={() => (pointerSelection = selection())}
      on:click={discuss}
    >
      Discuss in chat
    </button>
  </div>
  {#if loading}<p role="status">Opening Word document…</p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  <iframe
    bind:this={frame}
    title={`Word preview: ${file}`}
    sandbox="allow-same-origin"
    srcdoc={framePage}
    on:focus={onFocus}
    on:load={loaded}
    style:height={`${height}px`}
  ></iframe>
</section>

<style>
  .word-reader {
    min-width: 0;
  }
  iframe {
    display: block;
    width: 100%;
    border: 0;
  }
  p {
    font: 13px var(--ke-font-ui);
    color: var(--ke-text-secondary);
  }
</style>
