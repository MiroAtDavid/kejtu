<script lang="ts">
  import "./document-chrome.css";
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { identity } from "$lib/api/client";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import ReaderNotice from "./ReaderNotice.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { readCourseDocument, MAX_PDF_BYTES } from "./craft-document";
  import { registerReader, refreshReaderDrafts } from "./reader-drafts";
  export let courseId: string;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => null;
  export let file = "";
  export let active = true;
  export let onFocus: () => void = () => {};
  export let onChange: (dirty: boolean) => void = () => {};
  export let onSaved: (path: string, format: string) => void = () => {};
  type ExportFormat = "docx" | "pdf" | "md";
  const formats = [
    { value: "docx", label: "DOCX", detail: "Editable Word document" },
    {
      value: "pdf",
      label: "PDF",
      detail: "Page layout for reading or printing",
    },
    {
      value: "md",
      label: "Markdown",
      detail: "Text and structure; simpler formatting",
    },
  ];
  const token = identity();
  let format: ExportFormat = "docx";
  $: formatName = formats.find((option) => option.value === format)!.label;
  function exportCopy() {
    if (!opened || saving) return;
    if (pending) {
      void save();
      return;
    }
    post({ type: "wordcraft:export", format });
  }
  let frame: HTMLIFrameElement;
  let container: HTMLDivElement;
  let ready = false,
    sent = false,
    opened = false,
    disposed = false;
  let dirty = false,
    saving = false,
    height = 600;
  let error = "",
    notice = "";
  let input: ArrayBuffer | null | undefined = file ? undefined : null;
  let pending: {
    id: number;
    name: string;
    format: ExportFormat;
    bytes: ArrayBuffer;
  } | null = null;
  let timer: ReturnType<typeof setTimeout>;
  let paint = 0;
  let inspect: {
    id: string;
    resolve: (dirty: boolean) => void;
    reject: () => void;
    timer: ReturnType<typeof setTimeout>;
  } | null = null;
  $: {
    dirty;
    pending;
    saving;
    refreshReaderDrafts();
    onChange(!!(dirty || pending || saving));
  }
  $: if (active) resize();
  function resize() {
    cancelAnimationFrame(paint);
    paint = requestAnimationFrame(() => {
      if (!disposed && active && container?.getClientRects().length)
        height = Math.max(
          260,
          innerHeight - container.getBoundingClientRect().top - 12,
        );
    });
  }
  function post(data: object, transfer: Transferable[] = []) {
    frame?.contentWindow?.postMessage(data, location.origin, transfer);
  }
  function theme() {
    const root = document.documentElement;
    const chosen = root.dataset.theme;
    const style = getComputedStyle(root);
    // Send the resolved shared tokens: the canvas editor cannot inherit CSS.
    const colors = Object.fromEntries(
      [
        "surface",
        "surface-raised",
        "surface-inset",
        "border",
        "border-strong",
        "text-primary",
        "text-secondary",
        "text-tertiary",
        "accent",
        "on-accent",
        "accent-soft",
        "marker-blue",
      ].map((name) => [name, style.getPropertyValue(`--ke-${name}`).trim()]),
    );
    post({
      type: "wordcraft:theme",
      colors,
      theme:
        chosen === "dark" ||
        (!chosen && matchMedia("(prefers-color-scheme: dark)").matches)
          ? "dark"
          : "light",
    });
  }
  function send() {
    if (!ready || sent || input === undefined) return;
    const bytes = input;
    post(
      {
        type: "wordcraft:open",
        token,
        name: file.split("/").pop() || "Untitled.docx",
        bytes,
      },
      bytes ? [bytes] : [],
    );
    sent = true;
    input = undefined;
    theme();
  }
  function command(command: string) {
    if (opened && !saving && !pending)
      post({ type: "wordcraft:command", command });
  }
  export async function confirmDiscard(): Promise<boolean> {
    if (saving) {
      notice = "Finish saving this document first.";
      return false;
    }
    if (opened) {
      if (inspect) return false;
      try {
        dirty = await new Promise<boolean>((resolve, reject) => {
          const id = identity();
          inspect = {
            id,
            resolve,
            reject: () => reject(new Error("Editor unavailable")),
            timer: setTimeout(() => {
              const waiting = inspect;
              inspect = null;
              waiting?.reject();
            }, 3000),
          };
          post({ type: "wordcraft:inspect", request: id });
        });
      } catch {
        return window.confirm(
          "The document editor is not responding. Closing it may discard unsaved edits. Continue?",
        );
      }
    }
    return (
      !(dirty || pending) ||
      window.confirm(
        "This DOCX document has unsaved edits. Discard them and close it?",
      )
    );
  }
  function unload(event: BeforeUnloadEvent) {
    if (dirty || pending || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  }
  async function save() {
    if (!pending || saving) return;
    const copy = pending;
    saving = true;
    error = "";
    notice = "";
    try {
      const initial = await chooseSaveFolder();
      if (initial === undefined || disposed) return;
      const bytes = new Uint8Array(copy.bytes);
      let binary = "";
      for (let i = 0; i < bytes.length; i += 32768)
        binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      const path = await invoke<string | null>(
        copy.format === "pdf"
          ? "save_pdf_copy"
          : copy.format === "md"
            ? "save_markdown_copy"
            : "save_docx_copy",
        {
          name: copy.name,
          data: btoa(binary),
          initial,
        },
      );
      if (path) {
        // Only the editor can clear dirty: it compares the saved revision with today's edits.
        post({ type: "wordcraft:saved", id: copy.id });
        if (pending === copy) pending = null;
        notice = `${copy.format === "md" ? "Markdown" : copy.format.toUpperCase()} copy saved`;
        onSaved(path, copy.format);
      } else notice = "Save cancelled; your copy is still here.";
    } catch (e) {
      error = String(e);
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
    const data = event.data;
    if (data.type === "wordcraft:ready") {
      if (data.version !== "0.6.1" || data.bridgeVersion !== 2) {
        error = "This editor bundle is incompatible.";
        return;
      }
      ready = true;
      send();
    } else if (
      data.type === "wordcraft:state" &&
      data.token === token &&
      typeof data.dirty === "boolean" &&
      Number.isSafeInteger(data.revision)
    ) {
      opened = true;
      dirty = data.dirty;
      clearTimeout(timer);
      if (inspect?.id === data.request) {
        const waiting = inspect;
        inspect = null;
        clearTimeout(waiting.timer);
        waiting.resolve(dirty);
      }
    } else if (data.type === "wordcraft:save") {
      if (
        !opened ||
        pending ||
        saving ||
        !Number.isSafeInteger(data.id) ||
        data.id < 1 ||
        !(data.bytes instanceof ArrayBuffer) ||
        data.bytes.byteLength > MAX_PDF_BYTES ||
        data.token !== token ||
        !["docx", "pdf", "md"].includes(data.format) ||
        (data.format === "docx" &&
          (data.bytes.byteLength < 4 ||
            new Uint8Array(data.bytes)[0] !== 80 ||
            new Uint8Array(data.bytes)[1] !== 75)) ||
        (data.format === "pdf" &&
          !new TextDecoder()
            .decode(new Uint8Array(data.bytes).subarray(0, 5))
            .startsWith("%PDF-"))
      ) {
        error = "The editor returned an unsupported document copy.";
        return;
      }
      pending = {
        id: data.id,
        format: data.format,
        name: typeof data.name === "string" ? data.name : "Untitled.docx",
        bytes: data.bytes,
      };
      format = data.format;
      void save();
    } else if (data.type === "wordcraft:failed") {
      error =
        typeof data.error === "string"
          ? data.error
          : "The editor could not open this document.";
      clearTimeout(timer);
    }
  }
  let detach: (() => void) | undefined;
  function frameLoaded() {
    detach?.();
    const doc = frame?.contentDocument;
    if (!doc) return;
    const interact = () => {
      if (active) onFocus();
      window.dispatchEvent(new Event("kejtu-reader-interaction"));
    };
    const events = ["keydown", "pointerdown", "wheel", "touchstart"];
    events.forEach((e) =>
      doc.addEventListener(e, interact, { capture: true, passive: true }),
    );
    detach = () =>
      events.forEach((e) => doc.removeEventListener(e, interact, true));
  }
  onMount(() => {
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
    timer = setTimeout(
      () =>
        (error =
          "The document editor is taking too long to open. Your other tab is still available."),
      60000,
    );
    if (file)
      void readCourseDocument(courseId, file, () => !disposed)
        .then((value) => {
          input = value.bytes;
          send();
        })
        .catch((e) => {
          if (!disposed) {
            error = String(e);
            clearTimeout(timer);
          }
        });
    const layout = new ResizeObserver(resize);
    layout.observe(container.parentElement!);
    resize();
    return () => {
      disposed = true;
      clearTimeout(timer);
      cancelAnimationFrame(paint);
      unregister();
      observer.disconnect();
      scheme.removeEventListener("change", theme);
      layout.disconnect();
      detach?.();
      if (inspect) {
        clearTimeout(inspect.timer);
        inspect.reject();
      }
    };
  });
</script>

<svelte:window
  on:message={message}
  on:resize={resize}
  on:beforeunload={unload}
/>
<div class="editor">
  <div class="document-toolbar" aria-label="DOCX document controls">
    <button
      class="tool"
      title={`Save a ${formatName} copy`}
      aria-label={`Save a ${formatName} copy`}
      disabled={!opened || saving}
      on:click={exportCopy}><Icon name="save" /></button
    >
    <button
      class="tool"
      title="Undo"
      aria-label="Undo document edit"
      disabled={!opened || saving || !!pending}
      on:click={() => command("edit.undo")}><Icon name="undo" /></button
    >
    <button
      class="tool"
      title="Redo"
      aria-label="Redo document edit"
      disabled={!opened || saving || !!pending}
      on:click={() => command("edit.redo")}><Icon name="redo" /></button
    >
    <div class="export-format">
      <ChoicePicker
        id={`export-${token}`}
        label="Save format"
        value={format}
        options={formats}
        compact
        disabled={!opened || saving || !!pending}
        onChange={(value) => (format = value as ExportFormat)}
      />
    </div>
    <span class="status" role="status"
      >{saving
        ? "Saving copy…"
        : notice ||
          (pending
            ? "Copy waiting to be saved"
            : dirty
              ? "Unsaved changes"
              : "DOCX document")}</span
    >
    {#if notice}<button
        class="tool"
        title="Dismiss save message"
        aria-label="Dismiss save message"
        on:click={() => (notice = "")}><Icon name="close" size={13} /></button
      >{/if}
    {#if pending && !saving}<button class="quiet" on:click={save}
        >Save copy…</button
      >{/if}
    <button
      class="quiet"
      disabled={!opened}
      on:click={() => command("ui.collapseRibbon")}>Formatting</button
    >
  </div>
  {#if error}<ReaderNotice
      error
      label="Dismiss editor error"
      onDismiss={() => (error = "")}>{error}</ReaderNotice
    >{/if}
  <div class="canvas" bind:this={container} style:height={`${height}px`}>
    <iframe
      bind:this={frame}
      on:load={frameLoaded}
      title={`DOCX editor: ${file.split("/").pop() || "Untitled.docx"}`}
      src="/private/wordcraft/index.html?host=parent&webgl"
      sandbox="allow-scripts allow-same-origin allow-downloads allow-modals allow-popups"
    ></iframe>
    {#if !opened}<div class="opening" role="status">
        {error ? "Document unavailable" : "Opening document…"}
      </div>{/if}
  </div>
</div>

<style>
  .editor {
    min-width: 0;
  }
  .tool {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
  }
  .status {
    font-size: 11px;
    color: var(--ke-text-tertiary);
    margin-left: 8px;
  }
  .quiet {
    font-size: 11px;
    margin-left: auto;
  }
  .canvas {
    position: relative;
    overflow: hidden;
  }
  iframe {
    width: 100%;
    height: 100%;
    border: 0;
    display: block;
  }
  .opening {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: var(--ke-surface);
    color: var(--ke-text-tertiary);
    font-size: 12px;
  }
  .export-format {
    width: 128px;
  }
</style>
