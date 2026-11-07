<script lang="ts">
  import { readerDrafts } from "../workspace/reader-drafts";
  import type { SkillSelection } from "$lib/api/wire.generated";
  let selectedSkill: SkillSelection | null = null;
  import { writable } from "svelte/store";
  import {
    DOCUMENT_ASSIST,
    documentAnswer,
    type DocumentAssistant,
    type DocumentReference,
    type DocumentOutcome,
  } from "$lib/features/workspace/document-assist";
  import {
    documentHistory,
    type DocumentHistory,
  } from "$lib/features/workspace/document-conversation";
  const documentHistoryStore = writable<DocumentHistory>({
    conversationId: "",
    entries: [],
    canEarlier: false,
    loading: false,
  });
  let documentOutcomes: Record<string, DocumentOutcome> = {};
  const documentOpeners = new Map<string, () => Promise<void>>();
  async function openDocumentDiscussion(reference: DocumentReference) {
    if (ownerBusy || controlsLocked || loading)
      throw new Error("Finish the current action before opening the document.");
    if (
      !$workspace.snapshot?.settings.university_enabled ||
      !$workspace.snapshot.courses.some(
        (c) => c.id === reference.course_id && !c.cancelled,
      )
    )
      throw new Error(
        "This course is unavailable. Check Projects and its access settings.",
      );
    const opener = documentOpeners.get(reference.session_id);
    if (!opener && !reference.file)
      throw new Error(
        "This document is no longer open in Projects. Its discussion is still available in Details.",
      );
    await openWorkspace("course", reference.course_id);
    await tick();
    if (opener) {
      await workspaceView.showMaterial(reference.course_id);
      await opener();
    } else await workspaceView.openDocument(reference);
  }
  const documentAvailable = writable(false);
  const documentPending = writable(false);
  let documentEditing = false;
  const documentProgressStore = writable<Stream | null>(null);
  // Reuse the current foreground stream owner; the editor starts no second
  // stream, model request or retry. Final saved bodies remain authoritative.
  $: documentProgressStore.set(documentEditing ? stream : null);
  setContext<DocumentAssistant>(DOCUMENT_ASSIST, {
    progress: documentProgressStore,
    history: documentHistoryStore,
    earlier,
    async more(turnId) {
      const conversation = selected;
      const turn = turns.find((t) => t.id === turnId);
      const ref = turn?.bodies.find(
        (r) => r.role === "answer" && r.state === "ready",
      );
      if (!ref) return;
      while (selected === conversation && !bodies[ref.body_id]?.complete) {
        const oldEnd = bodies[ref.body_id]?.end ?? 0;
        await loadBody(turnId, ref);
        if ((bodies[ref.body_id]?.end ?? 0) <= oldEnd) break;
      }
    },
    outcome(requestId, outcome) {
      documentOutcomes = { ...documentOutcomes, [requestId]: outcome };
    },
    register(reference, open) {
      documentOpeners.set(reference.session_id, open);
      return () => {
        documentOpeners.delete(reference.session_id);
      };
    },
    available: documentAvailable,
    pending: documentPending,
    async resolve() {
      const page = workspacePage,
        course = workspaceCourse;
      await resolve();
      if (page !== "journal") await openWorkspace(page, course);
    },
    async ask(text, signal) {
      if (
        ownerBusy ||
        documentEditing ||
        !loaded ||
        selectedConversation?.imported
      )
        throw new Error(
          "Finish the current action before asking Kejtu to edit.",
        );
      if (signal.aborted)
        throw new Error("Stopped. Your document is unchanged.");
      documentEditing = true;
      try {
        const accepted = await send(text, true);
        if (!accepted)
          throw new Error(
            error ||
              "The request was not confirmed. Resolve the pending request before sending again.",
          );
        return await documentAnswer(accepted, signal);
      } finally {
        documentEditing = false;
      }
    },
  });
  $: documentHistoryStore.set({
    conversationId: selected,
    entries: documentHistory(turns, bodies, documentOutcomes),
    canEarlier: !!before,
    loading,
  });
  $: documentPending.set(uncertain);
  $: documentAvailable.set(
    state === "ready" &&
      loaded &&
      !ownerBusy &&
      !documentEditing &&
      !selectedConversation?.imported,
  );

  import {
    appendPage,
    savedBody,
    type Stream,
  } from "$lib/features/chat/stream";
  import { onMount, tick, setContext } from "svelte";
  import {
    createWorkspace,
    workspaceRequest,
    type View as WorkspacePage,
  } from "$lib/features/workspace/workspace";
  import type { StudyReference } from "$lib/features/chat/study-requests";
  import type { MaterialPage } from "$lib/features/workspace/material-reader";
  import WorkspaceView from "$lib/features/workspace/WorkspaceView.svelte";
  import WorkspaceSettings from "$lib/features/workspace/WorkspaceSettings.svelte";
  import WorkspaceNotice from "$lib/features/workspace/WorkspaceNotice.svelte";
  let materialSidebar: HTMLDivElement;
  let workspaceView: WorkspaceView;
  let readingMaterial = false;
  let wideReading = false;
  let sourceReturn: { course: string; source: Source } | null = null;
  let studySourceOpening = false;
  async function openStudySource(reference: StudyReference) {
    if (ownerBusy || studySourceOpening)
      throw new Error("Finish the current action before opening this source.");
    if (
      !$workspace.snapshot?.settings.university_enabled ||
      !$workspace.snapshot.courses.some(
        (course) => course.id === reference.courseId && !course.cancelled,
      )
    )
      throw new Error(
        "This course is unavailable. Check Projects and its current access settings.",
      );
    const conversation = selected,
      openedGeneration = generation;
    studySourceOpening = true;
    try {
      // A displayed link carries no authority. Resolve the retained artifact through
      // the existing permission-checked owner before navigating; never open its path.
      const result = await workspaceRequest<MaterialPage>(
        "materials.snapshot",
        {
          course_id: reference.courseId,
          artifact_id: reference.artifactId,
          offset: 0,
          maximum: 16384,
        },
      );
      if (
        selected !== conversation ||
        generation !== openedGeneration ||
        ownerBusy
      )
        throw new Error(
          "The conversation changed. Open this source again when ready.",
        );
      const source = result.source;
      if (
        !source ||
        source.artifact_id !== reference.artifactId ||
        source.path !== reference.path ||
        source.page !== reference.page ||
        !/^[0-9a-f]{64}$/.test(source.sha256)
      )
        throw new Error("This source does not match the saved request.");
      sourceReturn = { course: reference.courseId, source };
      await openWorkspace("course", reference.courseId);
      workspaceView?.openMaterial(reference.courseId, source);
    } finally {
      studySourceOpening = false;
    }
  }
  async function returnToSource() {
    if (ownerBusy || !sourceReturn) return;
    await openWorkspace("course", sourceReturn.course);
    workspaceView?.openMaterial(sourceReturn.course, sourceReturn.source);
  }
  import Tasks from "$lib/features/tasks/Tasks.svelte";
  import Research from "$lib/features/tasks/Research.svelte";
  import Home from "$lib/features/workspace/Home.svelte";
  import Welcome from "$lib/features/help/Welcome.svelte";
  import type { OnboardingStatus } from "$lib/api/wire.generated";
  let onboarding: OnboardingStatus | null = null;
  let onboardingBusy = false;
  let onboardingUncertain = false;
  let onboardingError = "";
  async function refreshOnboarding() {
    try {
      onboarding = await request("onboarding.read", {});
      onboardingUncertain = false;
      onboardingError = "";
    } catch (caught) {
      onboardingError =
        caught instanceof Error ? caught.message : String(caught);
    }
  }
  async function changeOnboarding(
    action: "start" | "complete" | "skip",
    conversationId: string | null = null,
  ) {
    if (!onboarding || onboardingBusy || onboardingUncertain) return false;
    onboardingBusy = true;
    onboardingError = "";
    try {
      onboarding = await request("onboarding.change", {
        action,
        conversation_id: conversationId,
        expected_revision: onboarding.revision,
      });
      return true;
    } catch (caught) {
      // A committed save may have lost its acknowledgement. Read back explicitly;
      // never replay a start or finish, or allow a send under unknown guidance.
      onboardingUncertain = true;
      onboardingError =
        caught instanceof Error ? caught.message : String(caught);
      return false;
    } finally {
      onboardingBusy = false;
    }
  }
  async function startIntroduction() {
    if (
      ownerBusy ||
      draft.trim() ||
      !onboarding ||
      onboarding.status !== "available"
    )
      return;
    await createConversation();
    sending = true;
    try {
      createKey ??= identity();
      const created = await request("conversation.create", {
        mutation_key: createKey,
      });
      selected = created.conversation_id;
      createKey = null;
      if (await changeOnboarding("start", selected)) {
        // Prepare, never send. The user's click on Send owns the model request.
        draft =
          "Help me get started with Kejtu. Let’s find one useful thing to do together.";
        await listConversations();
      }
    } catch (caught) {
      uncertain = true;
      failure(caught);
    } finally {
      sending = false;
    }
  }
  async function continueIntroduction() {
    if (ownerBusy || draft.trim() || !onboarding?.conversation_id) return;
    await openWorkspace("journal");
    await openConversation(onboarding.conversation_id);
  }
  let showHelp = 0;
  function openHelp() {
    if (navigationLocked || loading) return;
    showHelp += 1;
    showWorkspaceSettings = 0;
    notebookOpen = false;
    inspectTurnId = "";
    showOperations();
  }
  import StudyNav from "$lib/features/workspace/StudyNav.svelte";
  import WidgetEditor from "$lib/features/workspace/WidgetEditor.svelte";
  import type { Source, Widget } from "$lib/features/workspace/workspace";
  import WidgetCard from "$lib/features/workspace/WidgetCard.svelte";
  import "$lib/features/workspace/workspace.css";
  import { workspaceNavigationLocked } from "$lib/features/workspace/workspace";
  const workspace = createWorkspace();
  let workspacePage: WorkspacePage = "journal";
  let workspaceCourse = "",
    personalTaskLock = false,
    showWorkspaceSettings = 0,
    tasksVisited = false,
    researchVisited = false,
    showAccess = 0,
    personalTaskScope = "",
    taskListVisit = 0,
    marginOpen = true;
  let widgetEditor: { side?: Widget["side"]; id?: string } | null = null;
  let compactMargins =
    typeof window !== "undefined" && window.innerWidth <= 1000;
  function arrangeWidgets(side?: Widget["side"], id?: string) {
    if (ownerBusy || !$workspace.snapshot) return;
    widgetEditor = { side, id };
  }
  let journalScroll = 0;
  let studyScroll = 0;
  let lastStudyPage: WorkspacePage = "semesters";
  let journalFocus: HTMLElement | null = null;
  let workspaceSurface: HTMLElement | undefined;
  async function openWorkspace(next: WorkspacePage, id = "") {
    if (navigationLocked || loading || uncertain || sending || documentEditing)
      return;
    // The main destinations share one lazily mounted controller each. Project
    // shortcuts set a list scope without replacing either task or chat drafts.
    if (next === "tasks") {
      personalTaskScope = id;
      tasksVisited = true;
    }
    if (next === "research") researchVisited = true;
    if (next === "workspace-settings") {
      showWorkspaceSettings++;
      showOperations();
      return;
    }
    showWorkspaceSettings = 0;
    if (next === workspacePage && (!id || id === workspaceCourse)) {
      view = "chat";
      indexOpen = false;
      return;
    }
    const returningToStudy =
      workspacePage === "journal" &&
      next === lastStudyPage &&
      (!id || id === workspaceCourse);
    if (
      workspacePage !== "journal" &&
      workspacePage !== "home" &&
      next === "journal"
    )
      studyScroll = scrollElement?.scrollTop || 0;
    if (workspacePage === "journal" && next !== "journal") {
      transcript?.remember();
      journalScroll = scrollElement?.scrollTop || 0;
      const focused = document.activeElement;
      if (
        focused instanceof HTMLElement &&
        (focused === scrollElement || focused.closest(".kept-chat"))
      )
        journalFocus = focused;
    }
    workspacePage = next;
    if (next === "semesters" || next === "course") lastStudyPage = next;
    if (id && (next === "course" || next === "semesters")) workspaceCourse = id;
    view = "chat";
    indexOpen = false;
    await tick();
    if (scrollElement)
      scrollElement.scrollTop =
        next === "journal" ? journalScroll : returningToStudy ? studyScroll : 0;
    if (next === "journal") {
      if (journalFocus?.isConnected)
        journalFocus.focus({ preventScroll: true });
    } else if (next === "home") scrollElement?.focus({ preventScroll: true });
    else workspaceSurface?.focus({ preventScroll: true });
  }
  function discussMaterial(text: string, source?: Source) {
    if (ownerBusy) return;
    draft = draft.trim() ? `${draft}\n\n${text}` : text;
    if (source) sourceReturn = { course: workspaceCourse, source };
    void openWorkspace("journal");
  }

  import {
    identity,
    reconcilePending,
    recordRendered,
    presentWindow,
    request,
  } from "$lib/api/client";
  import "$lib/design/tokens.css";
  import "$lib/design/chat.css";
  import Notebook from "$lib/features/notebook/Notebook.svelte";
  import AppShell from "$lib/features/chat/AppShell.svelte";
  import Latest from "$lib/features/chat/Latest.svelte";
  import {
    readBookmark,
    writeBookmark,
    type ReadingPosition,
  } from "$lib/features/chat/reading-position";
  import JournalIndex from "$lib/features/chat/JournalIndex.svelte";
  import Operations from "$lib/features/chat/Operations.svelte";
  import Transcript from "$lib/features/chat/Transcript.svelte";
  import { visibleConversationLabel } from "$lib/features/chat/conversation-label";
  import { ActivityBridge } from "$lib/features/background/activity";
  import type {
    Arguments,
    BodyRef,
    Conversation,
    Inspection,
    Profile,
    Results,
    Turn,
  } from "$lib/api/wire.generated";
  let imageViewerOpen = false;

  import type {
    VisibleBody as Body,
    ImageReader,
  } from "$lib/features/chat/images";

  /**
   * Transport and state coordinator.
   *
   * Every native request, generation token, poll, lock and recovery path stays
   * here; the chat components are presentation only. Keeping request ownership
   * in one place is what lets the later stream assignment change how a reply is
   * drawn without moving transport.
   */
  let state: "loading" | "ready" | "unavailable" = "loading";
  let error = "";
  let notice = "";
  let exchangeNotice = "";
  let exchangeNoticeTimer: ReturnType<typeof setTimeout>;
  function showExchangeNotice(message: string) {
    clearTimeout(exchangeNoticeTimer);
    exchangeNotice = message;
    exchangeNoticeTimer = setTimeout(() => (exchangeNotice = ""), 4000);
  }
  let identityLine = "";
  let capabilities = "";
  let conversations: Conversation[] = [];
  let conversationCursor: string | null = null;
  let listing = false;
  let selected = "";
  let draftCreatedAt = new Date().toISOString();
  let turns: Turn[] = [];
  const turnPageSize = 20;
  let before: string | null = null;
  let loading = false;
  let loaded = false;
  let generation = 0;
  let bodies: Record<string, Body> = {};
  let bodyLoading: Record<string, boolean> = {};
  let inspections: Record<
    string,
    { items: Inspection[]; next_cursor: string | null }
  > = {};
  let draft = "";
  let sending = false;
  let uncertain = false;
  let intent: Arguments["turn.submit"] | null = null;
  let createKey: string | null = null;
  let exchangeIntent: Arguments["exchange.action"] | null = null;
  let active: string | null = null;
  let stoppingTurn: string | null = null;
  $: if (stoppingTurn && stoppingTurn !== active) stoppingTurn = null;
  let stream: Stream | null = null;
  let streamingEnabled = false;
  let streamPaused = false;
  let statusAt = 0;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let destroyed = false;
  let polling = false;
  let notebookOpen = false;
  let notebookLock = false;
  let accessLock = false;
  let recoverAccess = false;
  let reminderLock = false;
  let peopleLock = false;
  let webLock = false;
  let recoverWeb = false;
  let modelsBusy = false;
  /** Local layout preference; it never changes model or archive settings. */
  let quiet = false;
  let indexOpen = false;
  let scrollElement: HTMLElement | undefined;
  let transcript: Transcript | undefined;
  let following = true;
  const readingPositions = new Map<string, ReadingPosition>();
  function rememberReading(id: string, position: ReadingPosition) {
    if (id === selected && loaded && !loading) {
      const anchor = turns.find((turn) => turn.id === position.anchorId);
      position.bodyEnds = Object.fromEntries(
        (anchor?.bodies ?? []).map((ref) => [
          ref.body_id,
          bodies[ref.body_id]?.end ?? 0,
        ]),
      );
      writeBookmark(stateId, { conversationId: id, position });
    }
  }
  function rememberBeforeClosing() {
    transcript?.remember();
  }
  function toggleQuiet() {
    transcript?.remember();
    quiet = !quiet;
    indexOpen = false;
    try {
      localStorage.setItem("kejtu-next:journal:quiet", String(quiet));
    } catch {
      /* Layout remains usable without browser storage. */
    }
  }
  $: editorLocked =
    onboardingBusy ||
    onboardingUncertain ||
    notebookLock ||
    accessLock ||
    reminderLock ||
    peopleLock ||
    webLock ||
    modelsBusy ||
    researchLock ||
    backgroundLock ||
    personalTaskLock;
  $: controlsLocked = editorLocked || $workspace.busy || !!$workspace.pending;
  $: navigationLocked = editorLocked || workspaceNavigationLocked($workspace);
  let stateId = "";
  let researchNew = 0,
    researchLock = false,
    backgroundLock = false;
  import {
    unread,
    type ResearchTarget,
    type FindingView,
  } from "../tasks/research-view";
  let researchTarget: ResearchTarget | null = null;
  let researchVersion = 0;
  let researchReading = false;
  let researchRefreshRequested = false;
  let workspaceLastActive: string | null | undefined;
  $: if (state === "ready" && active !== workspaceLastActive) {
    const finished = workspaceLastActive != null && active === null;
    workspaceLastActive = active;
    if (finished) {
      void workspace.refresh().catch(failure);
      void refreshResearch(true);
    }
  }
  async function refreshResearch(invalidate = false) {
    if (state !== "ready" || destroyed) return;
    // Periodic polling must not invalidate a slow read over and over. Only an
    // observed change or returning to the app requires an immediate follow-up.
    if (researchReading && !invalidate) return;
    researchRefreshRequested = true;
    if (researchReading) return;
    researchReading = true;
    try {
      // A review can commit while an older read is in flight. Queue one fresh
      // read and discard that stale response instead of waiting for the timer.
      while (researchRefreshRequested && !destroyed) {
        researchRefreshRequested = false;
        try {
          const overview = await request("research.overview", { limit: 1 });
          if (destroyed || researchRefreshRequested) continue;
          researchNew = unread(overview.counts);
          // A run can change status without changing the unread count.
          researchVersion++;
        } catch {
          /* Keep the last known marker; Research shows explicit read errors. */
        }
      }
    } finally {
      researchReading = false;
    }
  }
  function refreshOnReturn() {
    schedulePoll();
    if (document.visibilityState === "visible") void refreshResearch(true);
  }
  function openResearch(jobId?: string, view?: FindingView) {
    if (navigationLocked || loading) return;
    researchTarget = { jobId, view, token: (researchTarget?.token ?? 0) + 1 };
    void openWorkspace("research");
  }
  function discussResearch(text: string) {
    if (ownerBusy) return;
    draft = draft.trim() ? `${draft}\n\n${text}` : text;
    showChat();
  }
  /** Settings and main destinations retain their mounted controllers. */
  let view: "chat" | "operations" = "chat";
  let inspectTurnId = "";
  let launchMessageBytes: number | null = null;
  let conversationMessageBytes: number | null = null;
  let launchProfile: Profile | null = null;
  let activity: ActivityBridge | null = null;
  let activityError = "";

  // While a lock is set, the view that owns the pending change cannot be left:
  // ReminderAccess clears its own lock in onDestroy, so unmounting it mid-change
  // would silently unlock sending. The same holds for Notebook's removal lock.
  $: operationsOpen = view === "operations";
  $: ownerBusy =
    sending ||
    uncertain ||
    loading ||
    !!active ||
    controlsLocked ||
    documentEditing;
  /** Saved metadata owns names; transcript reads never rename a conversation. */
  $: selectedConversation =
    conversations.find((item) => item.conversation_id === selected) ?? null;
  $: currentLabel = selectedConversation
    ? visibleConversationLabel(selectedConversation.created_at)
    : "";
  function toggleIndex() {
    transcript?.remember();
    if (compactMargins) indexOpen = !indexOpen;
    else toggleQuiet();
  }

  $: activeTitle = selectedConversation?.title ?? currentLabel;
  $: indexConversations = conversations.map((item) => ({
    id: item.conversation_id,
    label:
      item.title ??
      `${visibleConversationLabel(item.created_at)} · ${item.conversation_id.slice(0, 8)}`,
    createdAt: item.created_at,
  }));

  let titleTimer: ReturnType<typeof setTimeout> | undefined;
  function scheduleTitle(delay = 2500) {
    clearTimeout(titleTimer);
    if (!destroyed && selected) titleTimer = setTimeout(refreshTitle, delay);
  }
  async function refreshTitle() {
    const cid = selected,
      token = generation;
    if (destroyed || !cid) return;
    if (
      document.visibilityState !== "visible" ||
      draft.trim() ||
      ownerBusy ||
      view !== "chat"
    ) {
      scheduleTitle();
      return;
    }
    try {
      const result = await request("conversation.title", {
        conversation_id: cid,
      });
      if (destroyed || token !== generation || result.conversation_id !== cid)
        return;
      // Updating a name in place must never reorder rows or rebuild the page.
      conversations = conversations.map((item) =>
        item.conversation_id === cid ? { ...item, title: result.title } : item,
      );
      if (result.status === "waiting" || result.status === "running")
        scheduleTitle();
    } catch {
      // The durable first-question label remains usable when titling fails.
    }
  }
  $: inspection = inspectTurnId ? (inspections[inspectTurnId] ?? null) : null;
  $: activityContext = {
    conversation_id: selected || null,
    draft: draft.length > 0 || $readerDrafts,
    dialog:
      imageViewerOpen ||
      !!widgetEditor ||
      operationsOpen ||
      workspacePage === "research" ||
      workspacePage === "tasks" ||
      controlsLocked ||
      sending ||
      uncertain ||
      loading ||
      state !== "ready",
  };
  $: if (activity && activityContext) activity.changed();

  function failure(caught: unknown) {
    error = caught instanceof Error ? caught.message : String(caught);
  }

  /** The topbar's Settings toggle. Same surface the chat used to call Operations. */
  $: settingsOpen = operationsOpen;

  /**
   * Settings toggle, and the only way out of the secondary surface.
   *
   * It carries the same lock the old "Back to chat" control did: while a permission
   * change has no confirmed outcome the view that owns it must stay mounted, or its
   * onDestroy would silently clear the lock and sending would resume unpaused.
   */
  function toggleSettings() {
    if (operationsOpen) {
      if (!navigationLocked) view = "chat";
    } else {
      showWorkspaceSettings = 0;
      showOperations();
    }
  }

  function showOperations() {
    if (navigationLocked || loading) return;
    // Capture before the shared scroll element is hidden and loses its geometry.
    transcript?.remember();
    view = "operations";
  }

  function showChat() {
    // Blocked while a lock is set so a pending change is never unmounted away:
    // ReminderAccess clears its own lock in onDestroy, and Notebook does the
    // same for a note removal.
    if (navigationLocked) return;
    view = "chat";
    void openWorkspace("journal");
  }

  async function listConversations(append = false) {
    if (listing) return;
    listing = true;
    try {
      const page = await request("conversations.list", {
        cursor: append ? conversationCursor : null,
      });
      conversations = append ? [...conversations, ...page.items] : page.items;
      conversationCursor = page.next_cursor;
    } finally {
      listing = false;
    }
  }

  async function loadBody(turnId: string, ref: BodyRef, token = generation) {
    if (bodyLoading[ref.body_id]) return;
    bodyLoading = { ...bodyLoading, [ref.body_id]: true };
    const previous = bodies[ref.body_id];
    const offset = previous?.end ?? 0;
    const scope = selected;
    try {
      const args = {
        conversation_id: scope,
        turn_id: turnId,
        body_id: ref.body_id,
        offset,
        maximum_bytes: 16384,
      };
      let part: Body;
      try {
        part =
          ref.role === "user" && (!previous || previous.images)
            ? await request("body.view", args)
            : await request("body.read", args);
      } catch (caught) {
        if (
          !(caught instanceof Error) ||
          !("code" in caught) ||
          caught.code !== "image_view_size"
        )
          throw caught;
        notice = caught.message;
        part = await request("body.read", args);
      }
      if (token !== generation || destroyed) return;
      if (
        part.body_id !== ref.body_id ||
        part.offset !== offset ||
        part.end < offset ||
        part.end > part.total_bytes ||
        part.complete !== (part.end === part.total_bytes) ||
        (!part.complete && part.end === offset) ||
        new TextEncoder().encode(part.content).length !== part.end - offset ||
        (previous &&
          (previous.sha256 !== part.sha256 ||
            previous.total_bytes !== part.total_bytes ||
            previous.source_sha256 !== part.source_sha256))
      ) {
        throw new Error("Body page identity or extent changed.");
      }
      bodies = {
        ...bodies,
        [ref.body_id]: {
          ...part,
          content: (previous?.content ?? "") + part.content,
        },
      };
    } catch (caught) {
      if (token === generation) failure(caught);
    } finally {
      if (token === generation)
        bodyLoading = { ...bodyLoading, [ref.body_id]: false };
    }
  }

  const loadImage: ImageReader = async (turnId, bodyId, source, image) => {
    const scope = selected,
      token = generation;
    const result = await request("body.image", {
      conversation_id: scope,
      turn_id: turnId,
      body_id: bodyId,
      index: image.index,
      source_sha256: source,
    });
    if (destroyed || token !== generation || scope !== selected)
      throw new Error("This conversation is no longer open.");
    if (
      result.body_id !== bodyId ||
      result.source_sha256 !== source ||
      ["index", "sha256", "width", "height", "bytes", "mime_type"].some(
        (key) =>
          result[key as keyof typeof result] !==
          image[key as keyof typeof image],
      )
    )
      throw new Error("Image identity changed. Reopen the conversation.");
    return `data:${result.mime_type};base64,${result.data}`;
  };

  async function initialBodies(
    items: Turn[],
    token: number,
    keepLoaded = false,
  ) {
    // One bounded extent per visible body. Continuation always requires an
    // explicit user action; opening a page never drains a whole large answer.
    for (const turn of items)
      for (const ref of turn.bodies) {
        if (token !== generation || destroyed) return;
        // Submitting another exchange must not reread or extend an already
        // visible body: partial-page continuation still belongs to the reader.
        if (keepLoaded && bodies[ref.body_id]) continue;
        await loadBody(turn.id, ref, token);
      }
  }

  async function openConversation(
    id: string,
    submittedText?: string,
    removedTurnId?: string,
  ) {
    if (navigationLocked) return;
    const keepTranscript =
      submittedText !== undefined && id === selected && loaded;
    transcript?.remember();
    indexOpen = false;
    notebookOpen = false;
    clearTimeout(titleTimer);
    const token = keepTranscript ? generation : ++generation;
    if (selected !== id) selectedSkill = null;
    selected = id;
    if (!keepTranscript) {
      conversationMessageBytes = null;
      loading = true;
      loaded = false;
      turns = [];
      bodies = {};
      bodyLoading = {};
      inspections = {};
      before = null;
      inspectTurnId = "";
    }
    stream = null;
    streamPaused = false;
    statusAt = 0;
    error = "";
    try {
      const page = await request("turns.page", {
        conversation_id: id,
        page_size: turnPageSize,
      });
      if (token !== generation || destroyed) return;
      conversationMessageBytes = page.maximum_message_bytes ?? null;
      const saved = !keepTranscript ? readingPositions.get(id) : undefined;
      if (saved && !saved.following && saved.anchorSequence) {
        // Restore only as far as the entry the reader actually left. Compare
        // sequences as integers, not floating point or timestamps. A deleted
        // anchor cannot make us drain the rest of the conversation indefinitely.
        const anchor = BigInt(saved.anchorSequence);
        const cursors = new Set<string>();
        while (
          page.items.length &&
          BigInt(page.items[0].sequence) > anchor &&
          page.next_cursor
        ) {
          const cursor = page.next_cursor;
          if (cursors.has(cursor))
            throw new Error(
              "Earlier messages could not be restored. Please reopen the conversation.",
            );
          cursors.add(cursor);
          const older = await request("turns.page", {
            conversation_id: id,
            before: cursor,
            page_size: turnPageSize,
          });
          if (token !== generation || destroyed) return;
          if (
            older.items.length &&
            BigInt(older.items.at(-1)!.sequence) >=
              BigInt(page.items[0].sequence)
          )
            throw new Error(
              "Earlier message order changed. Please reopen the conversation.",
            );
          page.items = [...older.items, ...page.items];
          page.next_cursor = older.items.length ? older.next_cursor : null;
        }
        if (!page.items.some((turn) => turn.id === saved.anchorId)) {
          readingPositions.delete(id);
          writeBookmark(stateId, null);
          // A deliberate deletion already has its own confirmation. Losing that
          // exact reading anchor is expected, not another persistent warning.
          if (saved.anchorId !== removedTurnId)
            notice =
              "The saved reading position is no longer available. Showing the retained messages.";
        }
      }
      // Navigation restores a position only after every initial body extent is
      // available. Publishing the page first makes history look like live growth
      // and starts a long follow animation through the selected conversation.
      await initialBodies(page.items, token, keepTranscript);
      if (token !== generation || destroyed) return;
      const anchorTurn =
        saved && page.items.find((turn) => turn.id === saved.anchorId);
      // Restore only the extra text the reader had explicitly opened in this
      // anchored entry. Other large bodies keep their ordinary first extent.
      for (const ref of anchorTurn?.bodies ?? []) {
        const end = saved?.bodyEnds?.[ref.body_id] ?? 0;
        while (
          bodies[ref.body_id] &&
          !bodies[ref.body_id].complete &&
          bodies[ref.body_id].end < end
        ) {
          const previous = bodies[ref.body_id].end;
          await loadBody(anchorTurn!.id, ref, token);
          if (token !== generation || destroyed) return;
          if ((bodies[ref.body_id]?.end ?? 0) <= previous) break;
        }
      }
      if (keepTranscript) {
        // Keep the mounted reader and its earlier pages while the new exchange
        // loads. Clear the acknowledged draft in the same render as its question,
        // avoiding an empty/shrunken transcript between the two states.
        const current = new Map(page.items.map((turn) => [turn.id, turn]));
        const existing = new Set(turns.map((turn) => turn.id));
        if (!turns.length) before = page.next_cursor;
        turns = [
          ...turns.map((turn) => current.get(turn.id) ?? turn),
          ...page.items.filter((turn) => !existing.has(turn.id)),
        ];
      } else {
        turns = page.items;
        before = page.next_cursor;
      }
      if (submittedText !== undefined && draft === submittedText) draft = "";
      // The native page cursor is authoritative about retained earlier entries.
      loaded = true;
      if (!keepTranscript && !readingPositions.has(id))
        writeBookmark(stateId, {
          conversationId: id,
          position: {
            top: 0,
            following: true,
            anchorId: "",
            anchorOffset: 0,
          },
        });
      // Read the freshly loaded page for running work in this conversation.
      const running = turns.find((turn) => turn.status === "running");
      if (running) {
        active = running.id;
        schedulePoll();
      } else if (active && turns.some((turn) => turn.id === active)) {
        // The turn being tracked is on this page and is no longer running, so it
        // has finished. Clearing it here stops a submit that returned "running"
        // from leaving a stale Stop button and a blocked composer behind.
        active = null;
      }
      // Otherwise the tracked turn belongs to a different conversation. `active`
      // is the global owned-turn tracker, and this page is not authoritative
      // about it, so cross-conversation ownership is retained until that turn's
      // terminal outcome is actually known.
    } catch (caught) {
      if (token === generation) failure(caught);
    } finally {
      if (token === generation) {
        loading = false;
        scheduleTitle(0);
      }
    }
  }

  async function earlier() {
    if (!before || loading) return;
    transcript?.holdReadingPosition();
    const token = generation;
    loading = true;
    try {
      const page = await request("turns.page", {
        conversation_id: selected,
        before,
        page_size: turnPageSize,
      });
      if (token !== generation || destroyed) return;
      await initialBodies(page.items, token);
      if (token !== generation || destroyed) return;
      // Publish the complete first body extents together. The reader can retain
      // its anchor without repeatedly jumping as newly prepended bodies grow.
      turns = [...page.items, ...turns];
      before = page.next_cursor;
    } catch (caught) {
      if (token === generation) failure(caught);
    } finally {
      if (token === generation) loading = false;
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (
      !destroyed &&
      active &&
      !streamPaused &&
      document.visibilityState === "visible"
    )
      pollTimer = setTimeout(
        poll,
        streamingEnabled && turns.some((turn) => turn.id === active) ? 60 : 500,
      );
  }

  async function poll() {
    if (
      !active ||
      polling ||
      destroyed ||
      document.visibilityState !== "visible"
    )
      return;
    polling = true;
    const id = active;
    const token = generation;
    try {
      const displayed = turns.some((turn) => turn.id === id);
      if (streamingEnabled && displayed) {
        const previous = stream?.turn_id === id ? stream : null;
        const page = await request("turn.stream", {
          turn_id: id,
          epoch: previous?.epoch ?? null,
          offset: previous?.end ?? 0,
          maximum_bytes: 32768,
        });
        if (destroyed || active !== id || token !== generation) return;
        if (page.conversation_id !== selected)
          throw new Error("Progress belongs to another conversation.");
        const next = appendPage(previous, page, id);
        // Stats/phase-only updates leave the exact text prop unchanged.
        stream = next;
        if (next.status === "running" && Date.now() - statusAt < 500) return;
        if (next.status !== "running" && !next.caught_up) return;
      }
      const current = await request("turn.status", { turn_id: id });
      if (destroyed || active !== id) return;
      if (token !== generation && current.conversation_id === selected) return;
      statusAt = Date.now();
      streamPaused = false;
      if (token === generation && current.conversation_id === selected) {
        if (
          current.status !== "running" &&
          streamingEnabled &&
          stream?.status === "running"
        )
          return;
        if (current.status !== "running" && stream?.turn_id === id) {
          const body = await savedBody(stream, current);
          if (destroyed || token !== generation || active !== id) return;
          if (body) bodies = { ...bodies, [body.body_id]: body };
          stream = null;
        }
        turns = turns.map((turn) => (turn.id === id ? current : turn));
        if (current.status !== "running") {
          for (const ref of current.bodies)
            if (!bodies[ref.body_id]) await loadBody(id, ref, token);
        }
      }
      if (current.status !== "running") {
        active = null;
        scheduleTitle(0);
      }
    } catch (caught) {
      if (destroyed || active !== id || token !== generation) return;
      failure(caught);
      // Preserve foreground ownership while progress is unavailable. Refresh
      // reconnects read-only; Stop stays reachable and Send cannot race the turn.
      if (streamingEnabled) streamPaused = true;
      else active = null;
    } finally {
      polling = false;
      schedulePoll();
    }
  }

  async function inspect(id: string, more = false) {
    const token = generation;
    try {
      const page = await request("turn.inspect", {
        turn_id: id,
        cursor: more ? inspections[id]?.next_cursor : null,
      });
      if (token !== generation || destroyed) return;
      inspections = {
        ...inspections,
        [id]: {
          ...page,
          items: more ? [...inspections[id].items, ...page.items] : page.items,
        },
      };
    } catch (caught) {
      if (token === generation) failure(caught);
    }
  }

  /** Inspection detail lives in the secondary view; this is its entry point. */
  async function openInspection(id: string) {
    if (controlsLocked) return;
    inspectTurnId = id;
    await inspect(id);
    if (!destroyed) view = "operations";
  }

  async function cancel() {
    if (!active || stoppingTurn === active) return;
    const id = active;
    stoppingTurn = id;
    try {
      await request("turn.cancel", { turn_id: id });
      // Acknowledgement is only a request. Keep the activity row and submission
      // guard until polling observes the saved terminal state.
      await poll();
    } catch (caught) {
      if (stoppingTurn === id) stoppingTurn = null;
      failure(caught);
    }
  }

  async function exchangeAction(
    turnId: string,
    action: "save_memory" | "exclude" | "delete",
    text: string,
  ) {
    if (ownerBusy || !selected) return;
    sending = true;
    error = "";
    exchangeIntent = {
      conversation_id: selected,
      turn_id: turnId,
      action,
      text,
      mutation_key: identity(),
    };
    try {
      const result = await request("exchange.action", exchangeIntent);
      exchangeIntent = null;
      showExchangeNotice(
        result.action === "save_memory"
          ? "Memory saved."
          : result.action === "delete"
            ? "Exchange deleted."
            : "Excluded from future recall. The transcript stays here.",
      );
      await listConversations();
      await openConversation(
        result.conversation_id,
        undefined,
        action === "delete" ? turnId : undefined,
      );
    } catch (caught) {
      failure(caught);
      // Even an application failure can follow a durable commit. The native
      // journal resolves the same intent; never submit another copy automatically.
      uncertain = true;
      try {
        let result = await reconcilePending();
        if (result.status === "none" && exchangeIntent)
          result = await request("mutation.lookup", {
            kind: "exchange.action",
            mutation_key: exchangeIntent.mutation_key,
          });
        if (result.status === "absent") {
          uncertain = false;
          exchangeIntent = null;
        } else if (
          result.status === "accepted" &&
          result.kind === "exchange.action"
        ) {
          uncertain = false;
          exchangeIntent = null;
          showExchangeNotice("Saved change recovered. Nothing was repeated.");
          await listConversations();
          await openConversation(
            result.conversation_id,
            undefined,
            action === "delete" ? turnId : undefined,
          );
          return;
        }
      } catch {
        /* Keep the pending intent and composer lock for explicit recovery. */
      }
      throw caught;
    } finally {
      sending = false;
    }
  }

  async function resolve() {
    error = "";
    try {
      let result = await reconcilePending();
      if (result.status === "none" && exchangeIntent) {
        result = await request("mutation.lookup", {
          kind: "exchange.action",
          mutation_key: exchangeIntent.mutation_key,
        });
      }
      if (result.status === "none" && (intent || createKey)) {
        result = await request("mutation.lookup", {
          kind: intent ? "turn.submit" : "conversation.create",
          mutation_key: intent?.mutation_key ?? createKey!,
        });
      }
      uncertain = false;
      if (result.status === "accepted" || result.status === "removed") {
        exchangeIntent = null;
        notice =
          result.status === "removed"
            ? "This exchange was already deleted. Nothing was resubmitted."
            : result.kind === "exchange.action"
              ? "Saved exchange action recovered. Nothing was repeated."
              : result.kind === "note.remove"
                ? "Saved note removal confirmed. No operation was repeated."
                : "Saved acceptance recovered. No message was resubmitted.";
        // A creation receipt acknowledges no message text. Clear only the
        // exact draft whose submission was established by this receipt.
        if (
          result.kind === "turn.submit" &&
          intent &&
          result.mutation_key === intent.mutation_key &&
          result.conversation_id === intent.conversation_id
        ) {
          if (draft === intent.text) draft = "";
          intent = null;
        }
        if (
          result.kind === "conversation.create" &&
          result.mutation_key === createKey
        )
          createKey = null;
        await listConversations();
        // Recovery may reveal a note removal, which belongs to the chat view.
        // This is still a view change, so it respects the same lock rule.
        showChat();
        await openConversation(result.conversation_id);
        if (result.kind === "note.remove") {
          notebookOpen = true;
          view = "operations";
        }
      } else {
        notice =
          "No recorded acceptance. Any remaining draft can be sent explicitly; drafts are not recovered after closing.";
      }
    } catch (caught) {
      uncertain = true;
      failure(caught);
    }
  }

  async function createConversation() {
    if (sending || uncertain || controlsLocked || active || documentEditing)
      return;
    transcript?.remember();
    clearTimeout(titleTimer);
    generation++;
    selected = "";
    writeBookmark(stateId, null);
    selectedSkill = null;
    draftCreatedAt = new Date().toISOString();
    turns = [];
    bodies = {};
    stream = null;
    streamPaused = false;
    statusAt = 0;
    bodyLoading = {};
    inspections = {};
    before = null;
    inspectTurnId = "";
    notebookOpen = false;
    indexOpen = false;
    loading = false;
    loaded = true;
    error = "";
    notice = "";
    view = "chat";
    workspacePage = "journal";
    await tick();
    transcript?.focusComposer();
  }

  async function send(submittedText = draft, fromDocument = false) {
    if (
      !loaded ||
      loading ||
      active ||
      !submittedText.trim() ||
      (documentEditing && !fromDocument) ||
      sending ||
      uncertain ||
      controlsLocked
    )
      return;
    notebookOpen = false;
    sending = true;
    const beganDraft = !selected;
    const skill = fromDocument ? null : selectedSkill;
    const focusAfterSend =
      document.activeElement?.getAttribute("aria-label") === "Message";
    error = "";
    notice = "";
    try {
      if (!selected) {
        // A blank page is local UI state. Persist only for an explicit first send.
        createKey ??= identity();
        const created = await request("conversation.create", {
          mutation_key: createKey,
        });
        selected = created.conversation_id;
        createKey = null;
      }
      if (
        !intent ||
        intent.text !== submittedText ||
        intent.conversation_id !== selected ||
        JSON.stringify(intent.skill ?? null) !== JSON.stringify(skill)
      ) {
        intent = {
          mutation_key: identity(),
          conversation_id: selected,
          expected_previous_turn_id: turns.at(-1)?.id ?? null,
          text: submittedText,
          ...(skill ? { skill: { ...skill } } : {}),
        };
      }
      const submitted = intent;
      const accepted = await request("turn.submit", submitted);
      intent = null;
      // Clear only the exact text that was submitted. The field stays editable
      // while the acknowledgement is in flight, so a newer thought typed during
      // that window must survive; this is what "cannot submit" being separate
      // from "cannot edit" requires.
      active = accepted.status === "running" ? accepted.turn_id : null;
      if (beganDraft) {
        try {
          await listConversations();
        } catch (caught) {
          failure(caught);
        }
      }
      await openConversation(submitted.conversation_id, submitted.text);
      // Acceptance is already durable even if the read-only refresh failed.
      // Do not leave that accepted draft looking unsent; preserve newer typing.
      if (draft === submitted.text) draft = "";
      schedulePoll();
      return accepted;
    } catch (caught) {
      uncertain = true;
      failure(caught);
    } finally {
      sending = false;
      await tick();
      if (focusAfterSend && document.activeElement === document.body)
        transcript?.focusComposer();
    }
  }

  async function nativeSmoke() {
    const phase = import.meta.env.VITE_R013_SMOKE;
    if (!["1", "reopen", "delay", "note_delay", "note_reopen"].includes(phase))
      return;
    // Explicit synthetic launcher probes use the same UI actions as a user.
    // Normal reopen never sends or replays any work.
    if (phase === "reopen" || phase === "note_reopen") {
      if (!conversations[0])
        throw new Error("Synthetic reopen conversation missing.");
      await openConversation(conversations[0].conversation_id);
    } else {
      await createConversation();
      draft =
        phase === "delay"
          ? "[[delay]] R013 close while running"
          : phase === "note_delay"
            ? "[[note]] R013 synthetic durable note [[after-commit-delay]]"
            : "R013 native WebView smoke 🌿";
      await send();
    }
    if (phase.startsWith("note_")) {
      const id = turns.at(-1)?.id;
      if (!id) throw new Error("Synthetic note turn missing.");
      // The inspector now lives in the secondary view, so the probe opens it.
      inspectTurnId = id;
      view = "operations";
      for (let n = 0; n < 80; n++) {
        await inspect(id);
        if (
          inspections[id]?.items.some(
            (item) =>
              item.kind === "note_outcome" && item.status === "committed",
          )
        )
          break;
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      await tick();
      if (
        !document
          .querySelector('[aria-label="Turn inspection"]')
          ?.textContent?.includes("committed")
      )
        throw new Error(
          "Native smoke did not render a committed note outcome.",
        );
    } else if (phase !== "delay") {
      while (active && !destroyed)
        await new Promise((resolve) => setTimeout(resolve, 100));
      await tick();
      if (
        !document
          .querySelector(".turns")
          ?.textContent?.includes(
            "Development reply: R013 native WebView smoke 🌿",
          )
      )
        throw new Error("Native smoke did not render the exact stored answer.");
    }
    await recordRendered(phase);
  }

  async function modelsChanged() {
    const hello = await request("hello", {});
    launchProfile = hello.profile;
    launchMessageBytes = hello.maximum_message_bytes ?? null;
    streamingEnabled = hello.capabilities.streaming === true;
    identityLine = `${hello.provider_mode} · ${hello.profile.model} · context ${hello.profile.context_limit} / reply ${hello.profile.generation_limit} · state ${hello.state_id} · schema ${hello.store_schema_version} · build ${hello.build_fingerprint.slice(0, 12)}`;
  }

  async function load() {
    try {
      const hello = await request("hello", {});
      identityLine = `${hello.provider_mode} · ${hello.profile.model} · context ${hello.profile.context_limit} / reply ${hello.profile.generation_limit} · state ${hello.state_id} · schema ${hello.store_schema_version} · build ${hello.build_fingerprint.slice(0, 12)}`;
      stateId = hello.state_id;
      await refreshOnboarding();
      const bookmark = readBookmark(stateId);
      if (bookmark)
        readingPositions.set(bookmark.conversationId, bookmark.position);
      recoverWeb =
        localStorage.getItem(`kejtu-next.web-settings.v1.${stateId}`) !== null;
      if (recoverWeb) {
        view = "operations";
        webLock = true;
      }
      recoverAccess =
        localStorage.getItem(`kejtu-next.access-settings.v1.${stateId}`) !==
        null;
      if (recoverAccess) view = "operations";
      launchProfile = hello.profile;
      launchMessageBytes = hello.maximum_message_bytes ?? null;
      streamingEnabled = hello.capabilities.streaming === true;
      capabilities = `New conversations: History ${hello.capabilities.history ? "on" : "off"} · Memory ${hello.capabilities.memory ? "on" : "off"} · Note writes ${hello.capabilities.notes_write ? "on" : "off"}`;
      active = hello.active_turn_id;
      const recovered = await reconcilePending();
      await listConversations();
      if (destroyed) return;
      state = "ready";
      activity = new ActivityBridge(
        hello.process_instance_id,
        () => activityContext,
        (message) => (activityError = message),
      );
      void activity.start();
      if (recovered.status === "accepted" || recovered.status === "removed") {
        notice =
          recovered.status === "removed"
            ? "This exchange was already deleted. Nothing was resubmitted."
            : recovered.kind === "exchange.action"
              ? "Saved exchange action recovered. Nothing was repeated."
              : recovered.kind === "note.remove"
                ? "Previous note removal confirmed. No operation was repeated."
                : "Saved acceptance recovered; no automatic resubmission.";
        await openConversation(recovered.conversation_id);
        if (recovered.kind === "note.remove") {
          notebookOpen = true;
          view = "operations";
        }
      } else if (recovered.status === "absent")
        notice =
          "No recorded acceptance for the previous pending request. Nothing was retried; any unsent text was not recovered.";
      else if (bookmark && !recoverWeb && !recoverAccess) {
        const cursors = new Set<string>();
        while (
          !conversations.some(
            (item) => item.conversation_id === bookmark.conversationId,
          ) &&
          conversationCursor
        ) {
          if (cursors.has(conversationCursor)) break;
          cursors.add(conversationCursor);
          await listConversations(true);
          if (destroyed) return;
        }
        if (
          conversations.some(
            (item) => item.conversation_id === bookmark.conversationId,
          )
        )
          await openConversation(bookmark.conversationId);
        else writeBookmark(stateId, null);
      }
      if (!selected) loaded = true;
      schedulePoll();
      await tick();
      await recordRendered();
      await nativeSmoke();
    } catch (caught) {
      state = "unavailable";
      failure(caught);
    } finally {
      await tick();
      await presentWindow().catch(failure);
    }
  }

  onMount(() => {
    try {
      quiet = localStorage.getItem("kejtu-next:journal:quiet") === "true";
    } catch {
      /* Optional layout preference. */
    }
    void load().then(async () => {
      if (stateId) await workspace.open(stateId);
      await refreshResearch();
    });
    const researchTimer = setInterval(() => void refreshResearch(), 15000);
    document.addEventListener("visibilitychange", refreshOnReturn);
    window.addEventListener("focus", refreshOnReturn);
    window.addEventListener("pagehide", rememberBeforeClosing);
    window.addEventListener("beforeunload", rememberBeforeClosing);
    return () => {
      rememberBeforeClosing();
      destroyed = true;
      clearInterval(researchTimer);
      activity?.stop();
      generation++;
      clearTimeout(pollTimer);
      clearTimeout(titleTimer);
      clearTimeout(exchangeNoticeTimer);
      document.removeEventListener("visibilitychange", refreshOnReturn);
      window.removeEventListener("focus", refreshOnReturn);
      window.removeEventListener("pagehide", rememberBeforeClosing);
      window.removeEventListener("beforeunload", rememberBeforeClosing);
    };
  });
</script>

<svelte:window on:resize={() => (compactMargins = window.innerWidth <= 1000)} />

<AppShell
  compact={compactMargins}
  courseScreen={workspacePage === "course"}
  reading={readingMaterial && workspacePage === "course"}
  wideReading={wideReading && readingMaterial && workspacePage === "course"}
  expanded={workspacePage !== "journal"}
  view={workspacePage}
  navigationLocked={navigationLocked ||
    loading ||
    uncertain ||
    sending ||
    documentEditing}
  onOpen={openWorkspace}
  {marginOpen}
  onToggleWidgets={() => (marginOpen = !marginOpen)}
  {quiet}
  {indexOpen}
  bind:scrollElement
  onReadingFocus={(element) => {
    if (
      workspacePage === "journal" &&
      (element === scrollElement || element.closest(".kept-chat"))
    )
      journalFocus = element;
  }}
  onToggleIndex={toggleIndex}
  {settingsOpen}
  onToggleSettings={toggleSettings}
  settingsLocked={navigationLocked || loading}
>
  {#if state === "loading"}
    <p class="notice">Connecting to the owned development backend…</p>
  {:else if state === "unavailable"}
    <p class="error">Unavailable: {error}</p>
  {:else}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if notice}<p class="notice">{notice}</p>{/if}
    {#if streamPaused && selected}<p class="notice" role="status">
        Progress connection interrupted.
        <button type="button" on:click={() => openConversation(selected)}
          >Reconnect response</button
        >
      </p>{/if}
    {#if activityError}<p class="notice" role="status">
        Background activity connection unavailable; idle work pauses when its
        lease expires. {activityError}
      </p>{/if}
    {#if uncertain}
      <p class="notice" role="status">
        The last submission has no confirmed outcome, so sending and switching
        are paused. Nothing is retried automatically.
        <button type="button" on:click={resolve}>Resolve pending request</button
        >
      </p>
    {/if}

    {#if onboardingError}
      <p class="notice" role="status">
        Getting-started guidance: {onboardingError}
        <button
          type="button"
          disabled={onboardingBusy}
          on:click={refreshOnboarding}>Refresh guidance</button
        >
      </p>
    {/if}
    {#if (workspacePage === "home" || workspacePage === "journal") && (onboarding?.status === "available" || onboarding?.status === "active")}
      <Welcome
        active={onboarding.status === "active"}
        busy={ownerBusy}
        hasDraft={!!draft.trim()}
        inConversation={workspacePage === "journal" &&
          selected === onboarding.conversation_id}
        onHelp={openHelp}
        onStart={startIntroduction}
        onContinue={continueIntroduction}
        onComplete={() => {
          if (!ownerBusy) void changeOnboarding("complete");
        }}
        onSkip={() => {
          if (!ownerBusy) void changeOnboarding("skip");
        }}
      />
    {/if}
    {#if workspacePage === "home"}
      <Home
        {workspace}
        active={!operationsOpen}
        disabled={navigationLocked ||
          loading ||
          uncertain ||
          sending ||
          documentEditing}
        {researchNew}
        {researchVersion}
        onOpen={openWorkspace}
        onResearch={openResearch}
        onHelp={openHelp}
        onSettings={toggleSettings}
      />
    {/if}
    <div class="kept-chat" hidden={workspacePage !== "journal"}>
      {#if sourceReturn}<button
          class="source-return"
          disabled={ownerBusy}
          on:click={returnToSource}
          >← Return to {sourceReturn.source.path.split("/").at(-1)}{sourceReturn
            .source.page
            ? ` · page ${sourceReturn.source.page}`
            : ""}</button
        >{/if}
      <!-- Middle slot: the current surface. The composer stays inside this block
         with the transcript, never as a column-wide footer, so a future
         document or terminal surface can occupy the column without inheriting a
         chat bar. -->
      {#key `${selected}:${generation}`}
        <Transcript
          bind:this={transcript}
          {scrollElement}
          visible={!settingsOpen && !indexOpen && workspacePage === "journal"}
          positions={readingPositions}
          onRemember={rememberReading}
          canEarlier={!!before}
          onEarlier={earlier}
          bind:following
          {selected}
          title={activeTitle}
          createdAt={selectedConversation?.created_at ?? draftCreatedAt}
          imported={selectedConversation?.imported ??
            turns.some((turn) => turn.imported)}
          {turns}
          onExchangeAction={exchangeAction}
          exchangeActionsDisabled={ownerBusy}
          {bodies}
          {stream}
          progressPaused={streamPaused}
          onInspect={controlsLocked ? undefined : openInspection}
          readInspection={(id, cursor) =>
            request("turn.inspect", { turn_id: id, cursor, page_size: 50 })}
          {bodyLoading}
          {loading}
          {loaded}
          onMore={loadBody}
          onOpenStudy={openStudySource}
          onOpenDocument={openDocumentDiscussion}
          onImage={loadImage}
          onImageViewer={(open) => (imageViewerOpen = open)}
          showComposer={!selectedConversation?.imported &&
            !turns.some((turn) => turn.imported)}
          bind:draft
          bind:selectedSkill
          maximumMessageBytes={selected
            ? conversationMessageBytes
            : launchMessageBytes}
          composerDisabled={!loaded || loading}
          composerReadOnly={documentEditing ||
            sending ||
            uncertain ||
            controlsLocked ||
            !!active ||
            !loaded}
          {sending}
          working={!!active}
          stopping={stoppingTurn === active && !!active}
          onSend={() => send()}
          onStop={cancel}
        />
      {/key}
    </div>
    <!-- Mounted workspaces and the transcript retain their own drafts/state. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      class="workspace-surface"
      hidden={workspacePage === "journal" || workspacePage === "home"}
      bind:this={workspaceSurface}
      tabindex="-1"
      role="region"
      aria-label="Personal planning workspace"
    >
      {#if tasksVisited}<div
          class="main-task-page"
          hidden={workspacePage !== "tasks"}
        >
          <Tasks
            {stateId}
            listVisit={taskListVisit}
            workspaceSnapshot={$workspace.snapshot}
            scope={personalTaskScope}
            active={!operationsOpen && workspacePage === "tasks"}
            {ownerBusy}
            bind:navigationLocked={personalTaskLock}
            onAccess={() => {
              if (navigationLocked || loading) return;
              showAccess++;
              showOperations();
            }}
            openConversation={(id) => {
              showChat();
              void openConversation(id);
            }}
          />
        </div>{/if}
      {#if researchVisited}<div
          class="main-task-page"
          hidden={workspacePage !== "research"}
        >
          <Research
            {stateId}
            target={researchTarget}
            {ownerBusy}
            onDiscuss={discussResearch}
            onChanged={() => void refreshResearch(true)}
            bind:navigationLocked={researchLock}
          />
        </div>{/if}
      <WorkspaceView
        {workspace}
        view={workspacePage}
        active={!operationsOpen &&
          workspacePage !== "home" &&
          workspacePage !== "tasks" &&
          workspacePage !== "research"}
        bind:this={workspaceView}
        onFileChosen={() => (indexOpen = false)}
        onReadingChange={(value) => (readingMaterial = value)}
        onWideChange={(value) => (wideReading = value)}
        fileSidebar={materialSidebar || null}
        courseId={workspaceCourse}
        busy={sending ||
          uncertain ||
          loading ||
          !!active ||
          notebookLock ||
          accessLock ||
          reminderLock ||
          peopleLock ||
          webLock ||
          modelsBusy ||
          researchLock ||
          backgroundLock ||
          personalTaskLock}
        {navigationLocked}
        onOpen={openWorkspace}
        onDiscuss={discussMaterial}
      />
    </div>
  {/if}

  <!-- The settings surface: a sibling of the journal grid, full width, which is how
       the reference renders `SettingsPage` (`+page.svelte:1206`). -->
  <svelte:fragment slot="footer">
    <Latest
      visible={workspacePage === "journal" && !following && turns.length > 0}
      onLatest={() => transcript?.toLatest()}
    />
  </svelte:fragment>

  <svelte:fragment slot="surface">
    <Operations
      active={settingsOpen}
      {showHelp}
      canAskHelp={state === "ready" &&
        !!selected &&
        !ownerBusy &&
        !documentEditing}
      onAskHelp={(text) => {
        if (ownerBusy || !selected || documentEditing) return;
        discussResearch(text);
        void openWorkspace("journal");
      }}
      {showAccess}
      {personalTaskLock}
      showWorkspace={showWorkspaceSettings}
      workspaceLocked={workspaceNavigationLocked($workspace)}
      {researchLock}
      bind:backgroundLock
      {recoverWeb}
      {recoverAccess}
      bind:accessLock
      accessBusy={sending ||
        uncertain ||
        loading ||
        !!active ||
        notebookLock ||
        reminderLock ||
        peopleLock ||
        webLock ||
        modelsBusy ||
        researchLock ||
        backgroundLock ||
        personalTaskLock ||
        $workspace.busy ||
        !!$workspace.pending}
      {stateId}
      {selected}
      {launchProfile}
      {identityLine}
      {capabilities}
      {ownerBusy}
      bind:reminderLock
      webBusy={sending ||
        uncertain ||
        loading ||
        !!active ||
        notebookLock ||
        accessLock ||
        reminderLock ||
        peopleLock ||
        modelsBusy}
      bind:webLock
      bind:peopleLock
      bind:modelsBusy
      onModelsChanged={modelsChanged}
      {notebookLock}
      showNotes={notebookOpen}
      {inspectTurnId}
      {inspection}
      onMoreInspect={() => inspect(inspectTurnId, true)}
      canRefresh={!!selected && !loading}
      canEarlier={!!before}
      earlierLoading={loading}
      onRefresh={() => selected && openConversation(selected)}
      onEarlier={earlier}
      canInspect={turns.length > 0 && !loading && !controlsLocked}
      onInspectLatest={() => {
        const latest = turns.at(-1);
        if (latest && !loading && !controlsLocked) openInspection(latest.id);
      }}
    >
      <svelte:fragment slot="workspace-settings"
        ><WorkspaceSettings
          {workspace}
          onArrange={() => arrangeWidgets()}
          onMedia={() => openWorkspace("media")}
          busy={sending ||
            uncertain ||
            loading ||
            !!active ||
            notebookLock ||
            accessLock ||
            reminderLock ||
            peopleLock ||
            webLock ||
            modelsBusy ||
            researchLock}
        /></svelte:fragment
      >
      <svelte:fragment slot="notes" let:openAccess>
        {#key selected}<Notebook
            conversationId={selected}
            onAccess={openAccess}
            busy={accessLock ||
              sending ||
              uncertain ||
              loading ||
              !!active ||
              reminderLock ||
              peopleLock ||
              webLock}
            bind:locked={notebookLock}
            onRemoved={() => {
              generation++;
              inspections = {};
              inspectTurnId = "";
            }}
          />{/key}
      </svelte:fragment>
    </Operations>
  </svelte:fragment>

  <!-- Keep one sidebar mounted on every page, preserving its expansion/filter
       and the material portal's owner when switching between work and chat. -->
  <svelte:fragment slot="left">
    <JournalIndex
      active={workspacePage === "journal"}
      onLatest={(id) => {
        void openWorkspace("journal");
        // Returning to a loaded latest chat keeps its draft and reading position.
        if (id && id !== selected) void openConversation(id);
      }}
      canCreate={state === "ready" && !ownerBusy}
      onCreate={createConversation}
      conversations={indexConversations}
      {selected}
      cursor={conversationCursor}
      {listing}
      disabled={state !== "ready" ||
        navigationLocked ||
        loading ||
        uncertain ||
        sending ||
        documentEditing}
      onOpen={(id) => {
        void openWorkspace("journal");
        void openConversation(id);
      }}
      onMore={() => listConversations(true).catch(failure)}
    />
    <StudyNav
      {researchNew}
      snapshot={$workspace.snapshot}
      view={workspacePage}
      courseId={workspaceCourse}
      onOpen={(destination) => {
        if (
          navigationLocked ||
          loading ||
          uncertain ||
          sending ||
          documentEditing
        )
          return;
        if (destination === "tasks") taskListVisit++;
        if (destination === "research") openResearch();
        else void openWorkspace(destination);
      }}
      disabled={state !== "ready" ||
        navigationLocked ||
        loading ||
        uncertain ||
        sending ||
        documentEditing}
    />
    <div hidden={workspacePage !== "course"}>
      <div bind:this={materialSidebar}></div>
    </div>
  </svelte:fragment>
  <svelte:fragment slot="right">
    {#each $workspace.snapshot?.settings.widgets ?? [] as widget (widget.id)}
      <WidgetCard
        {workspace}
        {widget}
        conversationId={selected}
        busy={ownerBusy}
        active={!settingsOpen && marginOpen}
        onConfigure={(id) => arrangeWidgets(undefined, id)}
        onOpen={openWorkspace}
      />
    {/each}
    <div class="margin-add">
      <button
        class="widget-icon"
        aria-label="Add widget"
        title="Add a widget"
        disabled={ownerBusy || !$workspace.snapshot}
        on:click={() => arrangeWidgets("right")}>+</button
      >
    </div>
  </svelte:fragment>
</AppShell>

{#if state === "ready"}<WorkspaceNotice {workspace} floating />{/if}

{#if exchangeNotice}
  <div class="exchange-notice" role="status">{exchangeNotice}</div>
{/if}
{#if widgetEditor}<WidgetEditor
    {workspace}
    conversationId={selected}
    side={widgetEditor.side}
    selectedId={widgetEditor.id || ""}
    busy={ownerBusy}
    onClose={() => (widgetEditor = null)}
  />{/if}

<style>
  .main-task-page {
    min-width: 0;
  }
  .main-task-page[hidden] {
    display: none;
  }
  .exchange-notice {
    position: fixed;
    z-index: 50;
    bottom: max(24px, env(safe-area-inset-bottom));
    left: 50%;
    transform: translateX(-50%);
    width: max-content;
    max-width: calc(100vw - 2rem);
    box-sizing: border-box;
    padding: 8px 12px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
    box-shadow: var(--ke-shadow-panel);
    pointer-events: none;
    animation: ke-dialog-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .kept-chat[hidden],
  .workspace-surface[hidden] {
    display: none !important;
  }
  /* The middle column's width is the shell grid's `minmax(0, 730px)`; a second
     cap here would silently fight it. */
  .notice,
  .error {
    margin: 0;
    padding: var(--ke-space-2) var(--ke-space-4);
  }
  .notice {
    background: var(--ke-warning-soft);
    color: var(--ke-text-primary);
  }
  .error {
    background: var(--ke-danger-soft);
    color: var(--ke-danger);
  }
  .notice button {
    margin-left: var(--ke-space-2);
    padding: var(--ke-space-1) var(--ke-space-2);
    border: 1px solid var(--ke-border-strong);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-primary);
    cursor: pointer;
  }
</style>
