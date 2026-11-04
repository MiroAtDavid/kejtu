<script lang="ts">
  import { onMount, tick, getContext } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { identity } from "$lib/api/client";
  import Icon from "$lib/design/Icon.svelte";
  import DocumentPreview from "./DocumentPreview.svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import DocumentPopover from "./DocumentPopover.svelte";
  import DocumentActivity from "./DocumentActivity.svelte";
  import { workProgress } from "./document-work-progress";
  import { phaseLabel, type Stream } from "$lib/features/chat/stream";
  import "./document-chrome.css";
  import { documentScrollbars } from "./document-scrollbars";
  import type { DocumentSource } from "./document-source";
  export let sourceName = "";
  export let prepareSource:
    ((signal: AbortSignal) => Promise<DocumentSource>) | undefined = undefined;
  let usePdf = true;
  let preparingSource = false;
  import ReaderNotice from "./ReaderNotice.svelte";
  import { readCourseDocument } from "./craft-document";
  import { registerReader, refreshReaderDrafts } from "./reader-drafts";
  import {
    mountMarkdown,
    markdownBytes,
    type MarkdownView,
  } from "./markdown-editor";
  import { readable } from "svelte/store";
  import {
    DOCUMENT_ASSIST,
    workRequest,
    proposalText,
    editPrompt,
    readDocumentReply,
    type DocumentReply,
    type DocumentReference,
    type DocumentAssistant,
    type EditTarget,
  } from "./document-assist";
  const assistant = getContext<DocumentAssistant | undefined>(DOCUMENT_ASSIST);
  import { belongsToDocument, outcomeLabel } from "./document-conversation";
  import RichText from "$lib/features/chat/RichText.svelte";
  const history =
    assistant?.history ??
    readable({
      conversationId: "",
      entries: [],
      canEarlier: false,
      loading: false,
    });
  const sessionId = identity();
  $: documentReference = {
    session_id: sessionId,
    course_id: courseId,
    file: discussionFile,
    name,
  };
  $: documentExchanges = $history.entries.filter((entry) =>
    belongsToDocument(entry, documentReference),
  );
  const available = assistant?.available ?? readable(false);
  const pending = assistant?.pending ?? readable(false);
  const progress = assistant?.progress ?? readable<Stream | null>(null);
  let liveRequestId = "",
    liveTargetId = -1,
    liveWorkId = -1;
  let liveDocument: string | null = null,
    liveAnswer = "";
  let progressTimer: ReturnType<typeof setTimeout> | undefined;
  let queuedProgress: Stream | null = null;
  $: queueProgress(asking ? $progress : null, liveRequestId);
  $: visiblePreview = liveDocument ?? preview;
  function clearPreview() {
    view?.setWorkPreview(liveWorkId, null);
    liveDocument = null;
  }
  function queueProgress(value: Stream | null, requestId: string) {
    queuedProgress = value;
    if (
      !asking ||
      stopped ||
      !requestId ||
      (value !== null &&
        (value.mode !== "live" ||
          !["running", "completed"].includes(value.status)))
    ) {
      clearTimeout(progressTimer);
      progressTimer = undefined;
      if (liveDocument !== null) clearPreview();
      liveAnswer = "";
      return;
    }
    // Saved-body verification can outlive the stream. Retain the last preview
    // until ask() validates the final answer or discards it on failure.
    if (value === null) return;
    // Throttle rather than debounce: continuous chunks must keep painting.
    // Presentation never applies an edit or changes dirty state.
    if (progressTimer !== undefined) return;
    progressTimer = setTimeout(() => {
      progressTimer = undefined;
      if (
        !asking ||
        stopped ||
        disposed ||
        requestId !== liveRequestId ||
        !queuedProgress
      )
        return;
      const partial = workProgress(queuedProgress.content, requestId);
      if (partial?.action === "edit") {
        liveAnswer = "";
        if (view && liveDocument === null) {
          try {
            const range = view.workRange(liveWorkId, partial.edits);
            target = view.markTarget(range);
            liveTargetId = target.id;
          } catch {
            liveTargetId = -1;
          }
        }
        liveDocument = view?.setWorkPreview(liveWorkId, partial.edits) ?? null;
      } else {
        if (liveDocument !== null) clearPreview();
        liveAnswer = partial?.text ?? "";
      }
    }, 80);
  }
  function activityPosition() {
    if (!target?.valid || target.id !== liveTargetId) return null;
    if (mode !== "preview") return view?.targetRect?.() ?? null;
    const blocks = Array.from(
      surface.querySelectorAll<HTMLElement>(".preview [data-document-from]"),
    );
    const block =
      blocks.find(
        (b) =>
          target!.from >= Number(b.dataset.documentFrom) &&
          target!.from < Number(b.dataset.documentTo),
      ) ??
      (target.from === text.replace(/\r\n?/g, "\n").length
        ? blocks.at(-1)
        : null);
    if (!block)
      return (
        surface.querySelector(".document-page")?.getBoundingClientRect() ?? null
      );
    const rect = block.getBoundingClientRect();
    return {
      left: rect.left,
      top:
        target.from === Number(block.dataset.documentTo)
          ? rect.bottom
          : rect.top,
      height: rect.height,
    };
  }
  let instruction = "",
    asking = false,
    stopped = false,
    assistError = "",
    result = "",
    answer = "",
    expanded = false;
  let target: EditTarget | null = null;
  let controller: AbortController | null = null;
  let proposal: DocumentReply | null = null;
  let appliedState: { requestId: string; text: string } | null = null;
  let panelOpen = false;
  let panelX = 200,
    panelY = 200;
  let zoom = 100;
  let focusHint = "";
  let menu: { x: number; y: number; label: string } | null = null;
  let menuElement: HTMLDivElement;
  function closePanel() {
    panelOpen = false;
    if (mode !== "preview") view?.focus();
    else surface?.querySelector<HTMLElement>(".preview")?.focus();
  }
  async function openPanel(x?: number, y?: number) {
    if (!view || !ready) return;
    // Keyboard selections have no mouseup event. Capture their optional hint
    // before the panel takes focus; failed source mapping never gates editing.
    if (
      !asking &&
      !panelOpen &&
      mode !== "preview" &&
      mode !== "diff" &&
      visualHint()
    )
      rememberHint();
    const bounds = surface.getBoundingClientRect();
    panelX = x ?? Math.max(bounds.left + 12, bounds.right - 580);
    panelY = y ?? bounds.top + 64;
    menu = null;
    panelOpen = true;
  }
  function dismissMenu(e: PointerEvent) {
    if (menu && !menuElement?.contains(e.target as Node)) menu = null;
  }
  function visualHint() {
    const selected = window.getSelection();
    if (!selected?.rangeCount) return "";
    const range = selected.getRangeAt(0);
    if (!surface.contains(range.commonAncestorContainer)) return "";
    if (!selected.isCollapsed) return selected.toString();
    const node =
      range.startContainer.nodeType === 1
        ? (range.startContainer as Element)
        : range.startContainer.parentElement;
    return node?.closest("p,li,pre,td,th,h1,h2,h3,h4,h5,h6")?.textContent ?? "";
  }
  function rememberHint() {
    focusHint = visualHint();
    try {
      target = view?.markTarget() ?? null;
    } catch {
      view?.clearTarget();
      target = null;
    }
    assistError = "";
  }
  async function chooseTarget(e: MouseEvent, origin: "source" | "preview") {
    if (!assistant || !view || !ready || asking) return;
    const context = e.type === "contextmenu";
    // Linux browsers may dispatch contextmenu before the right-button mouseup.
    if (!context && e.button !== 0) return;
    if (
      origin === "source" &&
      mode === "edit" &&
      e.type !== "contextmenu" &&
      window.getSelection()?.isCollapsed
    ) {
      menu = null;
      if (!panelOpen && !instruction.trim() && !proposal) {
        view.clearTarget();
        focusHint = "";
      }
      return;
    }
    try {
      let range: { from: number; to: number };
      let label = "";
      if (origin === "source") {
        range = view.selection();
        if (range.from === range.to) {
          if (!context) {
            menu = null;
            if (!panelOpen && !instruction.trim() && !proposal) {
              view.clearTarget();
            }
            return;
          }
          const pos = view.positionAt(e.clientX, e.clientY);
          if (pos !== null) range = { from: pos, to: pos };
        }
      } else {
        // Never edit against the delayed preview of an earlier document version.
        if (preview !== text) return;
        const selection = window.getSelection();
        const selected =
          selection && !selection.isCollapsed && selection.rangeCount
            ? selection.getRangeAt(0)
            : null;
        if (!context && !selected) {
          menu = null;
          return;
        }
        const block = (e.target as HTMLElement).closest<HTMLElement>(
          "[data-document-from]",
        );
        if (!block) return;
        let blocks = [block];
        if (
          selected &&
          block.closest(".preview")?.contains(selected.commonAncestorContainer)
        ) {
          blocks = Array.from(
            block
              .closest(".preview")!
              .querySelectorAll<HTMLElement>("[data-document-from]"),
          ).filter((el) => selected.intersectsNode(el));
        }
        const from = Number(blocks[0]?.dataset.documentFrom);
        const to = Number(blocks.at(-1)?.dataset.documentTo);
        if (
          !Number.isInteger(from) ||
          !Number.isInteger(to) ||
          from < 0 ||
          to < from
        )
          return;
        range = { from, to };
        label =
          blocks.length > 1
            ? "Selected blocks"
            : (block.dataset.documentLabel ?? "block");
        label = label[0].toUpperCase() + label.slice(1);
      }
      e.preventDefault();
      focusHint = "";
      target = view.markTarget(range);
      menu = {
        x: e.clientX || surface.getBoundingClientRect().left + 60,
        y: e.clientY + 10 || surface.getBoundingClientRect().top + 50,
        label,
      };
      await tick();
      if (menu && menuElement)
        menu = {
          ...menu,
          x: Math.max(
            8,
            Math.min(menu.x, innerWidth - menuElement.offsetWidth - 8),
          ),
          y: Math.max(
            8,
            Math.min(menu.y, innerHeight - menuElement.offsetHeight - 8),
          ),
        };
    } catch {
      // Exact rich/source offsets are optional. Keep the visible passage as a
      // hint and let the model locate it in the full draft.
      focusHint = visualHint();
      view.clearTarget();
      target = null;
      assistError = "";
      menu = null;
      if (context) {
        e.preventDefault();
        void openPanel(e.clientX, e.clientY + 20);
      }
    }
  }

  function insertAfter() {
    if (!target || !view || !menu) return;
    target = view.markTarget({ from: target.to, to: target.to });
    void openPanel(menu.x, menu.y + 30);
  }
  function wholeDocument() {
    if (!view || asking) return;
    view.clearTarget();
    target = null;
    focusHint = "";
  }

  function documentKeys(e: KeyboardEvent) {
    if (!active || !focused) return;
    if (e.key === "Escape") menu = null;
    if (
      (e.ctrlKey || e.metaKey) &&
      e.shiftKey &&
      e.key.toLowerCase() === "k" &&
      assistant
    ) {
      e.preventDefault();
      e.stopPropagation();
      if (!asking) rememberHint();
      void openPanel();
    }
  }
  let exchanges: { instruction: string; reply: string }[] = [];
  let discussionRequested = false;
  export async function openDiscussion(reference?: DocumentReference) {
    if (reference) historySession = reference.session_id;
    expanded = true;
    // Journal links can mount a document before its permission-checked read and
    // editor load finish. Keep the request until the editor can display it.
    discussionRequested = true;
    if (ready) {
      await openPanel();
      discussionRequested = false;
    }
  }
  let historySession = "";
  let lastConversation = $history.conversationId;
  $: if ($history.conversationId !== lastConversation) {
    lastConversation = $history.conversationId;
    answer = "";
    result = "";
    exchanges = [];
  }
  $: visibleExchanges = historySession
    ? $history.entries.filter(
        (entry) =>
          entry.request.document?.session_id === historySession ||
          belongsToDocument(entry, documentReference),
      )
    : documentExchanges;
  async function moreHistory(turnId?: string) {
    try {
      if (turnId) await assistant?.more?.(turnId);
      else await assistant?.earlier?.();
    } catch (e) {
      assistError = e instanceof Error ? e.message : String(e);
    }
  }
  onMount(() =>
    assistant?.register?.(documentReference, () => openDiscussion()),
  );
  async function ask() {
    if (asking || !$available || !assistant || !view || !instruction.trim())
      return;
    const currentTarget = view.rearmTarget?.() ?? view.target();
    const snapshot = view.beginWork();
    liveWorkId = snapshot.id;
    const aimed = currentTarget?.valid ? currentTarget : null;
    const submitted = instruction;
    liveTargetId = aimed?.id ?? -1;
    liveRequestId = "";
    const includeSource = usePdf && !!sourceName;
    controller = new AbortController();
    const signal = controller.signal;
    asking = true;
    stopped = false;
    assistError = "";
    result = "";
    answer = "";
    proposal = null;
    try {
      preparingSource = includeSource;
      const source = includeSource ? await prepareSource?.(signal) : undefined;
      if (disposed || signal.aborted) return;
      if (includeSource && !source)
        throw new Error(
          "The PDF source is unavailable. Reopen it or turn off the source for this edit.",
        );
      preparingSource = false;
      const request = workRequest(
        name,
        submitted,
        snapshot.text,
        documentReference,
        aimed,
        focusHint,
        source,
      );
      liveRequestId = request.request_id;
      const response = await assistant.ask(editPrompt(request), signal);
      if (disposed || signal.aborted) return;
      clearTimeout(progressTimer);
      progressTimer = undefined;
      clearPreview();
      let reply: DocumentReply;
      try {
        reply = readDocumentReply(response, request.request_id);
      } catch {
        throw new Error(
          "Kejtu did not return a complete matching response. Nothing changed; the original answer remains in Journal details.",
        );
      }
      answer = reply.explanation;
      if (reply.action === "reply") {
        // Conversation has no replacement field and must never touch the editor,
        // target, dirty state or Undo history (even after concurrent typing).
        assistant.outcome?.(request.request_id, "replied");
        if (instruction === submitted) instruction = "";
      } else {
        const beforeEdit = text;
        let applied = false;
        try {
          if (!("edits" in reply))
            throw new Error(
              "Kejtu returned an older edit format without passage locations. Nothing was replaced; the proposal is kept below.",
            );
          applied = view.applyWork(snapshot.id, reply.edits);
        } catch (e) {
          assistError = e instanceof Error ? e.message : String(e);
        }
        if (!applied) {
          proposal = reply;
          assistant.outcome?.(request.request_id, "conflict");
        } else {
          focusHint = "";
          const changed = text !== beforeEdit;
          result = changed ? "Applied to draft" : "No change";
          appliedState = changed
            ? { requestId: request.request_id, text }
            : null;
          assistant.outcome?.(
            request.request_id,
            changed ? "applied" : "unchanged",
          );
          if (instruction === submitted) instruction = "";
        }
      }
      exchanges = [
        ...exchanges.slice(-9),
        {
          instruction: submitted,
          reply: reply.explanation || result || "Proposal kept for review.",
        },
      ];
    } catch (e) {
      if (!disposed)
        assistError = signal.aborted
          ? "Stopped. Your document is unchanged."
          : e instanceof Error
            ? e.message
            : String(e);
    } finally {
      clearTimeout(progressTimer);
      progressTimer = undefined;
      if (!disposed) {
        clearPreview();
        liveRequestId = "";
        view?.endWork(snapshot.id);
        liveTargetId = -1;
        liveWorkId = -1;
        liveAnswer = "";
        asking = false;
        preparingSource = false;
        controller = null;
        if (signal.aborted)
          assistError = "Stopped. Your document is unchanged.";
        resize();
      }
    }
  }
  function stopEdit() {
    stopped = true;
    clearTimeout(progressTimer);
    progressTimer = undefined;
    clearPreview();
    controller?.abort();
  }
  function instructionKeys(e: KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void ask();
    }
  }
  export let courseId: string;
  export let chooseSaveFolder: () => Promise<
    string | null | undefined
  > = async () => null;
  export let file = "";
  export let initialContent = "";
  export let initialName = "";
  export let courseFolder: string | null = null;
  let discussionFile = file;
  export let active = true;
  export let focused = true;
  export let onReadOriginal: (file: string) => void = () => {};
  export let onChange: (dirty: boolean) => void = () => {};
  export let onSaved: (path: string) => void = () => {};
  let host: HTMLDivElement;
  let richHost: HTMLDivElement;
  let richTools: HTMLDivElement;
  let surface: HTMLDivElement;
  let view: MarkdownView | undefined;
  let text = file ? "" : initialContent,
    saved = "",
    preview = "";
  let ready = false,
    saving = false,
    disposed = false,
    dirty = false;
  let mode: "edit" | "write" | "preview" | "both" | "diff" = "edit";
  let height = 600,
    error = "",
    notice = "";
  let name = file.split("/").pop() || initialName || "Untitled.md";
  let timer: ReturnType<typeof setTimeout>;
  let paint = 0;
  $: dirty = text !== saved;
  $: {
    dirty;
    saving;
    asking;
    instruction;
    proposal;
    refreshReaderDrafts();
    onChange(dirty || saving);
  }
  $: {
    text;
    mode;
    active;
    schedulePreview();
  }
  $: {
    active;
    mode;
    error;
    notice;
    assistError;
    result;
    expanded;
    asking;
    proposal;
    resize();
  }
  function schedulePreview() {
    clearTimeout(timer);
    // Source stays responsive while the separate formatted view catches up.
    if (active && ["preview", "both"].includes(mode))
      timer = setTimeout(() => {
        preview = text;
      }, 180);
  }
  function resize() {
    cancelAnimationFrame(paint);
    if (!active) return;
    paint = requestAnimationFrame(() => {
      if (disposed || !surface?.getClientRects().length) return;
      height = Math.max(
        260,
        innerHeight - surface.getBoundingClientRect().top - 12,
      );
      view?.measure();
    });
  }
  async function changeMode(value: typeof mode) {
    mode = value;
    view?.setMode?.(value);
    preview = text;
    await tick();
    view?.measure();
    if (value === "write" || value === "edit") view?.focus();
  }
  export async function confirmDiscard() {
    if (asking) {
      assistError = "Stop or finish the document edit before closing.";
      return false;
    }
    if (saving) {
      notice = "Finish saving this document first.";
      return false;
    }
    return (
      (!dirty && !instruction.trim() && !proposal) ||
      window.confirm(
        "This document has unsaved edits, instructions or a proposal. Discard them and close it?",
      )
    );
  }
  function unload(event: BeforeUnloadEvent) {
    if (dirty || saving || asking || instruction.trim() || proposal) {
      event.preventDefault();
      event.returnValue = "";
    }
  }
  async function save() {
    if (!ready || saving || asking) return;
    const snapshot = text;
    saving = true;
    error = "";
    notice = "";
    try {
      const initial = await chooseSaveFolder();
      if (initial === undefined || disposed) return;
      const path = await invoke<string | null>("save_markdown_copy", {
        name,
        data: markdownBytes(snapshot),
        initial: initial ?? courseFolder,
      });
      if (disposed) return;
      if (path) {
        // A delayed chooser receipt acknowledges only the bytes it actually saved.
        saved = snapshot;
        name = path.split("/").pop() || name;
        // A saved copy can leave this course. Never reopen the old original under
        // the copy's name or turn an absolute chooser path into an access grant.
        const root = courseFolder?.replace(/\/$/, "");
        discussionFile =
          root && path.startsWith(root + "/")
            ? path.slice(root.length + 1)
            : "";
        onSaved(path);
      } else notice = "Save cancelled. Your Markdown is still here.";
    } catch (e) {
      if (!disposed) error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }
  onMount(() => {
    const unregister = registerReader(identity(), {
      check: confirmDiscard,
      dirty: () =>
        dirty || saving || asking || !!instruction.trim() || !!proposal,
    });
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(surface);
    void (async () => {
      try {
        if (file) {
          const original = await readCourseDocument(
            courseId,
            file,
            () => !disposed,
          );
          if (disposed) return;
          text = new TextDecoder("utf-8", {
            fatal: true,
            ignoreBOM: true,
          }).decode(original.bytes);
        }
        // A supplied starter is an unsaved document, not an acknowledged file.
        saved = file ? text : "";
        preview = text;
        const mounted = await mountMarkdown(
          host,
          text,
          (value) => {
            text = value;
            result = "";
          },
          () => void save(),
          (value) => {
            target = value;
          },
          {
            richParent: richHost,
            toolsParent: richTools,
            onError: (message) => {
              error = message;
            },
            onMode: (value) => {
              void changeMode(value as typeof mode);
            },
          },
        );
        if (disposed) {
          mounted.destroy();
          return;
        }
        view = mounted;
        view.setMode?.(mode);
        ready = true;
        resize();
        // Show a prefilled brief from its beginning; rich-editor focus can scroll
        // to its last inserted paragraph before the reader has seen the context.
        if (active && !file && !initialContent) view.focus();
        if (discussionRequested) await openDiscussion();
      } catch (e) {
        if (!disposed) error = e instanceof Error ? e.message : String(e);
      }
    })();
    return () => {
      disposed = true;
      clearTimeout(progressTimer);
      controller?.abort();
      clearTimeout(timer);
      cancelAnimationFrame(paint);
      observer?.disconnect();
      view?.destroy();
      unregister();
    };
  });
</script>

<svelte:window
  on:resize={resize}
  on:beforeunload={unload}
  on:pointerdown={dismissMenu}
  on:keydown|capture={documentKeys}
/>
<div class="markdown-editor">
  {#if initialContent && !file && !saved}<p class="starter-note">
      Section brief starter · Add your context, develop it with Ask Kejtu, then
      save a Markdown copy.
    </p>{/if}
  <div class="document-toolbar" aria-label="Markdown document controls">
    <button
      class="icon"
      aria-label="Save a Markdown copy"
      title="Save a Markdown copy · Ctrl/⌘ S"
      disabled={!ready || saving || asking}
      on:click={save}><Icon name="save" size={16} /></button
    >
    <button
      class="icon"
      aria-label="Undo Markdown edit"
      title="Undo"
      disabled={!ready || liveDocument !== null}
      on:click={() => view?.undo()}><Icon name="undo" size={16} /></button
    >
    <button
      class="icon"
      aria-label="Redo Markdown edit"
      title="Redo"
      disabled={!ready || liveDocument !== null}
      on:click={() => view?.redo()}><Icon name="redo" size={16} /></button
    >
    <span class="state" role="status"
      >{saving
        ? "Saving…"
        : liveDocument !== null
          ? "Live preview"
          : !ready && !error
            ? "Opening Markdown…"
            : dirty
              ? "Unsaved changes"
              : "Markdown"}</span
    >
    {#if discussionFile}<button
        title="Read the saved original and use Explain or Practise"
        aria-label="Read original Markdown"
        on:click={() => onReadOriginal(discussionFile)}>Read original</button
      >{/if}
  </div>
  {#if error}<ReaderNotice
      error
      label="Dismiss Markdown error"
      onDismiss={() => (error = "")}>{error}</ReaderNotice
    >{/if}
  {#if notice}<ReaderNotice
      label="Dismiss Markdown message"
      onDismiss={() => (notice = "")}>{notice}</ReaderNotice
    >{/if}
  <div
    class="document-desk"
    bind:this={surface}
    style:height={`${height}px`}
    style:--document-zoom={zoom / 100}
    style:--document-height={`${height}px`}
  >
    <div class="mode-rail" role="group" aria-label="Markdown view">
      <button
        aria-label="Write"
        title="Write in the formatted document"
        aria-pressed={mode === "edit"}
        on:click={() => changeMode("edit")}
        ><Icon name="text" size={17} /></button
      >
      <button
        aria-label="Source"
        title="Edit Markdown source"
        aria-pressed={mode === "write"}
        on:click={() => changeMode("write")}
        ><Icon name="code" size={17} /></button
      >
      <button
        aria-label="Preview"
        title="Preview document"
        aria-pressed={mode === "preview"}
        on:click={() => changeMode("preview")}
        ><Icon name="file" size={17} /></button
      >
      <button
        aria-label="Both"
        title="Source and preview side by side"
        aria-pressed={mode === "both"}
        on:click={() => changeMode("both")}
        ><Icon name="sidebar" size={17} /></button
      >
      <button
        aria-label="Compare"
        title="Compare with the opened document"
        aria-pressed={mode === "diff"}
        on:click={() => changeMode("diff")}
        ><Icon name="compare" size={17} /></button
      >
    </div>
    <div class="surface" class:both={mode === "both"} use:documentScrollbars>
      <!-- Selection and context menus enhance the ordinary keyboard editor. -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="rich-writing"
        bind:this={richHost}
        hidden={mode !== "edit" && mode !== "diff"}
        on:mouseup={(e) => chooseTarget(e, "source")}
        on:contextmenu={(e) => chooseTarget(e, "source")}
      ></div>
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="writing"
        bind:this={host}
        hidden={mode !== "write" && mode !== "both"}
        on:mouseup={(e) => chooseTarget(e, "source")}
        on:contextmenu={(e) => chooseTarget(e, "source")}
      ></div>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
      <section
        class="preview"
        aria-label="Markdown preview"
        hidden={mode !== "preview" && mode !== "both"}
        tabindex="0"
        on:mouseup={(e) => chooseTarget(e, "preview")}
        on:contextmenu={(e) => chooseTarget(e, "preview")}
      >
        <div class="document-page">
          {#if active && mode !== "write"}
            {#if visiblePreview.length > 200000}<p class="empty">
                This document is too large for the preview. You can still edit
                and save all of its Markdown.
              </p>
            {:else if visiblePreview.trim()}<DocumentPreview
                source={visiblePreview}
                target={liveDocument === null ? target : null}
              />
            {:else}<p class="empty">
                Your formatted document will appear here.
              </p>{/if}
          {/if}
        </div>
      </section>
      {#if active && asking && !stopped}
        <DocumentActivity
          {surface}
          locate={activityPosition}
          revision={[target, mode, liveDocument, zoom]}
          label={preparingSource
            ? "Kejtu is reading the PDF…"
            : liveDocument !== null
              ? "Kejtu is writing a live preview…"
              : phaseLabel($progress?.phase)}
          onOpen={() => openPanel()}
        />
      {/if}
    </div>
    <div class="document-rail">
      {#if assistant}<button
          class:working={asking}
          aria-label="Ask Kejtu"
          title={asking ? "Kejtu is working…" : "Ask Kejtu · Ctrl/⌘ Shift K"}
          aria-pressed={panelOpen}
          disabled={!ready}
          on:click={() => (panelOpen ? closePanel() : openPanel())}
          ><Icon
            name="comment"
            size={17}
          />{#if asking || instruction.trim() || proposal}<span class="dot"
            ></span>{/if}</button
        >{/if}
      <div class="rich-tools" bind:this={richTools}></div>
      <div class="zoom-controls" role="group" aria-label="Document zoom">
        <button
          class="zoom-value"
          title="Reset zoom"
          aria-label="Reset document zoom"
          on:click={() => (zoom = 100)}>{zoom}%</button
        >
        <button
          aria-label="Zoom in"
          title="Zoom in"
          disabled={zoom >= 150}
          on:click={() => (zoom = Math.min(150, zoom + 10))}
          ><Icon name="zoom-in" size={17} /></button
        >
        <button
          aria-label="Zoom out"
          title="Zoom out"
          disabled={zoom <= 80}
          on:click={() => (zoom = Math.max(80, zoom - 10))}
          ><Icon name="zoom-out" size={17} /></button
        >
      </div>
    </div>
  </div>
  {#if active && menu}
    <div
      class="selection-menu"
      bind:this={menuElement}
      style:left={`${menu.x}px`}
      style:top={`${menu.y}px`}
      role="group"
      aria-label="Document selection actions"
    >
      <button on:click={() => menu && openPanel(menu.x, menu.y + 30)}
        >{menu.label
          ? `Edit ${menu.label.toLowerCase()}…`
          : "Ask Kejtu…"}</button
      >
      {#if target && target.from !== target.to}<button on:click={insertAfter}
          >Insert after…</button
        >{/if}
      <button
        aria-label="Close selection menu"
        title="Close"
        on:click={() => (menu = null)}><Icon name="close" size={12} /></button
      >
    </div>
  {/if}
  {#if assistant && active && panelOpen}
    <DocumentPopover bind:x={panelX} bind:y={panelY} onClose={closePanel}>
      <div class="assistant-bar">
        <div class="assist-context">
          <span
            class="focus-hint"
            title="Optional focus hint. Kejtu can locate passages from your instruction."
            >{target?.valid
              ? target.from === target.to
                ? "Insertion point"
                : "Selected passage"
              : focusHint
                ? "Selected passage"
                : "This document"}</span
          >
          {#if target || focusHint}<button
              class="clear-focus"
              aria-label="Clear document focus"
              title="Use the document without a focus hint"
              disabled={asking}
              on:click={wholeDocument}><Icon name="close" size={12} /></button
            >{/if}
          {#if sourceName}<ChoicePicker
              id={`document-edit-source-${sessionId}`}
              label="Reference source"
              hideLabel
              labelOnly
              value={usePdf ? "pdf" : "none"}
              disabled={asking}
              options={[
                { value: "pdf", label: sourceName },
                { value: "none", label: "No reference source" },
              ]}
              onChange={(value) => (usePdf = value === "pdf")}
            />{/if}
          {#if visibleExchanges.length || exchanges.length || $history.canEarlier}<button
              class="edit-details"
              title="Document conversation"
              aria-label="Document conversation"
              aria-expanded={expanded}
              on:click={() => (expanded = !expanded)}>···</button
            >{/if}
        </div>
        {#if expanded}<div
            class="edit-conversation"
            aria-label="Document editing conversation"
          >
            <div class="history-heading">In this Journal conversation</div>
            {#if $history.canEarlier}<button
                disabled={$history.loading}
                on:click={() => moreHistory()}>Earlier exchanges</button
              >{/if}
            {#if assistant.history}
              {#each visibleExchanges as exchange (exchange.turnId)}
                <div class="document-exchange">
                  <p class="instruction">{exchange.request.instruction}</p>
                  <div class="exchange-reply">
                    <RichText
                      source={exchange.reply?.explanation || exchange.detail}
                      complete
                    />
                  </div>
                  <small>{outcomeLabel(exchange)}</small>
                  {#if !exchange.complete && exchange.status === "completed"}<button
                      on:click={() => moreHistory(exchange.turnId)}
                      >Load answer</button
                    >{/if}
                  {#if exchange.reply?.action === "edit"}<details>
                      <summary>Proposed change</summary>
                      <pre>{proposalText(exchange.reply)}</pre>
                    </details>{/if}
                </div>
              {/each}
              {#if !visibleExchanges.length}<p>
                  No loaded exchanges for this document.
                </p>{/if}
            {:else}
              {#each exchanges as exchange}<p class="instruction">
                  {exchange.instruction}
                </p>
                <p>{exchange.reply}</p>{/each}
            {/if}
          </div>{/if}
        {#if proposal}<details class="proposal" open>
            <summary>Proposed Markdown</summary>
            <pre>{proposalText(proposal)}</pre>
            <p>{proposal.explanation}</p>
            <button
              on:click={() => {
                proposal = null;
                assistError = "";
              }}>Dismiss proposal</button
            >
          </details>{/if}
        {#if assistError}<ReaderNotice
            error
            label="Dismiss editing message"
            onDismiss={() => (assistError = "")}>{assistError}</ReaderNotice
          >{/if}
        {#if (answer || liveAnswer) && !expanded}<div
            class="document-answer"
            role="status"
          >
            <RichText source={answer || liveAnswer} complete={!asking} />
          </div>{/if}
        {#if result}<div class="edit-result" role="status">
            <span>{result}</span>{#if result.startsWith("Applied")}<button
                disabled={!appliedState || text !== appliedState.text}
                title={appliedState && text !== appliedState.text
                  ? "Use the document toolbar to undo later changes first"
                  : "Undo this edit"}
                on:click={() => {
                  if (!appliedState || text !== appliedState.text) return;
                  view?.undo();
                  assistant.outcome?.(appliedState.requestId, "undone");
                  result = "";
                  appliedState = null;
                }}>Undo</button
              >{/if}<button
              aria-label="Dismiss edit result"
              on:click={() => (result = "")}
              ><Icon name="close" size={12} /></button
            >
          </div>{/if}
        <div class="instruction-row">
          <textarea
            aria-label="Ask Kejtu"
            placeholder="Ask a question or describe a change…"
            bind:value={instruction}
            rows="4"
            on:keydown={instructionKeys}
            disabled={!ready}
          ></textarea>
          {#if asking}<button
              class="submit"
              aria-label="Stop document edit"
              disabled={stopped}
              on:click={stopEdit}><Icon name="stop" size={15} /></button
            >
          {:else}<button
              class="submit"
              aria-label="Send document instruction"
              title="Send · Ctrl/⌘ Enter"
              disabled={!ready || !$available || !instruction.trim()}
              on:click={ask}><Icon name="arrow-up" size={15} /></button
            >{/if}
        </div>
        <div class="assist-foot">
          <span
            >{asking
              ? stopped
                ? "Stopping…"
                : preparingSource
                  ? "Checking the PDF source…"
                  : liveDocument !== null
                    ? "Writing a live preview…"
                    : phaseLabel($progress?.phase)
              : ""}</span
          >
          {#if $pending && !asking}<button on:click={() => assistant.resolve()}
              >Check pending request</button
            >{/if}
        </div>
      </div>
    </DocumentPopover>
  {/if}
</div>

<style>
  .starter-note {
    margin: 8px 12px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--ke-text-secondary);
  }
  .document-answer {
    overflow: auto;
    max-height: min(180px, 30%);
    min-height: 24px;
    padding: 4px 0 8px;
    flex-shrink: 1;
    font-size: 13px;
    line-height: 1.55;
  }
  .document-answer :global(p) {
    margin: 0;
  }
  .document-exchange {
    border-bottom: 1px solid var(--ke-border);
    padding: 8px 0;
  }
  .document-exchange small,
  .history-heading {
    color: var(--ke-text-tertiary);
    font-size: 10px;
  }
  .document-exchange pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 200px;
    overflow: auto;
  }
  .exchange-reply :global(p) {
    margin: 6px 0;
  }

  .assistant-bar {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    padding: 0;
    color: var(--ke-text-secondary);
    font-size: 11px;
  }
  .assist-context,
  .instruction-row,
  .assist-foot,
  .edit-result {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .assist-context {
    flex-wrap: wrap;
    justify-content: flex-start;
    flex-wrap: nowrap;
    padding-bottom: 10px;
  }
  .assistant-bar button {
    border: 0;
    background: transparent;
    color: var(--ke-text-secondary);
    padding: 4px 6px;
    border-radius: var(--ke-radius-sm);
    font: inherit;
    cursor: pointer;
  }
  .assistant-bar button:hover {
    background: var(--ke-surface-inset);
  }
  .assistant-bar button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .assistant-bar button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 1px;
  }
  .instruction-row {
    flex: 1;
    min-height: 64px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    padding: 5px 7px;
  }
  .instruction-row:focus-within {
    border-color: var(--ke-border-strong);
    box-shadow: 0 0 0 1px var(--ke-border);
  }
  .instruction-row textarea:focus {
    outline: none;
  }
  .instruction-row textarea {
    flex: 1;
    width: 100%;
    min-width: 0;
    min-height: 48px;
    height: 100%;
    box-sizing: border-box;
    resize: none;
    padding: 4px;
    border: 0;
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    font-size: 12px;
    line-height: 1.5;
  }
  .instruction-row .submit {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    flex: 0 0 28px;
    align-self: flex-end;
    padding: 0;
  }
  .assist-foot {
    justify-content: space-between;
    padding-top: 5px;
    color: var(--ke-text-tertiary);
    font-size: 10px;
  }
  .edit-conversation,
  .proposal {
    max-height: 180px;
    overflow: auto;
    scrollbar-width: thin;
    padding: 5px 0;
    overflow-wrap: anywhere;
  }
  .edit-conversation p {
    margin: 5px 0;
    white-space: pre-wrap;
  }
  .edit-conversation .instruction {
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .proposal pre {
    white-space: pre-wrap;
    user-select: text;
  }
  .edit-result {
    padding-bottom: 5px;
  }
  .markdown-editor {
    min-width: 0;
  }
  .document-desk {
    position: relative;
    container-type: inline-size;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--document-rail-width);
    min-height: 260px;
    background: var(--ke-surface);
  }
  .mode-rail {
    position: absolute;
    z-index: 2;
    left: var(--document-tool-inset);
    top: var(--document-tool-inset);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 6px;
    border: 1px solid var(--ke-border);
    border-radius: 12px;
    background: var(--ke-surface-raised);
    box-shadow: 0 4px 16px rgb(0 0 0 / 0.11);
  }
  :global(.workspace) .mode-rail button,
  :global(.workspace) .document-rail button {
    position: relative;
    display: grid;
    place-items: center;
    flex: none;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 5px;
    background: transparent;
    color: var(--ke-text-secondary);
    cursor: pointer;
  }
  :global(.workspace) .mode-rail button:hover,
  :global(.workspace) .document-rail button:hover {
    background: var(--ke-surface-inset);
  }
  :global(.workspace) .mode-rail button[aria-pressed="true"],
  :global(.workspace) .document-rail button[aria-pressed="true"] {
    background: var(--ke-surface-inset);
    border-color: var(--ke-border-strong);
    color: var(--ke-text-primary);
  }
  :global(.workspace) .mode-rail button:focus-visible,
  :global(.workspace) .document-rail button:focus-visible,
  .selection-menu button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -1px;
  }
  .document-rail {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 6px;
    border-left: 1px solid var(--ke-border);
  }
  :global(.workspace) .document-rail > button {
    width: 32px;
    height: 32px;
  }
  :global(.workspace) .document-rail button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .document-rail .dot {
    position: absolute;
    top: 4px;
    right: 3px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--ke-marker-blue);
  }
  .zoom-controls {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .document-rail .zoom-value {
    font: 10px var(--ke-font-ui);
  }
  .surface {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
    overflow: hidden;
    position: relative;
    z-index: 0;
    isolation: isolate;
  }
  .surface.both {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
  .writing,
  .rich-writing {
    height: 100%;
    min-width: 0;
    overflow: hidden;
  }
  .preview {
    overflow: auto;
    padding: var(--document-page-top) var(--document-page-side);
    min-width: 0;
    font-size: calc(14px * var(--document-zoom));
    line-height: 1.7;
    color: var(--ke-text-primary);
    overflow-wrap: anywhere;
  }
  .preview:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -2px;
  }
  .focus-hint {
    flex: none;
    color: var(--ke-text-secondary);
  }
  .edit-details {
    margin-left: auto;
    flex: none;
  }
  .assist-context :global(.choice-field) {
    margin: 0;
    min-width: 0;
  }
  .assist-context :global(.choice-trigger) {
    font-size: 11px;
    min-height: 28px;
  }
  .rich-tools {
    min-height: 0;
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    width: 44px;
    scrollbar-width: none;
  }
  .document-page {
    box-sizing: border-box;
    width: 100%;
    min-height: 100%;
    margin: 0 auto;
    padding: clamp(24px, 4vw, 56px);
    background: var(--ke-surface-raised);
    border: 1px solid color-mix(in srgb, var(--ke-border) 60%, transparent);
    box-shadow: var(--document-paper-shadow);
  }
  .both .document-page {
    padding: 24px;
  }
  .selection-menu {
    display: flex;
    align-items: center;
    position: fixed;
    z-index: 61;
    max-width: calc(100vw - 16px);
    border: 1px solid var(--ke-border);
    background: var(--ke-surface-raised);
    border-radius: 8px;
    padding: 4px;
    box-shadow: var(--ke-shadow-panel);
  }
  .selection-menu button {
    border: 0;
    background: transparent;
    color: var(--ke-text-primary);
    border-radius: 4px;
    padding: 8px;
    cursor: pointer;
    font: 12px var(--ke-font-ui);
  }
  .selection-menu button:hover {
    background: var(--ke-surface-inset);
  }
  @container (max-width: 600px) {
    .surface.both {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
    }
    .document-page {
      padding: 24px 18px;
    }
    .writing :global(.cm-content) {
      padding: 24px 18px !important;
    }
  }
  @media (max-width: 700px) {
    .surface.both {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
    }
    .document-page {
      padding: 24px 18px;
    }
  }
  .preview :global(table) {
    display: block;
    overflow: auto;
    border-collapse: collapse;
  }
  .preview :global(th),
  .preview :global(td) {
    padding: 7px 10px;
    border-bottom: 1px solid var(--ke-border);
    text-align: left;
  }
  .preview :global(pre) {
    overflow: auto;
  }
  .preview :global(h1),
  .preview :global(h2),
  .preview :global(h3) {
    line-height: 1.3;
    font-family: var(--ke-font-serif, Georgia, serif);
    font-weight: 400;
  }
  .preview :global(h1) {
    font-size: 2em;
  }
  .preview :global(h2) {
    font-size: 1.65em;
  }
  .preview :global(h3) {
    font-size: 1.35em;
  }
  .empty {
    color: var(--ke-text-tertiary);
    font-size: 12px;
  }
  [hidden] {
    display: none !important;
  }
</style>
