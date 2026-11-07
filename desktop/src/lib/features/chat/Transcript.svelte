<script lang="ts">
  import type { SkillSelection } from "$lib/api/wire.generated";
  export let maximumMessageBytes: number | null = null;
  export let selectedSkill: SkillSelection | null = null;
  import { onDestroy, onMount, tick } from "svelte";
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import {
    editSummary,
    type DocumentReference,
  } from "$lib/features/workspace/document-assist";
  import { groupDocumentTurns } from "$lib/features/workspace/document-conversation";
  import DocumentSessionCard from "./DocumentSessionCard.svelte";
  export let onOpenDocument:
    ((reference: DocumentReference) => Promise<void>) | undefined = undefined;
  let documentExpanded: Record<string, boolean> = {};
  import TranscriptBatch from "./TranscriptBatch.svelte";
  import { historyBatches } from "./history-batches";
  import ContextLine from "./ContextLine.svelte";
  import ReplyInformation from "./ReplyInformation.svelte";
  import MessageActions from "./MessageActions.svelte";
  import { phaseLabel, type Stream } from "./stream";
  import StudyRequestText from "./StudyRequestText.svelte";
  import type { StudyReference } from "./study-requests";
  export let onOpenStudy:
    ((reference: StudyReference) => Promise<void>) | undefined = undefined;
  import MessageBody from "./MessageBody.svelte";
  import ChatAvatar from "$lib/design/ChatAvatar.svelte";
  import { chatAppearance, rotatingKejtu } from "$lib/design/theme";
  import {
    imageParts,
    savedImages,
    type VisibleBody as Body,
    type ImageReader,
  } from "./images";
  import ImageGallery from "./ImageGallery.svelte";
  import Latest from "./Latest.svelte";
  import type { ReadingPosition } from "./reading-position";
  import Composer from "./Composer.svelte";
  import Activity from "./Activity.svelte";
  import { readMetrics, shouldFollow } from "./scroll";
  import type { BodyRef, Results, Turn } from "$lib/api/wire.generated";

  export let onImage: ImageReader = async () => {
    throw new Error("Image access is unavailable here.");
  };
  export let onImageViewer: (open: boolean) => void = () => {};

  export let selected = "";
  export let imported = false;
  export let onExchangeAction:
    | ((
        id: string,
        action: "save_memory" | "exclude" | "delete",
        text: string,
      ) => Promise<void>)
    | undefined = undefined;
  export let exchangeActionsDisabled = false;
  /** Backend title or first-question fallback for the conversation. */
  export let title = "";
  /** The conversation's stored creation timestamp, for the header date. */
  export let createdAt: string | null = null;
  /** A collapse affordance is per entry; state is local to this surface. */
  let collapsed: Record<string, boolean> = {};
  let information: { id: string; section: "activity" | "context" } | null =
    null;
  let informationConversation = selected;
  $: if (selected !== informationConversation) {
    informationConversation = selected;
    information = null;
  }
  $: informationTurn = information
    ? turns.find((turn) => turn.id === information?.id)
    : undefined;

  /**
   * Per-turn display data, derived at component level.
   *
   * This must be a reactive declaration rather than a function: Svelte 4
   * instruments component-level variables, so a function that reads `bodies`
   * inside its own body is never re-run when a body extent arrives, and the
   * entry's question would render empty forever.
   */
  $: turnViews = turns.map((turn, index) => {
    const userRefs = turn.bodies.filter((ref) => ref.role === "user");
    return {
      turn,
      index,
      userRefs,
      document:
        userRefs.length === 1
          ? editSummary(bodies[userRefs[0].body_id]?.content ?? "")
          : null,
      answerRef: turn.bodies.find((ref) => ref.role !== "user"),
      time: entryTime(turn.created_at),
      day: dayKey(turn.created_at),
      heading: dayHeading(turn.created_at),
      previousDay: index > 0 ? dayKey(turns[index - 1]?.created_at) : "",
    };
  });

  $: documentSessions = groupDocumentTurns(turnViews);
  $: batches = historyBatches(turnViews);
  let forcedEntry = "";

  /**
   * Calendar-day key for a turn, or '' when the turn has no usable timestamp.
   *
   * The journal only draws a divider when the DAY changes (`Transcript.svelte:160`
   * in the old app), so the key is the local calendar date, not the raw value.
   */
  export function dayKey(value: string | null | undefined): string {
    if (!value) return "";
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "";
    return `${at.getFullYear()}-${at.getMonth()}-${at.getDate()}`;
  }

  const timeFormat = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  const headerFormat = new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const dayFormat = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  /** `10:47 PM` — the journal's per-entry time, matching `journal/dates.ts`. */
  export function entryTime(value: string | null | undefined): string {
    if (!value) return "";
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "";
    return timeFormat.format(at);
  }

  /** `5 October 2026` — the header eyebrow, uppercased by CSS. */
  export function headerDate(value: string | null | undefined): string {
    if (!value) return "Original date unavailable";
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "Original date unavailable";
    return headerFormat.format(at);
  }

  /** `Wednesday, October 5, 2026` — the journal's day heading. */
  export function dayHeading(value: string | null | undefined): string {
    if (!value) return "Original date unavailable";
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "Original date unavailable";
    return dayFormat.format(at);
  }
  export let turns: Turn[] = [];
  export let stream: Stream | null = null;
  export let progressPaused = false;
  export let onInspect: ((id: string) => void) | undefined = undefined;
  export let readInspection:
    | ((id: string, cursor: string | null) => Promise<Results["turn.inspect"]>)
    | undefined = undefined;
  export let bodies: Record<string, Body> = {};
  export let bodyLoading: Record<string, boolean> = {};
  export let loading = false;
  export let loaded = false;
  export let canEarlier = false;
  export let onEarlier: () => Promise<void> = async () => {};
  export let onMore: (turnId: string, ref: BodyRef) => void = () => {};

  /**
   * Composer state, passed rather than slotted.
   *
   * A slot cannot reach here: `AppShell` renders `<slot />` into the reading
   * column, and slotted content is not forwarded through a component boundary, so
   * a `slot="composer"` child of the shell never arrives. The composer is a child
   * of this component instead, and its state belongs to the coordinator.
   */
  export let showComposer = false;
  export let draft = "";
  export let composerDisabled = false;
  export let composerReadOnly = false;
  export let sending = false;
  export let working = false;
  export let stopping = false;
  export let onSend: () => void = () => {};
  export let onStop: () => void = () => {};
  let composer: Composer | undefined;
  export function focusComposer() {
    composer?.focus();
  }

  /** The journal shell owns scrolling; standalone hosts can use the local box. */
  export let scrollElement: HTMLElement | undefined = undefined;
  export let visible = true;
  export let positions = new Map<string, ReadingPosition>();
  export let onRemember: (
    id: string,
    position: ReadingPosition,
  ) => void = () => {};
  export let following = true;
  let localScroller: HTMLElement | undefined;
  let scroller: HTMLElement | undefined;
  let feed: HTMLElement | undefined;
  let sizeObserver: ResizeObserver | undefined;
  let gestureTimer: ReturnType<typeof setTimeout> | undefined;
  let detach = () => {};
  let mounted = false;
  let gestureActive = false;
  let anchoredId = "";
  let anchoredOffset = 0;
  let lastFirst = "";
  let lastCount = 0;
  let wasVisible = visible;
  let suspended: ReadingPosition | undefined;
  let restorePending = true;
  let followFrame: number | undefined;
  let lastFollowFrame = 0;
  let requestingEarlier = false;
  let upwardIntent = false;
  let lastScrollTop = 0;

  $: scroller = scrollElement ?? localScroller;
  $: if (mounted && scroller) listen(scroller);
  $: plan(turns);
  $: if (visible !== wasVisible) {
    if (!visible) {
      suspended = remember();
      stopFollowMotion();
    }
    wasVisible = visible;
    if (visible)
      void tick().then(async () => {
        if (suspended) await applyPosition(suspended);
        suspended = undefined;
        settle(true);
      });
  }
  $: if (mounted && loaded && visible && restorePending) {
    restorePending = false;
    void tick().then(async () => {
      const saved = positions.get(selected);
      if (saved) await applyPosition(saved);
      settle(true);
    });
  }

  function anchorNode(id: string): HTMLElement | undefined {
    // Match identity directly rather than interpolating an ID into CSS.
    return Array.from(
      feed?.querySelectorAll<HTMLElement>("[data-turn]") ?? [],
    ).find((node) => node.dataset.turn === id && !node.hidden);
  }

  function captureAnchor() {
    if (!scroller || !visible || scroller.clientHeight === 0) return;
    const top = scroller.getBoundingClientRect().top;
    // A fast scrollbar jump may enter a parked batch before its intersection
    // callback. Do not replace the bookmark with the distant newest entry.
    const parked = feed?.querySelectorAll<HTMLElement>(
      '[data-suspended="true"]',
    );
    if (
      Array.from(parked ?? []).some((node) => {
        const rect = node.getBoundingClientRect();
        return rect.top <= top && rect.bottom > top;
      })
    ) {
      anchoredId = "";
      return;
    }
    const nodes = Array.from(
      feed?.querySelectorAll<HTMLElement>("[data-turn]") ?? [],
    );
    const node =
      nodes.find(
        (item) => !item.hidden && item.getBoundingClientRect().bottom > top,
      ) ?? nodes.filter((item) => !item.hidden).at(-1);
    if (!node) return;
    anchoredId = node.dataset.turn ?? "";
    anchoredOffset = entryTop(node) - top;
  }

  function entryTop(node: HTMLElement): number {
    // The first entry gains a separator/padding after a prepend. Anchor its
    // content, so that change in the outer box cannot shift the text.
    return (node.querySelector(".entry-meta") ?? node).getBoundingClientRect()
      .top;
  }

  /** Called before a page replacement, hiding the reader or leaving a conversation. */
  export function remember(capture = true): ReadingPosition | undefined {
    if (!scroller || !selected) return undefined;
    if (
      !visible ||
      !loaded ||
      loading ||
      restorePending ||
      scroller.clientHeight === 0
    )
      return suspended ?? positions.get(selected);
    if (capture) captureAnchor();
    const position = {
      top: scroller.scrollTop,
      following,
      anchorId: anchoredId,
      anchorOffset: anchoredOffset,
      anchorSequence: turns.find((turn) => turn.id === anchoredId)?.sequence,
    };
    positions.set(selected, position);
    onRemember(selected, position);
    return position;
  }

  async function applyPosition(position: ReadingPosition) {
    if (!scroller) return;
    stopFollowMotion();
    forcedEntry = position.anchorId;
    await tick();
    following = position.following;
    anchoredId = position.anchorId;
    anchoredOffset = position.anchorOffset;
    scroller.scrollTop = position.top;
    if (!following) holdAnchor();
  }

  /** Prepending history keeps the current entry at the same screen offset. */
  export function holdReadingPosition() {
    remember();
    stopFollowMotion();
    following = false;
    if (suspended) suspended.following = false;
  }

  function plan(next: Turn[]) {
    const first = next[0]?.id ?? "";
    if (next.length > lastCount && lastFirst && first !== lastFirst) {
      holdReadingPosition();
      // Anchor correction also emits scroll events. End the completed gesture
      // before inserting; those events must not select a newly inserted entry.
      gestureActive = false;
      clearTimeout(gestureTimer);
      void tick().then(() => settle());
    }
    lastFirst = first;
    lastCount = next.length;
  }

  function holdAnchor() {
    if (!scroller || !visible || scroller.clientHeight === 0 || !anchoredId)
      return;
    const node = anchorNode(anchoredId);
    // Initial body pages can arrive later; keep the anchor until it is available.
    if (!node) return;
    const drift =
      entryTop(node) - scroller.getBoundingClientRect().top - anchoredOffset;
    // WebKit rounds scroll offsets to integer pixels. Round deliberately so
    // repeated history prepends do not accumulate a fractional downward drift.
    if (Math.abs(drift) > 0.5)
      scroller.scrollTop = Math.round(scroller.scrollTop + drift);
  }

  function stopFollowMotion() {
    if (followFrame !== undefined) cancelAnimationFrame(followFrame);
    followFrame = undefined;
    lastFollowFrame = 0;
  }

  // Reuse the live query: allocating one on every follow frame creates needless
  // browser work. Its matches value still follows OS preference changes.
  let motionPreference: MediaQueryList | undefined;
  function reduceMotion() {
    motionPreference ??= window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    );
    return motionPreference?.matches;
  }

  function followBottom(now: number) {
    followFrame = undefined;
    if (!visible || !loaded || restorePending || !scroller || !following) {
      lastFollowFrame = 0;
      return;
    }
    const target = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    const distance = target - scroller.scrollTop;
    if (Math.abs(distance) <= 0.75 || reduceMotion()) {
      scroller.scrollTop = target;
      lastFollowFrame = 0;
      return;
    }
    // Retarget one animation as text arrives; never restart a browser smooth
    // scroll on every chunk. Bound speed after a large insertion or paused tab.
    const elapsed = lastFollowFrame ? Math.min(now - lastFollowFrame, 32) : 16;
    lastFollowFrame = now;
    const step = Math.max(
      1,
      Math.min(
        Math.abs(distance) * (1 - Math.exp(-elapsed / 85)),
        elapsed * 1.4,
      ),
    );
    scroller.scrollTop += Math.sign(distance) * step;
    followFrame = requestAnimationFrame(followBottom);
  }

  function settle(immediate = false) {
    if (
      !visible ||
      !loaded ||
      restorePending ||
      !scroller ||
      scroller.clientHeight === 0
    )
      return;
    if (!following) {
      stopFollowMotion();
      // Native scrolling can advance before its next DOM scroll event. A new
      // streamed line must not restore the previous anchor against that motion.
      if (!gestureActive) holdAnchor();
    } else if (immediate || reduceMotion()) {
      stopFollowMotion();
      scroller.scrollTop = scroller.scrollHeight;
    } else if (followFrame === undefined) {
      followFrame = requestAnimationFrame(followBottom);
    }
  }

  export function toLatest() {
    forcedEntry = "";
    stopFollowMotion();
    anchoredId = "";
    following = true;
    if (scroller && visible) scroller.scrollTop = scroller.scrollHeight;
    remember();
  }

  /** Index navigation shares the same follow owner as wheel and Latest. */
  export async function jumpToEntry(id: string) {
    forcedEntry = id;
    stopFollowMotion();
    following = false;
    gestureActive = false;
    clearTimeout(gestureTimer);
    await tick();
    const node = anchorNode(id);
    if (!node || !scroller) return;
    scroller.scrollTop +=
      node.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      16;
    captureAnchor();
    remember();
  }

  async function earlierMessages() {
    if (
      !selected ||
      !turns.length ||
      !canEarlier ||
      loading ||
      !loaded ||
      requestingEarlier ||
      !visible
    )
      return;
    requestingEarlier = true;
    upwardIntent = false;
    try {
      await onEarlier();
    } finally {
      requestingEarlier = false;
      // Let the prepend render and restore its original anchor before saving;
      // otherwise the new first entry would replace the reader's bookmark.
      await tick();
      settle();
      if (scroller) lastScrollTop = scroller.scrollTop;
      remember(false);
    }
  }

  function finishGestureSoon() {
    clearTimeout(gestureTimer);
    gestureTimer = setTimeout(() => {
      gestureActive = false;
      if (!scroller || !visible) return;
      following = shouldFollow(readMetrics(scroller));
      remember();
    }, 120);
  }

  function onGesture(event: Event) {
    if (
      event.target instanceof Element &&
      event.target.closest(
        "form, textarea, input, .composer, .column-left, dialog",
      )
    )
      return;
    // A top-layer viewer still belongs to this DOM subtree. Panning it or
    // opening a thumbnail must not interrupt following or page the journal.
    if (
      event.type === "pointerdown" &&
      event.target instanceof Element &&
      event.target.closest(".image-gallery")
    )
      return;
    if (
      event instanceof KeyboardEvent &&
      ![
        "ArrowUp",
        "ArrowDown",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        " ",
      ].includes(event.key)
    )
      return;
    following = false;
    upwardIntent =
      event instanceof WheelEvent
        ? event.deltaY < 0
        : event instanceof KeyboardEvent
          ? ["ArrowUp", "PageUp", "Home"].includes(event.key)
          : true;
    if (scroller) lastScrollTop = scroller.scrollTop;
    gestureActive = true;
    forcedEntry = "";
    stopFollowMotion();
    anchoredId = "";
    finishGestureSoon();
    // At scrollTop=0 another upward wheel/key may produce no scroll event.
    if (
      (event instanceof WheelEvent || event instanceof KeyboardEvent) &&
      upwardIntent &&
      scroller &&
      scroller.scrollTop <= 48
    )
      void earlierMessages();
  }

  function onScroll() {
    // Restoring an anchor itself emits scroll events. Only real gestures may
    // replace it; otherwise early short body extents would corrupt the saved ID.
    if (gestureActive && !following && visible) {
      captureAnchor();
      // Trackpad momentum may continue after the final wheel/touch event.
      finishGestureSoon();
    }
    if (!scroller) return;
    const movingUp = scroller.scrollTop < lastScrollTop;
    lastScrollTop = scroller.scrollTop;
    if (gestureActive && upwardIntent && movingUp && scroller.scrollTop <= 48)
      void earlierMessages();
  }

  function listen(node: HTMLElement) {
    detach();
    const gestures = ["wheel", "touchmove", "keydown", "pointerdown"] as const;
    for (const name of gestures)
      node.addEventListener(name, onGesture, { passive: true });
    node.addEventListener("scroll", onScroll, { passive: true });
    detach = () => {
      for (const name of gestures) node.removeEventListener(name, onGesture);
      node.removeEventListener("scroll", onScroll);
    };
  }

  // Ordinary completion needs no badge. Retain a small notice only when an
  // answer did not finish; backend details remain available in Settings.
  function notice(status: Turn["status"]): string {
    switch (status) {
      case "running":
        return working
          ? ""
          : "Progress unavailable. Refresh the conversation to check.";
      case "completed":
        return "";
      case "cancelled":
        return "Response stopped.";
      case "failed":
        return "Couldn’t complete the response.";
      case "interrupted":
        return "Response interrupted.";
    }
  }

  onMount(() => {
    mounted = true;
    const saved = positions.get(selected);
    following = saved?.following ?? true;
    if (saved) {
      following = saved.following;
      anchoredId = saved.anchorId;
      anchoredOffset = saved.anchorOffset;
    }
    if (typeof ResizeObserver !== "undefined" && feed) {
      sizeObserver = new ResizeObserver(() => settle());
      sizeObserver.observe(feed);
    }
  });
  onDestroy(() => {
    // The coordinator saves before replacing the shared scroll contents. A
    // teardown read would observe a clamped offset from the next conversation.
    if (!scrollElement) remember();
    detach();
    sizeObserver?.disconnect();
    stopFollowMotion();
    clearTimeout(gestureTimer);
  });
</script>

{#if information && informationTurn}
  <ReplyInformation
    turn={informationTurn}
    section={information.section}
    {readInspection}
    onClose={() => (information = null)}
  />
{/if}

<div class="wrap" class:external={!!scrollElement}>
  {#if loading && !loaded}
    <p class="loading" role="status">Loading conversation page…</p>
  {/if}

  <div
    class="turns"
    role="region"
    aria-label="Conversation transcript"
    bind:this={localScroller}
    use:quietScrollbar
  >
    <div
      class="feed"
      class:restoring={restorePending && !!selected}
      bind:this={feed}
    >
      <!-- The journal opens a conversation with an eyebrow date over a serif
           title and a quiet separator. -->
      {#if selected || loaded}
        <header class="day-header">
          <div class="eyebrow">{headerDate(createdAt)}</div>
          <h1>{title || "A new conversation"}</h1>
          {#if imported}
            <p class="import-notice">
              Imported conversation. Kejtu can recall this history; start a new
              conversation to reply.
            </p>
          {/if}
        </header>
      {/if}
      {#if selected && loaded && turns.length && (canEarlier || requestingEarlier)}
        <div
          class="history-start"
          aria-live="polite"
          aria-busy={requestingEarlier || loading}
        >
          <button
            type="button"
            class="earlier"
            disabled={loading || requestingEarlier}
            on:click={earlierMessages}
          >
            <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true"
              ><path d="m4.5 10.5 4.5-4.5 4.5 4.5" /></svg
            >
            {loading || requestingEarlier
              ? "Loading earlier messages…"
              : "Earlier messages"}
          </button>
        </div>
      {/if}
      {#each batches as batch, batchIndex (batch.key)}
        <TranscriptBatch
          scrollElement={scroller}
          batchId={batch.key}
          pinned={batchIndex === batches.length - 1 ||
            batch.entries.some(
              (view) =>
                view.turn.id === forcedEntry ||
                view.turn.id === information?.id ||
                view.turn.status === "running",
            )}
        >
          {#each batch.entries as view (view.turn.id)}
            {@const live = stream?.turn_id === view.turn.id ? stream : null}
            {@const ref = view.answerRef}
            {@const body = ref ? bodies[ref.body_id] : undefined}
            {@const session = documentSessions[view.turn.id]}
            {@const expanded =
              !session ||
              documentExpanded[session.key] ||
              (forcedEntry !== session.head &&
                session.ids.includes(forcedEntry)) ||
              session.ids.includes(information?.id ?? "")}

            <!-- The journal draws a divider only when the DAY changes
             (old `Transcript.svelte:160`), never between turns of one day. -->
            {#if view.index > 0 && view.day && view.day !== view.previousDay}
              <h2 class="day-divider">{view.heading}</h2>
            {/if}
            {#if session?.head === view.turn.id}
              <DocumentSessionCard
                {session}
                time={view.time}
                expanded={!!expanded}
                disabled={exchangeActionsDisabled}
                onOpen={onOpenDocument}
                onToggle={() =>
                  (documentExpanded = {
                    ...documentExpanded,
                    [session.key]: !documentExpanded[session.key],
                  })}
                onStop={session.running && working ? onStop : undefined}
                {stopping}
              />
            {/if}
            <section
              hidden={!expanded}
              class="turn"
              class:continued={view.index > 0 &&
                !(view.day && view.day !== view.previousDay)}
              class:first-entry={view.index === 0 &&
                !(canEarlier || requestingEarlier)}
              id={`turn-${view.turn.id}`}
              data-turn={view.turn.id}
            >
              <!-- Keep the useful time; actions live together beside the question. -->
              <div class="entry-meta">
                <time
                  datetime={view.turn.created_at ?? undefined}
                  title={view.turn.created_at ?? undefined}>{view.time}</time
                >
              </div>

              <div
                class="query"
                class:without-thumbnail={!$chatAppearance.userThumbnail}
              >
                {#if $chatAppearance.userThumbnail}<ChatAvatar
                    role="user"
                  />{/if}
                <div class="question-text">
                  {#each view.userRefs as ref (ref.body_id)}
                    <div class="question" data-body={ref.body_id}>
                      {#if bodies[ref.body_id]?.images}<StudyRequestText
                          text={bodies[ref.body_id]?.content || ""}
                          complete={bodies[ref.body_id]?.complete ?? false}
                          onOpen={onOpenStudy}
                          disabled={exchangeActionsDisabled}
                        />{:else}{#each imageParts(bodies[ref.body_id]?.content ?? "", bodies[ref.body_id]?.complete ?? false) as part}{#if part.kind === "pending"}<span
                              class="extent"
                              >Image preview needs the remaining message data.</span
                            >{:else if part.kind === "text"}<StudyRequestText
                              text={part.value}
                              complete={bodies[ref.body_id]?.complete ?? false}
                              onOpen={onOpenStudy}
                              disabled={exchangeActionsDisabled}
                            />{/if}{/each}{/if}
                    </div>
                    <ImageGallery
                      onViewerChange={onImageViewer}
                      images={savedImages(
                        bodies[ref.body_id],
                        view.turn.id,
                        onImage,
                      )}
                    />
                    {#if !bodies[ref.body_id]?.complete}
                      <p class="extent">
                        {bodies[ref.body_id]
                          ? `${bodies[ref.body_id].end} of ${bodies[ref.body_id].total_bytes} UTF-8 bytes shown`
                          : "No part of this body has been read yet."}
                      </p>
                      <button
                        type="button"
                        class="more-question"
                        disabled={bodyLoading[ref.body_id] ?? false}
                        on:click={() => onMore(view.turn.id, ref)}
                        >{bodies[ref.body_id]
                          ? "More body text"
                          : "Load body text"}</button
                      >
                    {/if}
                  {/each}
                </div>
                <MessageActions
                  collapsed={!!collapsed[view.turn.id]}
                  onToggleAnswer={() =>
                    (collapsed = {
                      ...collapsed,
                      [view.turn.id]: !collapsed[view.turn.id],
                    })}
                  answerText={view.turn.status !== "running" &&
                  (!live || live.status !== "running")
                    ? (live?.content ?? body?.content ?? "")
                    : ""}
                  answerComplete={live
                    ? live.caught_up
                    : (body?.complete ?? !ref)}
                  onDetails={!view.turn.imported &&
                  view.turn.status !== "running" &&
                  (!live || (live.status !== "running" && live.caught_up))
                    ? (section) => (information = { id: view.turn.id, section })
                    : undefined}
                  disabled={exchangeActionsDisabled}
                  recallExcluded={view.turn.recall_excluded ?? false}
                  partialRecallExcluded={view.turn.partial_recall_excluded ??
                    false}
                  onAction={onExchangeAction
                    ? (action, text) =>
                        onExchangeAction!(view.turn.id, action, text)
                    : undefined}
                  text={view.userRefs
                    .map((ref) => bodies[ref.body_id]?.content ?? "")
                    .join("")}
                  complete={view.userRefs.length > 0 &&
                    view.userRefs.every((ref) => bodies[ref.body_id]?.complete)}
                  onInspect={onInspect
                    ? () => onInspect?.(view.turn.id)
                    : undefined}
                />
              </div>

              <div
                class="response"
                class:without-thumbnail={!$chatAppearance.kejtuThumbnail}
                id={`reply-${view.turn.id}`}
                hidden={collapsed[view.turn.id]}
              >
                {#if $chatAppearance.kejtuThumbnail}<ChatAvatar
                    role="assistant"
                    working={view.turn.status === "running" &&
                      working &&
                      !progressPaused &&
                      !collapsed[view.turn.id] &&
                      !!expanded}
                    phase={live?.phase}
                  />{/if}
                <div class="response-content">
                  <!-- Turn identity owns this renderer, never terminal status/body identity. -->
                  <MessageBody
                    turnId={view.turn.id}
                    role={ref?.role === "partial" ? "partial" : "answer"}
                    state={ref?.state ?? "partial"}
                    content={live?.content ?? body?.content ?? ""}
                    complete={live ? live.caught_up : (body?.complete ?? !ref)}
                    streaming={!!live && live.status === "running"}
                    working={view.turn.status === "running"}
                    end={live?.end ?? body?.end ?? 0}
                    total={live?.total_bytes ?? body?.total_bytes ?? 0}
                    loading={ref ? (bodyLoading[ref.body_id] ?? false) : false}
                    hideRole
                    hideCopy
                    onMore={() => ref && onMore(view.turn.id, ref)}
                  >
                    {#if view.turn.status === "running" && working && !collapsed[view.turn.id]}
                      <Activity
                        showMark={!$rotatingKejtu}
                        paused={progressPaused}
                        phase={live?.phase}
                        label={progressPaused
                          ? "Progress unavailable"
                          : phaseLabel(live?.phase)}
                        {stopping}
                        {onStop}
                      />
                    {/if}
                  </MessageBody>
                  <!-- Status can finish before the last stream page arrives. Keep
                 inspection out of the working reply until both have settled. -->
                  {#if !view.turn.imported && view.turn.status !== "running" && (!live || (live.status !== "running" && live.caught_up))}
                    <ContextLine turn={view.turn} />
                  {/if}
                  {#if notice(view.turn.status)}
                    <p class="notice" role="status">
                      {notice(view.turn.status)}
                    </p>
                    {#if view.turn.status === "failed" && view.turn.detail}
                      <p class="detail">{view.turn.detail}</p>
                    {/if}
                  {/if}
                </div>
              </div>
            </section>
          {/each}
        </TranscriptBatch>
      {/each}
      {#if working && !turnViews.some((view) => view.turn.status === "running" && !collapsed[view.turn.id])}
        <!-- Keep cancellation reachable when the active answer is collapsed or
             the reader has opened another conversation. Never duplicate it. -->
        <Activity
          paused={progressPaused}
          label={turns.some((turn) => turn.status === "running")
            ? "Preparing a response…"
            : "Preparing a response in another conversation…"}
          {stopping}
          {onStop}
        />
      {/if}
    </div>

    <!-- The composer is the column's LAST CHILD, inside the scroll container, as
         the journal's QuestionField is the last child of `.journal-chat`
         (`+page.svelte:1119`). It therefore scrolls with the entries instead of
         being pinned to the bottom, which is what gives a starting chat its
         "input right under the header" behaviour and puts the only gap between
         the field and the window edge, at the very bottom. -->
    {#if showComposer && !imported}
      <div class="composer">
        <Composer
          {maximumMessageBytes}
          {onOpenStudy}
          bind:this={composer}
          bind:draft
          conversationId={selected}
          bind:selectedSkill
          empty={turns.length === 0}
          autoFocus={visible && loaded && turns.length === 0}
          disabled={composerDisabled}
          readOnly={composerReadOnly}
          {sending}
          {onSend}
          {onImageViewer}
        />
      </div>
    {/if}
  </div>

  {#if !scrollElement}
    <div class="latest-rail">
      <Latest visible={!following && turns.length > 0} onLatest={toLatest} />
    </div>
  {/if}
</div>

<style>
  .import-notice {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-base);
    line-height: 1.5;
    margin-top: 0.75rem;
  }
  .feed.restoring {
    /* Keep layout measurable but reveal history only at its restored position. */
    visibility: hidden;
  }

  .wrap {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1;
  }
  .loading {
    margin: 0;
    padding: var(--ke-space-2) var(--ke-space-4);
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }
  /* `.journal-reading` is a flex column with a 1.5rem gap; that gap is the whole
     of the space between the header and the first entry, and between the entries
     and the composer. */
  .turns {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    overflow: auto;
    overscroll-behavior-y: contain;
  }

  .turns {
    /* The journal grid owns all outer insets. */
    padding: 0;
  }

  /* The grid owns the shared top inset; no second offset in the reading column. */
  .day-header {
    margin-top: 0;
  }
  /* Keep a little breathing room around the separator between exchanges. */
  .turn.continued {
    margin-top: 1.25rem;
    padding-top: 1.125rem;
    border-top: 1px solid var(--ke-border);
  }

  /* Align the first exchange with the compact separator rhythm. */
  .turn.first-entry {
    margin-top: 1.125rem;
  }
  .history-start {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 48px;
    padding: 0.5rem 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }
  .earlier {
    display: inline-flex;
    align-items: center;
    gap: var(--ke-space-2);
    border: 0;
    border-radius: var(--ke-radius-sm);
    padding: var(--ke-space-2) var(--ke-space-3);
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .earlier:hover:not(:disabled) {
    color: var(--ke-text-primary);
  }
  .earlier:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  .earlier:disabled {
    cursor: default;
  }
  .earlier svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.25;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .entry-meta {
    display: flex;
    align-items: center;
    min-height: 18px;
    margin: 0 0 8px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
  }

  /* The day heading only appears when the day changes: mono 11px, spaced, with a
     hairline above and 23px of air (old `.journal-date-divider`). */
  .day-divider {
    margin: 12px 0 0;
    padding-top: 23px;
    border-top: 1px solid var(--ke-border);
    color: var(--ke-text-secondary);
    font-family: var(--ke-font-mono);
    font-size: var(--ke-text-2xs);
    font-weight: 400;
    letter-spacing: 0.06em;
  }

  /* Conversation header: mono uppercase eyebrow, serif title, hairline, air. */
  .day-header {
    padding: 0 0 22px;
    border-bottom: 1px solid var(--ke-border);
  }

  .eyebrow {
    font: var(--ke-label-font);
    letter-spacing: var(--ke-label-tracking);
    text-transform: uppercase;
    color: var(--ke-text-tertiary);
  }

  .day-header h1 {
    margin: 13px 0 0;
    font: var(--ke-title-page);
    letter-spacing: var(--ke-title-page-tracking);
    color: var(--ke-text-primary);
    overflow-wrap: anywhere;
  }

  /* Journal questions and responses share the same reading measure. */
  .query {
    min-width: 0;
    display: grid;
    grid-template-columns: 24px minmax(0, 1fr) auto;
    align-items: start;
    gap: var(--ke-space-3);
  }
  .question-text {
    min-width: 0;
  }
  .query.without-thumbnail {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  /* A smaller reading size, with the question distinguished by weight. */
  .question {
    margin: 0;
    white-space: pre-wrap;
    color: var(--ke-text-primary);
    font: var(--ke-chat-user-font);
    overflow-wrap: anywhere;
  }

  /* Continuation for a truncated question, mirroring MessageBody's affordance. */
  .extent {
    margin: var(--ke-space-1) 0 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }

  .more-question {
    margin-top: var(--ke-space-1);
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    text-decoration: underline;
    cursor: pointer;
  }

  /* A compact reading rhythm; finished replies reserve no toolbar row. */
  .response {
    min-width: 0;
    --ke-chat-answer-size: 12.5px;
    --ke-chat-answer-inset: 0;
    --ke-chat-line-height: 1.65;
    margin-top: 14px;
    margin-inline: 0;
    display: grid;
    grid-template-columns: 24px minmax(0, 1fr);
    align-items: start;
    gap: var(--ke-space-3);
  }
  .response-content {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .response.without-thumbnail {
    grid-template-columns: minmax(0, 1fr);
  }
  .notice,
  .detail {
    margin: 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    line-height: 1.6;
  }
  /* A fixed-height rail centred under the reading column. It is a sibling of the
     scroll container, so the control stays put while the transcript scrolls. */
  .latest-rail {
    flex: 0 0 2rem;
    display: flex;
    justify-content: center;
    align-items: center;
  }

  .external .turns {
    overflow: visible;
    flex: none;
  }
  .external {
    flex: none;
  }
  @media (max-width: 1180px) {
    .day-header h1 {
      font-size: 30px;
    }
  }
</style>
