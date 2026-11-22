<script lang="ts">
  import { documentViewportHeight } from "./document-dock";
  import { onMount, tick, getContext } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { identity } from "$lib/api/client";
  import Icon from "$lib/design/Icon.svelte";
  import DocumentPreview from "./DocumentPreview.svelte";
  import type {
    DocumentDiscussionState,
    DocumentDiscussionActions,
  } from "./document-discussion";
  import DocumentActivity from "./DocumentActivity.svelte";
  import { workProgress } from "./document-work-progress";
  import { phaseLabel, type Stream } from "$lib/features/chat/stream";
  import { replyState } from "$lib/design/kejtu-state";
  import "./document-chrome.css";
  import { documentScrollbars } from "./document-scrollbars";
  import type { DocumentSource } from "./document-source";
  export let sourceName = "";
  export let prepareSource:
    ((signal: AbortSignal) => Promise<DocumentSource>) | undefined = undefined;
  let usePdf = true;
  let preparingSource = false;
  import ReaderNotice from "./ReaderNotice.svelte";
  import { readCourseDocument } from "./document-bytes";
  import { documentDirectory } from "./document-save";
  import { registerReader, refreshReaderDrafts } from "./reader-drafts";
  import {
    mountMarkdown,
    markdownBytes,
    type MarkdownView,
  } from "./markdown-editor";
  import { readable, writable } from "svelte/store";
  import {
    DOCUMENT_ASSIST,
    workRequest,
    editPrompt,
    readDocumentReply,
    proposalText,
    type DocumentReply,
    type DocumentReference,
    type DocumentAssistant,
    type EditTarget,
  } from "./document-assist";
  const assistant = getContext<DocumentAssistant | undefined>(DOCUMENT_ASSIST);
  import { belongsToDocument } from "./document-conversation";

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
  const discussion = assistant?.discussion ?? readable(null);
  $: panelOpen = $discussion?.reference.session_id === sessionId;
  // A hidden/closed editor cannot remain the target of the main composer.
  // Its unsent instruction stays with this mounted editor for reopening.
  $: if (!active && panelOpen && !asking)
    assistant?.releaseDiscussion?.(sessionId);
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
            // Keep the rich-text bookmark for an unchanged target range.
            if (
              !target?.valid ||
              target.from !== range.from ||
              target.to !== range.to
            )
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
    if (!block) return null;
    const after = target.from === Number(block.dataset.documentTo);
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) =>
        node.textContent?.trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_SKIP,
    });
    let node = walker.nextNode();
    if (after) while (walker.nextNode()) node = walker.currentNode;
    if (!node) return null;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects());
    const rect = after ? rects.at(-1) : rects[0];
    return rect
      ? {
          left: after ? rect.right : rect.left,
          top: rect.top,
          height: rect.height,
        }
      : null;
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
  let zoom = 100;
  let focusHint = "";
  let menu: { x: number; y: number; label: string } | null = null;
  let menuElement: HTMLDivElement;
  async function openPanel() {
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
    menu = null;
    try {
      await assistant?.discuss?.({
        reference: documentReference,
        model: discussionModel,
        actions: discussionActions,
      });
    } catch (e) {
      assistError = e instanceof Error ? e.message : String(e);
    }
  }
  // The "···" document options close on a click anywhere outside them, like
  // the selection menu (and on Escape, in documentKeys).
  let optionsMenu: HTMLDetailsElement | undefined;
  function dismissMenu(e: PointerEvent) {
    if (menu && !menuElement?.contains(e.target as Node)) menu = null;
    if (
      optionsMenu?.open &&
      !(e.target instanceof Node && optionsMenu.contains(e.target))
    )
      optionsMenu.open = false;
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
      // The text the menu's card shows in the chat box and the request
      // carries as focus_hint: the marked blocks, or the selected source.
      let marked = "";
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
        marked = blocks
          .map((item) => item.textContent?.trim() ?? "")
          .filter(Boolean)
          .join("\n\n");
      }
      if (origin === "source") marked = text.slice(range.from, range.to);
      e.preventDefault();
      target = view.markTarget(range);
      focusHint = marked;
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
        void openPanel();
      }
    }
  }

  function insertAfter() {
    if (!target || !view || !menu) return;
    target = view.markTarget({ from: target.to, to: target.to });
    void openPanel();
  }
  function wholeDocument() {
    if (!view || asking) return;
    view.clearTarget();
    target = null;
    focusHint = "";
  }

  function documentKeys(e: KeyboardEvent) {
    if (e.key === "Escape" && optionsMenu?.open) {
      e.preventDefault();
      e.stopPropagation();
      if (optionsMenu.contains(document.activeElement))
        optionsMenu.querySelector("summary")?.focus({ preventScroll: true });
      optionsMenu.open = false;
      return;
    }
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
      const response = await assistant.ask(editPrompt(request), signal, () => {
        // Keep uncertain submissions and newer typing. The saved exchange owns
        // accepted text even when the answer later fails or is stopped.
        if (!disposed && instruction === submitted) instruction = "";
        // As with a marked PDF passage, sending unmarks: the sent question
        // keeps its card, the chat box and the document are clear again.
        if (!disposed) {
          view?.clearTarget();
          target = null;
          focusHint = "";
        }
      });
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
  > = async () => courseFolder;
  export let file = "";
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
  let text = "",
    saved = "",
    preview = "";
  let ready = false,
    saving = false,
    disposed = false,
    dirty = false;
  let mode: "edit" | "write" | "preview" | "both" | "diff" = file
    ? "preview"
    : "edit";
  let height = 600,
    error = "",
    notice = "";
  let name = file.split("/").pop() || "Untitled.md";
  let documentPath: string | null = null;
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
      height = documentViewportHeight(surface);
      view?.measure();
    });
  }
  // Preview and Compare are views you step into: pressing the lit button again
  // steps back to the view you came from (Write unless it was the source).
  let returnMode: "edit" | "write" | "both" = "edit";
  function toggleView(value: "preview" | "diff") {
    void changeMode(mode === value ? returnMode : value);
  }
  async function changeMode(value: typeof mode) {
    if (
      (value === "preview" || value === "diff") &&
      mode !== "preview" &&
      mode !== "diff"
    )
      returnMode = mode;
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
      const initial =
        documentDirectory(documentPath) ?? (await chooseSaveFolder());
      if (initial === undefined || disposed) return;
      const path = await invoke<string | null>("save_markdown_copy", {
        name,
        data: markdownBytes(snapshot),
        initial,
      });
      if (disposed) return;
      if (path) {
        documentPath = path;
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
          documentPath = original.path;
          text = new TextDecoder("utf-8", {
            fatal: true,
            ignoreBOM: true,
          }).decode(original.bytes);
        }
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
        if (active && !file) view.focus();
        if (discussionRequested) await openDiscussion();
      } catch (e) {
        if (!disposed) error = e instanceof Error ? e.message : String(e);
      }
    })();
    return () => {
      disposed = true;
      assistant?.releaseDiscussion?.(sessionId);
      clearTimeout(progressTimer);
      controller?.abort();
      clearTimeout(timer);
      cancelAnimationFrame(paint);
      observer?.disconnect();
      view?.destroy();
      unregister();
    };
  });
  const discussionModel = writable<DocumentDiscussionState | null>(null);
  const discussionActions: DocumentDiscussionActions = {
    input: (value) => (instruction = value),
    source: (value) => (usePdf = value),
    wholeDocument,
    toggleHistory: () => {
      expanded = !expanded;
    },
    moreHistory,
    dismissProposal: () => {
      proposal = null;
      assistError = "";
    },
    dismissError: () => {
      assistError = "";
    },
    dismissResult: () => {
      result = "";
    },
    undo: () => {
      if (!appliedState || text !== appliedState.text) return;
      view?.undo();
      assistant?.outcome?.(appliedState.requestId, "undone");
      result = "";
      appliedState = null;
    },
    instructionKeys,
    ask,
    stop: stopEdit,
    resolve: () => assistant?.resolve(),
  };
  $: discussionModel.set({
    sessionId,
    focus: target?.valid
      ? target.from === target.to
        ? "Insertion point"
        : "Selected passage"
      : focusHint
        ? "Selected passage"
        : "This document",
    hasFocus: !!(target || focusHint),
    marked: target?.valid
      ? focusHint || (target.from === target.to ? "" : target.selected)
      : focusHint,
    insertion: !!target?.valid && target.from === target.to,
    asking,
    sourceName,
    usePdf,
    expanded,
    visibleExchanges,
    exchanges,
    canEarlier: $history.canEarlier,
    loading: $history.loading,
    hasHistory: !!assistant?.history,
    proposal,
    assistError,
    answer,
    liveAnswer,
    result,
    canUndo: !!appliedState && text === appliedState.text,
    undoBlocked: !!appliedState && text !== appliedState.text,
    instruction,
    ready,
    stopped,
    available: $available,
    pending: $pending,
    phase: asking
      ? stopped
        ? "Stopping…"
        : preparingSource
          ? "Checking the PDF source…"
          : liveDocument !== null
            ? "Writing a live preview…"
            : phaseLabel($progress?.phase)
      : "",
    kejtu: asking
      ? stopped
        ? "idle"
        : preparingSource
          ? "reading"
          : liveDocument !== null
            ? "writing"
            : replyState($progress?.phase)
      : null,
  });
</script>

<svelte:window
  on:resize={resize}
  on:beforeunload={unload}
  on:pointerdown={dismissMenu}
  on:keydown|capture={documentKeys}
/>
<div class="markdown-editor">
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
      on:click={() =>
        appliedState && text === appliedState.text
          ? discussionActions.undo()
          : view?.undo()}><Icon name="undo" size={16} /></button
    >
    <button
      class="icon"
      aria-label="Redo Markdown edit"
      title="Redo"
      disabled={!ready || liveDocument !== null}
      on:click={() => view?.redo()}><Icon name="redo" size={16} /></button
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
        aria-label="Preview"
        title="Preview document"
        aria-pressed={mode === "preview"}
        on:click={() => toggleView("preview")}
        ><Icon name="file" size={17} /></button
      >
      <button
        aria-label="Compare"
        title="Compare with the opened document"
        aria-pressed={mode === "diff"}
        on:click={() => toggleView("diff")}
        ><Icon name="compare" size={17} /></button
      >
    </div>
    {#if assistant}<button
        class:working={asking}
        aria-label="Discuss document in chat"
        title={asking
          ? "Kejtu is working…"
          : panelOpen
            ? "Return to ordinary chat"
            : "Discuss in chat · Ctrl/⌘ Shift K"}
        aria-pressed={panelOpen}
        disabled={!ready}
        on:click={() =>
          panelOpen && !asking
            ? assistant?.releaseDiscussion?.(sessionId)
            : openPanel()}
        ><Icon
          name="comment"
          size={17}
        />{#if asking || instruction.trim() || proposal}<span class="dot"
          ></span>{/if}</button
      >{/if}
    <details class="document-options" bind:this={optionsMenu}>
      <summary aria-label="Document options" title="Document options"
        >···</summary
      >
      <div class="options-menu">
        <button
          aria-label="Source"
          aria-pressed={mode === "write"}
          on:click={() => changeMode("write")}>Edit Markdown source</button
        >
        <button
          aria-label="Both"
          aria-pressed={mode === "both"}
          on:click={() => changeMode("both")}>Source and preview</button
        >
        {#if discussionFile}<button
            aria-label="Read original Markdown"
            on:click={() => onReadOriginal(discussionFile)}
            >Read original Markdown</button
          >{/if}
        <details hidden={mode !== "edit"}>
          <summary>Formatting</summary>
          <div class="rich-tools" bind:this={richTools}></div>
        </details>
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
        {#if assistant}
          {#if target || focusHint}<button
              disabled={asking}
              on:click={wholeDocument}>Use whole document</button
            >{/if}
          {#if sourceName}<label class="reference-source"
              ><input
                type="checkbox"
                bind:checked={usePdf}
                disabled={asking}
              />Use {sourceName}</label
            >{/if}
          {#if proposal}<details class="unapplied-change">
              <summary>Unapplied change</summary>
              <pre>{proposalText(proposal)}</pre>
              <button on:click={discussionActions.dismissProposal}
                >Dismiss proposal</button
              >
            </details>{/if}
        {/if}
      </div>
    </details>
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
  </div>
  {#if error}<ReaderNotice
      error
      label="Dismiss Markdown error"
      onDismiss={() => (error = "")}>{error}</ReaderNotice
    >{/if}
  {#if assistError && !panelOpen}<ReaderNotice
      error
      label="Dismiss editing message"
      onDismiss={() => (assistError = "")}>{assistError}</ReaderNotice
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
      <!-- Each choice puts the marked passage (or the place after it) into the
           chat box as a card; Kejtu reads from the words whether to answer or
           edit, so Conversation and Edit send the same mark. -->
      <button on:click={() => menu && openPanel()}>Conversation</button>
      {#if menu.label}<button on:click={() => menu && openPanel()}
          >Edit {menu.label.toLowerCase()}</button
        >{/if}
      {#if target && target.from !== target.to}<button on:click={insertAfter}
          >Insert after</button
        >{/if}
      <button
        aria-label="Close selection menu"
        title="Close"
        on:click={() => (menu = null)}><Icon name="close" size={12} /></button
      >
    </div>
  {/if}
</div>

<style>
  .markdown-editor {
    min-width: 0;
  }
  .document-desk {
    position: relative;
    container-type: inline-size;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 260px;
    background: var(--ke-surface);
  }
  :global(.workspace) .document-toolbar {
    flex-wrap: wrap;
    height: auto;
    position: relative;
    gap: 2px;
    margin-bottom: 2px;
  }
  .mode-rail {
    margin-left: 6px;
  }
  .document-options {
    position: relative;
    flex: none;
  }
  .document-options > summary {
    display: grid;
    place-items: center;
    width: 28px;
    height: 32px;
    list-style: none;
    cursor: pointer;
    color: var(--ke-text-secondary);
  }
  .document-options > summary::-webkit-details-marker {
    display: none;
  }
  .document-options > summary:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
  }
  .document-options .options-menu {
    position: absolute;
    z-index: 6;
    right: 0;
    top: 100%;
    display: flex;
    flex-direction: column;
    min-width: 120px;
    width: max-content;
    max-width: min(280px, 70vw);
    gap: 4px;
    padding: 6px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface);
  }
  .zoom-controls {
    display: flex;
    gap: 2px;
  }
  .reference-source {
    display: flex;
    gap: 6px;
    padding: 6px;
    align-items: start;
    font-size: var(--ke-text-xs);
    overflow-wrap: anywhere;
  }
  .unapplied-change {
    padding: 6px;
    font-size: var(--ke-text-xs);
  }
  .unapplied-change pre {
    max-height: 180px;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .document-toolbar .state {
    flex: 1;
    margin-left: 4px;
  }
  .selection-menu button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -1px;
  }
  .document-toolbar .dot {
    position: absolute;
    top: 4px;
    right: 3px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--ke-marker-blue);
  }
  .zoom-value {
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
    padding: 0;
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
  .rich-tools {
    min-height: 0;
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    width: 44px;
    max-height: min(40vh, 240px);
    scrollbar-width: none;
  }
  .document-page {
    box-sizing: border-box;
    width: 100%;
    min-height: 100%;
    margin: 0 auto;
    padding: 18px 20px;
    background: transparent;
  }
  .both .document-page {
    padding: 18px 16px;
  }
  .selection-menu {
    display: flex;
    align-items: center;
    position: fixed;
    z-index: 61;
    max-width: calc(100vw - 16px);
    border: 1px solid var(--ke-border);
    background: var(--ke-surface-raised);
    border-radius: var(--ke-radius-md);
    padding: 4px;
    box-shadow: var(--ke-overlay-shadow);
    transform-origin: bottom center;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .selection-menu button {
    border: 0;
    background: transparent;
    color: var(--ke-text-primary);
    border-radius: var(--ke-radius-sm);
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
    font-family: var(--ke-font-heading);
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
    font-size: var(--ke-text-xs);
  }
  [hidden] {
    display: none !important;
  }
</style>
