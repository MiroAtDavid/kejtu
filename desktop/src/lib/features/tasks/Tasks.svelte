<script lang="ts">
  import "$lib/design/collection.css";
  import TaskReminder from "$lib/features/reminders/TaskReminder.svelte";
  let reminderLocked = false;
  import TaskDescription from "./TaskDescription.svelte";
  import TaskRows from "./TaskRows.svelte";
  import { DESCRIPTION_BYTES } from "./task-description";
  import type { Snapshot } from "$lib/features/workspace/workspace";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import { onMount, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { toPageTop } from "$lib/design/collection";
  import { identity, request } from "$lib/api/client";
  import {
    isNativeRequest,
    PROTOCOL,
    type Arguments,
    type TaskRecord,
    type TaskUrgency,
    type TaskRevision,
    type TaskSource,
  } from "$lib/api/wire.generated";

  export let stateId: string;
  export let workspaceSnapshot: Snapshot | null = null;
  export let active = true;
  // The navigation owner increments this only for an explicit Tasks list visit.
  export let listVisit = 0;
  let appliedListVisit = 0;
  export let scope = "";
  // Incremented with each scoped visit (a project's task row or its task list),
  // so opening the same task or list again applies it again.
  export let scopeVisit = 0;
  let appliedScope = "",
    appliedScopeVisit = 0;
  // A scoped task is read and then opened in two steps; the return-to-Tasks
  // list refresh waits so it cannot take the busy lock in between.
  let opening = false;
  let projectFilter = "",
    sectionFilter = "";
  let projectId = "",
    sectionId = "",
    description = "",
    placementChanged = false;
  let conflict = false;
  let switchTo: TaskRecord | "new" | null = null;
  let completed: TaskRecord[] = [],
    removed: TaskRecord[] = [];
  let completedNext: number | null = null,
    removedNext: number | null = null;
  // Which list is shown: open work, or the Completed / Removed views.
  let view: TaskGroup = "open";
  $: projects = workspaceSnapshot?.semesters ?? [];
  $: sections = workspaceSnapshot?.courses ?? [];
  $: dirty =
    title !== (selected?.title ?? "") ||
    due !== (selected?.due ?? "") ||
    description !== (selected?.description ?? "") ||
    urgency !== (selected?.urgency ?? "medium") ||
    placementChanged;
  $: canSave =
    !!title.trim() &&
    new TextEncoder().encode(title).length <= 500 &&
    new TextEncoder().encode(description).length <= DESCRIPTION_BYTES;
  $: if (
    ready &&
    !busy &&
    !pending &&
    (scope !== appliedScope || scopeVisit !== appliedScopeVisit)
  ) {
    appliedScope = scope;
    appliedScopeVisit = scopeVisit;
    projectFilter = scope.startsWith("project:") ? scope.slice(8) : "";
    sectionFilter = scope.startsWith("section:") ? scope.slice(8) : "";
    if (sectionFilter)
      projectFilter =
        sections.find((row) => row.id === sectionFilter)?.data.semester_id ??
        "";
    if (scope.startsWith("task-ref:")) void openReminderTask(scope.slice(9));
    else {
      // A project's (or module's) task list shows the list, like an explicit
      // list visit: an open task page closes, its draft kept.
      if ((projectFilter || sectionFilter) && !pending) closeEditor();
      void run(() => list());
    }
  }
  async function openReminderTask(ref: string) {
    let target: TaskRecord | undefined;
    opening = true;
    try {
      await run(async () => {
        const result = await request("tasks", {
          action: "read",
          read_ref: ref,
          limit: 1,
        });
        if ("task" in result) target = result.task;
      });
      if (target) requestOpen(target);
    } finally {
      opening = false;
    }
  }
  let wasActive = active;
  $: if (ready && active && !wasActive && !busy && !opening) {
    wasActive = true;
    void run(() => list());
  }
  $: if (!active) wasActive = false;
  function placementLabel(task: TaskRecord) {
    return (task.placements ?? [])
      .map((place) =>
        [
          projects.find((row) => row.id === place.project_id)?.data.title,
          sections.find((row) => row.id === place.section_id)?.data.title,
        ]
          .filter(Boolean)
          .join(" → "),
      )
      .join(", ");
  }
  function placementDraft() {
    const place =
      selected?.placements?.length === 1 ? selected.placements[0] : null;
    projectId = place?.project_id ?? "";
    sectionId = place?.section_id ?? "";
    placementChanged = false;
  }
  function changePlacement(project: string, section: string) {
    projectId = project;
    sectionId = section;
    placementChanged = true;
    saveDraft();
  }
  function requestOpen(task: TaskRecord | "new") {
    if (reminderLocked) return;
    if (dirty && (selected || task !== "new")) {
      switchTo = task;
      void revealDecision();
      return;
    }
    openTask(task);
  }
  function openTask(task: TaskRecord | "new") {
    if (task === "new") fresh();
    else
      void run(async () => {
        await read(task.read_ref);
        await openEditor();
      });
  }
  function discardAndOpen() {
    const target = switchTo;
    switchTo = null;
    title = selected?.title ?? "";
    due = selected?.due ?? "";
    description = selected?.description ?? "";
    urgency = selected?.urgency ?? "medium";
    placementChanged = false;
    if (target) openTask(target);
  }
  async function save() {
    if (!canSave) return;
    await send({
      action: "save",
      ...(selected ? { read_ref: selected.read_ref } : {}),
      title,
      description,
      urgency,
      due: due || null,
      ...(placementChanged
        ? {
            placement: {
              project_id: projectId || null,
              section_id: sectionId || null,
              expected_links: (selected?.placements ?? []).map(
                ({ link_id, revision }) => ({ link_id, revision }),
              ),
            },
          }
        : {}),
    });
  }
  async function complete(task: TaskRecord, done: boolean) {
    if (dirty) return;
    // On the task's own page the tick changes in place.
    const onPage =
      showEditor && selected?.id === task.id && task.outcome !== "cancelled";
    const focused = document.activeElement;
    await send({
      action: "transition",
      read_ref: task.read_ref,
      outcome: done ? "done" : "open",
    });
    // Busy temporarily disables the clicked checkbox. Restore its focus only,
    // without the page-opening scroll/focus intent.
    if (onPage && focused instanceof HTMLElement) restoreControl = focused;
  }
  async function listGroup(
    kind: "done" | "cancelled",
    append = false,
    keepExtent = false,
  ) {
    const offset = append
      ? ((kind === "done" ? completedNext : removedNext) ?? 0)
      : 0;
    const wanted =
      !append && keepExtent
        ? kind === "done"
          ? completed.length
          : removed.length
        : 0;
    const args: Change = {
      action: "list",
      outcome: kind,
      attention: "all",
      query: applied.query,
      ...(applied.urgency !== "all" ? { urgency: applied.urgency } : {}),
      ...(applied.order !== "due" ? { order: applied.order } : {}),
      limit: 12,
      offset,
      ...(applied.project ? { project_id: applied.project } : {}),
      ...(applied.section ? { section_id: applied.section } : {}),
    };
    const first = await request("tasks", args);
    if (!("items" in first)) throw new Error("Expected a task page.");
    let result = first;
    const rows = [...result.items];
    while (rows.length < wanted && result.next_offset !== null) {
      const page = await request("tasks", {
        ...args,
        offset: result.next_offset,
      });
      if (!("items" in page)) throw new Error("Expected a task page.");
      rows.push(...page.items);
      result = page;
    }
    if (kind === "done") {
      completed = append ? [...completed, ...rows] : rows;
      completedNext = result.next_offset;
    } else {
      removed = append ? [...removed, ...rows] : rows;
      removedNext = result.next_offset;
    }
  }
  export let ownerBusy = false;
  export let navigationLocked = false;
  export let onAccess: (() => void) | undefined = undefined;
  export let openConversation: (id: string) => void = () => {};
  type Change = Arguments["tasks"];
  let items: TaskRecord[] = [];
  let selected: TaskRecord | null = null;
  type TaskGroup = "open" | "done" | "cancelled";
  let restoreControl: HTMLElement | null = null;
  let page: HTMLElement;
  let addButton: HTMLButtonElement;
  function closeEditor() {
    if (reminderLocked) return;
    editorOpen = false;
    switchTo = null;
    focusWhenReady = false;
  }
  // The back link returns to the row the task was opened from.
  async function back() {
    if (reminderLocked) return;
    const id = selected?.id;
    closeEditor();
    await tick();
    if (showEditor) return;
    const row = id
      ? page?.querySelector<HTMLElement>(`[data-task="${id}"] .task-row`)
      : null;
    (row ?? addButton)?.focus();
  }
  function show(next: TaskGroup) {
    view = next;
    if (next !== "open") void run(() => listGroup(next));
  }
  // Chips and pickers apply at once; Find applies on Enter or its button.
  function apply() {
    void run(() => list());
  }
  function chooseProject(id: string) {
    projectFilter = id;
    sectionFilter = "";
    apply();
  }
  $: if (ready && !busy && listVisit && listVisit !== appliedListVisit) {
    appliedListVisit = listVisit;
    // An explicit list visit keeps the saved draft, never hides recovery.
    if (!pending) closeEditor();
  }

  let history: TaskRevision[] = [];
  let sources: TaskSource[] = [];
  let historyNext: number | null = null;
  let sourceNext: number | null = null;
  let next: number | null = null;
  let total = 0,
    openTotal = 0;
  let title = "",
    due = "",
    query = "";
  let urgency: TaskUrgency = "medium";
  let urgencyFilter: NonNullable<Change["urgency"]> = "all";
  let taskOrder: NonNullable<Change["order"]> = "due";
  const urgencyOptions = [
    { value: "low", label: "Low" },
    { value: "medium", label: "Medium" },
    { value: "high", label: "High" },
  ];
  let outcome: Change["outcome"] = "open";
  let attention: Change["attention"] = "all";
  let busy = false,
    error = "",
    notice = "",
    ready = false;
  let editorOpen = false;
  let descriptionPreview = false;
  let titleField: HTMLInputElement;
  let taskEditor: HTMLDivElement;
  let focusWhenReady = false;
  let switchDecision: HTMLDivElement;
  const editorId = `task-editor-${crypto.randomUUID()}`;
  // A task has its own page: while it is open the list is not shown.
  $: showEditor = editorOpen || pending !== null;
  async function revealDecision() {
    editorOpen = true;
    await tick();
    switchDecision?.focus({ preventScroll: true });
    switchDecision?.scrollIntoView?.({ block: "nearest" });
  }
  let pending: Change | null = null;
  $: taskBlocked = busy || reminderLocked;
  $: navigationLocked = taskBlocked || pending !== null;
  let absent = false;
  const localDay = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  };
  let day = localDay();
  let listed = false;
  let applied: {
    query: string;
    outcome: Change["outcome"];
    attention: Change["attention"];
    day: string;
    project: string;
    section: string;
    urgency: NonNullable<Change["urgency"]>;
    order: NonNullable<Change["order"]>;
  } = {
    query: "",
    outcome,
    attention,
    day,
    project: "",
    section: "",
    urgency: urgencyFilter,
    order: taskOrder,
  };
  $: filtersChanged =
    query !== applied.query ||
    outcome !== applied.outcome ||
    attention !== applied.attention ||
    day !== applied.day ||
    projectFilter !== applied.project ||
    sectionFilter !== applied.section ||
    urgencyFilter !== applied.urgency ||
    taskOrder !== applied.order;
  function dateLabel(value: string) {
    if (value === localDay()) return "Today";
    const date = new Date(value + "T12:00:00");
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      ...(date.getFullYear() !== new Date().getFullYear()
        ? { year: "numeric" as const }
        : {}),
    }).format(date);
  }
  // A new task starts in its title. An opened task is shown, not edited:
  // focus moves to its page, so the title has no cursor and long titles keep
  // their dots.
  async function focusOpened() {
    await tick();
    (selected ? taskEditor : titleField)?.focus({ preventScroll: true });
    toPageTop(page);
  }
  async function openEditor() {
    descriptionPreview = selected !== null;
    editorOpen = true;
    // Existing-task reads and saves keep fields disabled until their owner
    // settles. Consume one focus intent after run releases that lock.
    if (busy) focusWhenReady = true;
    else await focusOpened();
  }
  $: journalKey = `kejtu-next.tasks.v1.${stateId}`;

  function saveDraft() {
    localStorage.setItem(
      journalKey,
      JSON.stringify({
        stateId,
        title,
        due,
        description,
        urgency,
        projectId,
        sectionId,
        placementChanged,
        read_ref: selected?.read_ref ?? null,
        pending,
      }),
    );
  }
  function failure(caught: unknown) {
    error = caught instanceof Error ? caught.message : String(caught);
    conflict = !!selected && /changed|conflict/i.test(error);
  }
  function editDraft(field: "title" | "due", value: string) {
    // Input handlers run before Svelte's binding update. Persist this event's
    // value, not the previous keystroke, even if the view closes immediately.
    if (field === "title") title = value;
    else due = value;
    try {
      saveDraft();
    } catch (caught) {
      failure(caught);
    }
  }
  async function list(append = false) {
    // Paging belongs to the filter snapshot that produced the visible rows.
    // Editing a filter must not append another query's page to that list.
    const filters = append
      ? applied
      : {
          outcome,
          attention,
          query,
          day,
          project: projectFilter,
          section: sectionFilter,
          urgency: urgencyFilter,
          order: taskOrder,
        };
    const sameFilters =
      filters.query === applied.query &&
      filters.outcome === applied.outcome &&
      filters.attention === applied.attention &&
      filters.day === applied.day &&
      filters.project === applied.project &&
      filters.section === applied.section &&
      filters.urgency === applied.urgency &&
      filters.order === applied.order;
    // A refresh of the same filters retains the already-loaded extent, so
    // coming back from a later-page task finds its row where it was.
    const wanted = !append && sameFilters ? items.length : 0;
    const args: Change = {
      action: "list",
      outcome: filters.outcome,
      attention: filters.attention,
      query: filters.query,
      day: filters.day,
      ...(filters.urgency !== "all" ? { urgency: filters.urgency } : {}),
      ...(filters.order !== "due" ? { order: filters.order } : {}),
      ...(filters.project ? { project_id: filters.project } : {}),
      ...(filters.section ? { section_id: filters.section } : {}),
      offset: append ? (next ?? 0) : 0,
      limit: 12,
    };
    const first = await request("tasks", args);
    if (!("items" in first)) throw new Error("Expected a task page.");
    let result = first;
    const rows = [...result.items];
    while (rows.length < wanted && result.next_offset !== null) {
      const page = await request("tasks", {
        ...args,
        offset: result.next_offset,
      });
      if (!("items" in page)) throw new Error("Expected a task page.");
      rows.push(...page.items);
      result = page;
    }
    items = append ? [...items, ...rows] : rows;
    next = result.next_offset;
    total = result.total;
    openTotal = result.open_total;
    applied = filters;
    listed = true;
    if (!append && view !== "open") await listGroup(view, false, sameFilters);
  }
  async function run(work: () => Promise<void>) {
    if (reminderLocked) return;
    if (busy) return;
    busy = true;
    error = "";
    try {
      await work();
    } catch (caught) {
      failure(caught);
    } finally {
      busy = false;
      if (restoreControl) {
        const control = restoreControl;
        restoreControl = null;
        await tick();
        if (control.isConnected && !control.matches(":disabled"))
          control.focus({ preventScroll: true });
      } else if (focusWhenReady) {
        focusWhenReady = false;
        await focusOpened();
      }
    }
  }
  async function read(ref: string, preserveDraft = false) {
    const result = await request("tasks", {
      action: "read",
      read_ref: ref,
      limit: 12,
    });
    if (!("task" in result)) throw new Error("Expected a current task.");
    selected = result.task;
    history = [];
    // Ordinary reads carry only the current task. History starts at page zero
    // when the user explicitly requests it, including after a fresh reload.
    historyNext = 0;
    sources = [];
    sourceNext = null;
    if (!preserveDraft) {
      title = selected.title;
      due = selected.due ?? "";
      description = selected.description ?? "";
      urgency = selected.urgency ?? "medium";
      placementDraft();
    }
    conflict = false;
    saveDraft();
  }
  function fresh() {
    if (!selected && (title || due || description || urgency !== "medium")) {
      void openEditor();
      return;
    }
    selected = null;
    title = "";
    due = "";
    description = "";
    urgency = "medium";
    projectId = projectFilter;
    sectionId = sectionFilter;
    placementChanged = !!(projectId || sectionId);
    history = [];
    sources = [];
    notice = "";
    saveDraft();
    void openEditor();
  }
  async function accepted(result: { status: string; revision?: number }) {
    const wasOpen = editorOpen;
    // A confirmed change needs no confirmation note; it clears an earlier one
    // (such as a recovery notice) so nothing stale stays on the page.
    notice = "";
    pending = null;
    absent = false;
    saveDraft();
    await list();
    if (selected) {
      await read(selected.read_ref);
      if (!wasOpen) closeEditor();
    } else {
      title = "";
      due = "";
      description = "";
      urgency = "medium";
      projectId = "";
      sectionId = "";
      placementChanged = false;
      editorOpen = false;
      saveDraft();
    }
  }
  async function send(change: Change) {
    if (pending) return;
    pending = { ...change, mutation_key: identity() };
    absent = false;
    // Persist the exact intent before dispatch. Response loss never creates a
    // second action; reopening first resolves this key against the authority.
    saveDraft();
    let result;
    try {
      result = await request("tasks", pending);
    } catch (caught) {
      const code = (caught as { code?: string }).code;
      if (
        code &&
        [
          "task_arguments",
          "task_conflict",
          "task_placement",
          "task_placement_forbidden",
          "planner_arguments",
          "planner_missing",
          "planner_cancelled",
        ].includes(code)
      ) {
        pending = null;
        saveDraft();
      }
      throw caught;
    }
    if (!("revision" in result))
      throw new Error("Expected a durable task receipt.");
    await accepted(result);
  }
  async function resolve() {
    if (!pending?.mutation_key) return;
    const result = await request("task.lookup", {
      mutation_key: pending.mutation_key,
    });
    if (result.status === "absent") {
      absent = true;
      notice =
        "No saved effect for this intent. Your draft is retained; retry the same change or release it for editing.";
    } else await accepted(result);
  }
  async function retry() {
    if (!pending || !absent) return;
    absent = false;
    let result;
    try {
      result = await request("tasks", pending);
    } catch (caught) {
      const code = (caught as { code?: string }).code;
      if (
        code &&
        [
          "task_arguments",
          "task_conflict",
          "task_placement",
          "task_placement_forbidden",
          "planner_arguments",
          "planner_missing",
          "planner_cancelled",
        ].includes(code)
      ) {
        pending = null;
        saveDraft();
      }
      throw caught;
    }
    if (!("revision" in result))
      throw new Error("Expected a durable task receipt.");
    await accepted(result);
  }
  function release() {
    if (absent) {
      pending = null;
      absent = false;
      saveDraft();
    }
  }
  async function more(view: "history" | "sources", offset = 0) {
    if (!selected) return;
    const result = await request("tasks", {
      action: "read",
      read_ref: selected.read_ref,
      view,
      offset,
      limit: 12,
    });
    if (!("task" in result)) throw new Error("Expected task detail.");
    if (result.task.revision !== selected.revision) {
      throw new Error(
        "Task changed. Load current task before paging its history. Your draft is retained.",
      );
    }
    if (view === "history") {
      history = offset ? [...history, ...result.history] : result.history;
      historyNext = result.next_offset;
    } else {
      sources = offset
        ? [...sources, ...(result.sources ?? [])]
        : (result.sources ?? []);
      sourceNext = result.next_offset;
    }
  }
  onMount(() => {
    run(async () => {
      const raw = localStorage.getItem(journalKey);
      let ref: string | null = null;
      let legacyUrgency = false;
      if (raw) {
        const saved = JSON.parse(raw);
        if (
          saved.stateId !== stateId ||
          typeof saved.title !== "string" ||
          typeof saved.due !== "string"
        )
          throw new Error(
            "Task draft identity is invalid; retained for inspection.",
          );
        title = saved.title;
        due = saved.due;
        if (
          saved.description !== undefined &&
          typeof saved.description !== "string"
        )
          throw new Error(
            "The saved task description is invalid; draft retained.",
          );
        description = saved.description ?? "";
        if (
          saved.urgency !== undefined &&
          !urgencyOptions.some((option) => option.value === saved.urgency)
        )
          throw new Error("The saved task urgency is invalid; draft retained.");
        legacyUrgency = saved.urgency === undefined;
        urgency = saved.urgency ?? "medium";
        projectId = saved.projectId ?? "";
        sectionId = saved.sectionId ?? "";
        placementChanged = saved.placementChanged === true;
        ref = saved.read_ref;
        if (saved.pending) {
          if (
            !isNativeRequest({
              protocol: PROTOCOL,
              request_id: "0".repeat(32),
              operation: "tasks",
              arguments: saved.pending,
            }) ||
            !["save", "transition"].includes(saved.pending.action) ||
            !saved.pending.mutation_key
          )
            throw new Error(
              "Pending task intent is invalid; nothing was sent.",
            );
          pending = saved.pending;
        }
      }
      descriptionPreview = !!ref;
      editorOpen = !!(
        ref ||
        title ||
        due ||
        description ||
        urgency !== "medium" ||
        pending
      );
      ready = true;
      await list();
      if (ref) {
        await read(ref, true);
        if (legacyUrgency) {
          urgency = selected?.urgency ?? "medium";
          saveDraft();
        }
        if (!placementChanged) placementDraft();
      }
      if (pending) await resolve();
    });
  });
</script>

<section
  class="tasks collection-page"
  aria-label="Personal tasks"
  bind:this={page}
>
  {#if showEditor}
    <div
      class="task-editor"
      id={editorId}
      bind:this={taskEditor}
      tabindex="-1"
      role="region"
      aria-label={selected ? "Task details" : "New task"}
    >
      <button
        class="cp-back"
        aria-label="Back to tasks"
        disabled={taskBlocked || !!pending || !!switchTo}
        on:click={back}><Icon name="left" size={13} />Tasks</button
      >
      <form class="task-head" on:submit|preventDefault={() => run(save)}>
        {#if selected && selected.outcome !== "cancelled"}<input
            type="checkbox"
            class="cp-tick"
            checked={selected.outcome === "done"}
            disabled={taskBlocked || !!pending || dirty}
            aria-label={selected.outcome === "done"
              ? "Completed"
              : "Mark as done"}
            on:change={(event) =>
              run(() => complete(selected!, event.currentTarget.checked))}
          />{/if}
        <label class="task-title"
          ><span class="cp-sr">Task title</span><input
            bind:this={titleField}
            value={title}
            on:input={(e) => editDraft("title", e.currentTarget.value)}
            required
            maxlength="500"
            placeholder={selected ? "Task title" : "New task"}
            disabled={taskBlocked || !!pending}
          /></label
        >
        <div class="task-actions">
          {#if dirty}<span class="cp-hint">Unsaved changes</span>{/if}
          {#if selected?.outcome === "cancelled"}<span class="cp-tag"
              >Removed</span
            ><button
              type="button"
              class="cp-action"
              disabled={taskBlocked || !!pending || dirty}
              on:click={() => run(() => complete(selected!, false))}
              >Restore task</button
            >{/if}
          <button disabled={taskBlocked || !!pending || !canSave}
            >Save task</button
          >
        </div>
      </form>
      {#if error}<p class="cp-note" role="alert">{error}</p>{/if}
      {#if notice}<p class="cp-note" role="status">{notice}</p>{/if}
      {#if pending}<div class="cp-note">
          <p>
            The last change hasn’t been confirmed. Check its saved result before
            continuing.
          </p>
          <div class="note-actions">
            <button disabled={taskBlocked} on:click={() => run(resolve)}
              >Check saved result</button
            >{#if absent}<button
                disabled={taskBlocked}
                on:click={() => run(retry)}>Retry same change</button
              ><button disabled={taskBlocked} on:click={release}
                >Keep draft and edit</button
              >{/if}
          </div>
        </div>{/if}
      {#if switchTo}<div
          class="cp-note"
          role="alert"
          tabindex="-1"
          bind:this={switchDecision}
        >
          <p>You have unsaved changes in this task.</p>
          <div class="note-actions">
            <button
              disabled={taskBlocked || !!pending || !canSave}
              on:click={() =>
                run(async () => {
                  await save();
                  const target = switchTo;
                  switchTo = null;
                  if (target === "new") fresh();
                  else if (target) {
                    await read(target.read_ref);
                    await openEditor();
                  }
                })}>Save changes</button
            ><button
              disabled={taskBlocked || !!pending}
              on:click={discardAndOpen}>Discard changes</button
            ><button on:click={() => (switchTo = null)}>Keep editing</button>
          </div>
        </div>{/if}
      {#if conflict && selected}<div class="cp-note">
          <button
            disabled={taskBlocked || !!pending}
            on:click={() => run(() => read(selected!.read_ref, true))}
            >Load current task, keep draft</button
          >
        </div>{/if}
      <div class="cp-item">
        <div class="cp-main">
          <section class="cp-section">
            <TaskDescription
              bind:preview={descriptionPreview}
              value={description}
              disabled={taskBlocked || !!pending}
              onChange={(value) => {
                description = value;
                saveDraft();
              }}
            />
          </section>
          {#if selected}
            <section class="cp-section" aria-label="Linked">
              <h3 class="cp-cap">Linked</h3>
              <p class="cp-hint">
                {selected.source_status === "manual"
                  ? "Created here."
                  : selected.source_status === "available"
                    ? "Linked to a source conversation."
                    : "The original source is no longer available."}
                {#if selected.note_status === "available"}A supporting note is
                  linked.{:else if selected.note_status === "unavailable"}The
                  linked note is no longer available.{/if}
              </p>
              {#each sources as source}<blockquote>{source.quote}</blockquote>
                {#if source.conversation_id}<button
                    on:click={() => openConversation(source.conversation_id!)}
                    >Open source conversation</button
                  >{/if}{/each}
              <div class="note-actions">
                {#if sourceNext !== null}<button
                    disabled={taskBlocked}
                    on:click={() => run(() => more("sources", sourceNext!))}
                    >More sources</button
                  >{:else if !sources.length}<button
                    disabled={taskBlocked}
                    on:click={() => run(() => more("sources"))}
                    >Read supporting sources</button
                  >{/if}
                {#if selected.note_id}<button
                    disabled={taskBlocked || !!pending || dirty}
                    on:click={() =>
                      run(() =>
                        send({
                          action: "save",
                          read_ref: selected!.read_ref,
                          note_id: null,
                        }),
                      )}>Remove note link</button
                  >{/if}
              </div>
            </section>
            <section class="cp-section" aria-label="History">
              <h3 class="cp-cap">History</h3>
              {#if history.length}<ol class="history">
                  {#each history as revision}<li>
                      Revision {revision.revision}: {revision.title} · {revision.outcome ===
                      "cancelled"
                        ? "Removed"
                        : revision.outcome === "done"
                          ? "Done"
                          : "Open"} · {revision.due ??
                        "No due date"}{#if revision.description}<pre>{revision.description}</pre>{/if}
                    </li>{/each}
                </ol>{/if}
              {#if historyNext !== null}<div class="note-actions">
                  <button
                    disabled={taskBlocked}
                    on:click={() => run(() => more("history", historyNext!))}
                    >{historyNext === 0
                      ? "Read task history"
                      : "More history"}</button
                  >
                </div>{/if}
            </section>
          {/if}
        </div>
        <div class="cp-side" role="group" aria-label="Task properties">
          <label class="cp-row"
            ><span class="cp-key">Due</span><input
              type="date"
              aria-label="Due date"
              value={due}
              on:input={(e) => editDraft("due", e.currentTarget.value)}
              disabled={taskBlocked || !!pending}
            /></label
          >
          <div class="cp-row">
            <ChoicePicker
              id="task-urgency"
              label="Urgency"
              value={urgency}
              options={urgencyOptions}
              onChange={(value) => {
                urgency = value as TaskUrgency;
                try {
                  saveDraft();
                } catch (caught) {
                  failure(caught);
                }
              }}
              disabled={taskBlocked || !!pending}
            />
          </div>
          {#if workspaceSnapshot?.settings.university_enabled}
            <div class="cp-row">
              <ChoicePicker
                id="task-project"
                label="Project"
                value={projectId}
                options={[
                  { value: "", label: "No project" },
                  ...projects.map((row) => ({
                    value: row.id,
                    label: row.data.title,
                  })),
                ]}
                onChange={(value) => changePlacement(value, "")}
                disabled={taskBlocked || !!pending}
              />
            </div>
            <div class="cp-row">
              <ChoicePicker
                id="task-section"
                label="Module"
                value={sectionId}
                options={[
                  {
                    value: "",
                    label: projectId ? "Whole project" : "No module",
                  },
                  ...sections
                    .filter((row) => (row.data.semester_id ?? "") === projectId)
                    .map((row) => ({ value: row.id, label: row.data.title })),
                ]}
                onChange={(value) => changePlacement(projectId, value)}
                disabled={taskBlocked || !!pending}
              />
            </div>
            {#if (selected?.placements?.length ?? 0) > 1}<p
                class="cp-hint side-text"
              >
                Linked to {selected?.placements?.length} places. Choosing a new location
                replaces those links.
              </p>{/if}
          {/if}
          {#if selected && selected.outcome === "open"}<div class="side-block">
              {#key selected.id}<TaskReminder
                  {stateId}
                  task={selected}
                  timezone={workspaceSnapshot?.settings?.availability
                    ?.timezone ??
                    Intl.DateTimeFormat().resolvedOptions().timeZone}
                  disabled={taskBlocked || !!pending || dirty || ownerBusy}
                  bind:locked={reminderLocked}
                />{/key}
            </div>{/if}
          {#if selected && selected.outcome !== "cancelled"}<div
              class="side-block"
            >
              <button
                class="cp-text"
                disabled={taskBlocked || !!pending || dirty}
                on:click={() =>
                  run(() =>
                    send({
                      action: "transition",
                      read_ref: selected!.read_ref,
                      outcome: "cancelled",
                    }),
                  )}>Remove task</button
              >
              <p class="cp-hint">You can restore it from Removed.</p>
            </div>{/if}
        </div>
      </div>
    </div>
  {:else}
    <div class="cp-head">
      <div>
        <h2 class="cp-title">Personal tasks</h2>
        <p class="cp-status task-count">
          {#if listed}{total} open {total === 1
              ? "task"
              : "tasks"}{:else}&nbsp;{/if}
        </p>
      </div>
      <button
        class="cp-action"
        bind:this={addButton}
        disabled={taskBlocked || !!pending || !ready}
        on:click={() => requestOpen("new")}>New task</button
      >
    </div>
    {#if error}<p class="cp-note" role="alert">{error}</p>{/if}
    {#if notice}<p class="cp-note" role="status">{notice}</p>{/if}
    {#if selected ? dirty : title || due || description || urgency !== "medium"}<button
        class="cp-note task-resume"
        disabled={taskBlocked || !!pending}
        on:click={openEditor}
        ><span
          >{selected ? "Return to task" : "Continue draft"}{title
            ? ` · ${title}`
            : ""}</span
        ><span class="cp-hint">Unsaved changes</span></button
      >{/if}
    <div class="cp-bar">
      <div class="cp-pills" role="group" aria-label="Task lists">
        <button
          class="cp-pill"
          aria-pressed={view === "open"}
          disabled={taskBlocked}
          on:click={() => show("open")}
          >Open {#if listed}<span class="cp-count">{total}</span>{/if}</button
        >
        <button
          class="cp-pill"
          aria-pressed={view === "done"}
          disabled={taskBlocked}
          on:click={() => show("done")}>Completed</button
        >
        <button
          class="cp-pill"
          aria-pressed={view === "cancelled"}
          disabled={taskBlocked}
          on:click={() => show("cancelled")}>Removed</button
        >
      </div>
      <form class="cp-find" role="search" on:submit|preventDefault={apply}>
        <button
          class="cp-icon"
          aria-label="Apply filters"
          title="Apply filters"
          disabled={taskBlocked || !!pending}
          ><Icon name="search" size={14} /></button
        >
        <label
          ><span class="cp-sr">Find tasks</span><input
            type="search"
            bind:value={query}
            maxlength="256"
            placeholder="Find"
          /></label
        >
      </form>
      <button
        class="cp-icon"
        aria-label="Refresh tasks"
        title="Refresh tasks"
        disabled={taskBlocked || !!pending}
        on:click={apply}><Icon name="refresh" size={14} /></button
      >
    </div>
    <div class="cp-chips">
      {#if workspaceSnapshot?.settings.university_enabled && projects.length}<div
          class="cp-chip-group"
          role="group"
          aria-label="Project"
        >
          <button
            class="cp-chip"
            aria-pressed={!projectFilter}
            disabled={taskBlocked || !!pending}
            on:click={() => chooseProject("")}>All projects</button
          >
          {#each projects as row (row.id)}<button
              class="cp-chip"
              aria-pressed={projectFilter === row.id}
              disabled={taskBlocked || !!pending}
              on:click={() => chooseProject(row.id)}>{row.data.title}</button
            >{/each}
        </div>{/if}
      <span class="cp-hint changed"
        >{#if filtersChanged}Apply filters to update this list.{/if}</span
      >
      <ChoicePicker
        chip
        arrow={false}
        hideLabel
        id="task-urgency-filter"
        label="Urgency filter"
        value={urgencyFilter}
        options={[
          { value: "all", label: "All urgency levels" },
          ...urgencyOptions,
        ]}
        onChange={(value) => {
          urgencyFilter = value as NonNullable<Change["urgency"]>;
          apply();
        }}
        disabled={taskBlocked || !!pending}
      />
      <ChoicePicker
        chip
        arrow={false}
        hideLabel
        id="task-order"
        label="Sort by"
        value={taskOrder}
        options={[
          { value: "due", label: "Due date first" },
          { value: "urgency", label: "Highest urgency" },
        ]}
        onChange={(value) => {
          taskOrder = value as NonNullable<Change["order"]>;
          apply();
        }}
        disabled={taskBlocked || !!pending}
      />
    </div>
    {#if sectionFilter}<p class="cp-hint scope">
        Module: {sections.find((row) => row.id === sectionFilter)?.data.title ||
          "Selected module"}
        <button
          class="cp-text"
          disabled={taskBlocked || !!pending}
          on:click={() => {
            sectionFilter = "";
            apply();
          }}>Show whole project</button
        >
      </p>{/if}
    <div class="task-lists">
      {#if view === "open"}
        <TaskRows
          {items}
          grouped={applied.order === "due"}
          disabled={taskBlocked || !!pending}
          completionDisabled={dirty}
          onOpen={requestOpen}
          onComplete={(task, done) => run(() => complete(task, done))}
          {placementLabel}
        />
        {#if listed && !items.length}<p class="cp-empty">
            {query || projectFilter || sectionFilter || urgencyFilter !== "all"
              ? "No open tasks match these filters."
              : "Nothing to do here yet. Add a task when you need one."}
          </p>{/if}
        {#if next !== null}<button
            class="more"
            disabled={taskBlocked || !!pending || filtersChanged}
            on:click={() => run(() => list(true))}>More tasks</button
          >{/if}
      {:else if view === "done"}
        <TaskRows
          items={completed}
          disabled={taskBlocked || !!pending}
          completionDisabled={dirty}
          onOpen={requestOpen}
          onComplete={(task, done) => run(() => complete(task, done))}
          {placementLabel}
        />
        {#if !busy && !completed.length}<p class="cp-empty">
            No completed tasks in this view.
          </p>{/if}
        {#if completedNext !== null}<button
            class="more"
            disabled={taskBlocked}
            on:click={() => run(() => listGroup("done", true))}
            >More completed tasks</button
          >{/if}
      {:else}
        <TaskRows
          items={removed}
          disabled={taskBlocked || !!pending}
          onOpen={requestOpen}
          onComplete={(task, done) => run(() => complete(task, done))}
          {placementLabel}
        />
        {#if !busy && !removed.length}<p class="cp-empty">
            No removed tasks in this view.
          </p>{/if}
        {#if removedNext !== null}<button
            class="more"
            disabled={taskBlocked}
            on:click={() => run(() => listGroup("cancelled", true))}
            >More removed tasks</button
          >{/if}
      {/if}
    </div>
    {#if onAccess}<div class="task-footer">
        <button
          class="cp-text"
          disabled={navigationLocked || ownerBusy}
          on:click={() => onAccess?.()}>Model permissions</button
        >
      </div>{/if}
  {/if}
</section>

<style>
  .tasks {
    min-width: 0;
  }
  .task-lists {
    margin-top: 18px;
  }
  .more {
    margin-top: 12px;
  }
  .task-resume {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 4px 12px;
    width: 100%;
    border: 0;
    text-align: left;
    font-size: var(--ke-text-xs);
  }
  .changed {
    flex: 1 1 12px;
    min-height: 1.6em;
    padding: 0 6px;
    text-align: right;
  }
  .scope {
    margin-top: 10px;
  }
  .task-footer {
    margin-top: 26px;
  }
  /* A task's own page. */
  .task-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 14px;
    margin-top: 10px;
  }
  .task-head .cp-tick {
    width: 20px;
    height: 20px;
  }
  .task-head .cp-tick:checked::before {
    width: 9px;
    height: 5px;
  }
  .task-editor:focus {
    outline: none;
  }
  .task-title {
    flex: 1 1 280px;
    min-width: 0;
    /* Keeps a cut title's dots clear of the buttons beside it. */
    margin-right: 12px;
  }
  .task-title input {
    width: 100%;
    min-height: 0;
    padding: 0 0 2px;
    border: 0;
    border-bottom: 1px solid transparent;
    border-radius: 0;
    outline: 0;
    background: transparent;
    color: var(--ke-text-primary);
    font: 400 28px/1.3 var(--ke-font-heading);
    letter-spacing: -0.5px;
    text-overflow: ellipsis;
  }
  .task-title input:hover:not(:disabled) {
    border-bottom-color: var(--ke-border);
  }
  .task-title input:focus {
    border-bottom-color: var(--ke-border-strong);
  }
  .task-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }
  .cp-note p {
    margin: 0;
  }
  .note-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .note-actions:empty {
    display: none;
  }
  .history {
    margin: 0;
    padding: 0;
    list-style: none;
    color: var(--ke-text-secondary);
  }
  pre,
  blockquote {
    margin: 6px 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font: inherit;
  }
  blockquote {
    padding-left: 10px;
    border-left: 2px solid var(--ke-border-strong);
    color: var(--ke-text-secondary);
  }
  .side-block {
    margin-top: 4px;
    padding: 10px 14px 8px;
    border-top: 1px solid var(--ke-border);
  }
  .side-text {
    padding: 0 14px 8px;
  }
  @media (max-width: 650px) {
    .task-title input {
      font-size: 24px;
    }
  }
</style>
