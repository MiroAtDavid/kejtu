<script lang="ts">
  import "./planning.css";
  import TaskDescription from "./TaskDescription.svelte";
  import TaskRows from "./TaskRows.svelte";
  import { DESCRIPTION_BYTES } from "./task-description";
  import type { Snapshot } from "$lib/features/workspace/workspace";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import { onMount, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { identity, request } from "$lib/api/client";
  import {
    isNativeRequest,
    PROTOCOL,
    type Arguments,
    type TaskRecord,
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
  let appliedScope = "";
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
  let completedOpen = false,
    removedOpen = false;
  $: projects = workspaceSnapshot?.semesters ?? [];
  $: sections = workspaceSnapshot?.courses ?? [];
  $: dirty =
    title !== (selected?.title ?? "") ||
    due !== (selected?.due ?? "") ||
    description !== (selected?.description ?? "") ||
    placementChanged;
  $: canSave =
    !!title.trim() &&
    new TextEncoder().encode(title).length <= 500 &&
    new TextEncoder().encode(description).length <= DESCRIPTION_BYTES;
  $: if (ready && !busy && !pending && scope !== appliedScope) {
    appliedScope = scope;
    projectFilter = scope.startsWith("project:") ? scope.slice(8) : "";
    sectionFilter = scope.startsWith("section:") ? scope.slice(8) : "";
    if (sectionFilter)
      projectFilter =
        sections.find((row) => row.id === sectionFilter)?.data.semester_id ??
        "";
    void run(() => list());
  }
  let wasActive = active;
  $: if (ready && active && !wasActive && !busy) {
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
    const keepLocation =
      showEditor &&
      selected?.id === task.id &&
      task.outcome !== "cancelled" &&
      editorGroup !== "top";
    const focused = document.activeElement;
    if (keepLocation && !pinned) {
      const group = editorGroup as TaskGroup;
      const rows =
        group === "open" ? openRows : group === "done" ? doneRows : removedRows;
      pinned = {
        id: task.id,
        group,
        index: rows.findIndex((row) => row.id === task.id),
      };
    }
    await send(
      {
        action: "transition",
        read_ref: task.read_ref,
        outcome: done ? "done" : "open",
      },
      keepLocation,
    );
    // Busy temporarily disables the clicked checkbox. Restore its focus only,
    // without the editor-opening scroll/focus intent or changing its DOM host.
    if (keepLocation && focused instanceof HTMLElement)
      restoreControl = focused;
  }
  async function listGroup(kind: "done" | "cancelled", append = false) {
    const offset = append
      ? ((kind === "done" ? completedNext : removedNext) ?? 0)
      : 0;
    const wanted =
      !append && pinned
        ? kind === "done"
          ? completed.length
          : removed.length
        : 0;
    const args: Change = {
      action: "list",
      outcome: kind,
      attention: "all",
      query: applied.query,
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
  type RowAnchor = { id: string; group: TaskGroup; index: number };
  let pinned: RowAnchor | null = null;
  let restoreControl: HTMLElement | null = null;
  // Keep one visual host across list-then-read refreshes; source lists and counts
  // remain authoritative. Closing/switching/filtering releases this anchor.
  function rowsFor(
    group: TaskGroup,
    rows: TaskRecord[],
    anchor: RowAnchor | null,
    record: TaskRecord | null,
  ) {
    if (!anchor || !record || anchor.id !== record.id) return rows;
    const without = rows.filter((row) => row.id !== anchor.id);
    if (anchor.group === group)
      without.splice(Math.min(anchor.index, without.length), 0, record);
    return without;
  }
  $: openRows = rowsFor("open", items, pinned, selected);
  $: doneRows = rowsFor("done", completed, pinned, selected);
  $: removedRows = rowsFor("cancelled", removed, pinned, selected);
  $: if (
    pinned &&
    ((pinned.group === "done" && !completedOpen) ||
      (pinned.group === "cancelled" && !removedOpen))
  )
    pinned = null;
  function closeEditor() {
    editorOpen = false;
    pinned = null;
    switchTo = null;
    focusWhenReady = false;
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
  let outcome: Change["outcome"] = "open";
  let attention: Change["attention"] = "all";
  let busy = false,
    error = "",
    notice = "",
    ready = false;
  let editorOpen = false;
  let titleField: HTMLInputElement;
  let focusWhenReady = false;
  let switchDecision: HTMLDivElement;
  const editorId = `task-editor-${crypto.randomUUID()}`;
  $: showEditor = editorOpen || pending !== null;
  $: editorGroup = !selected
    ? "top"
    : openRows.some((task) => task.id === selected?.id)
      ? "open"
      : completedOpen && doneRows.some((task) => task.id === selected?.id)
        ? "done"
        : removedOpen && removedRows.some((task) => task.id === selected?.id)
          ? "cancelled"
          : "top";
  async function revealDecision() {
    editorOpen = true;
    await tick();
    switchDecision?.focus({ preventScroll: true });
    switchDecision?.scrollIntoView?.({ block: "nearest" });
  }
  let pending: Change | null = null;
  $: navigationLocked = busy || pending !== null;
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
  } = { query: "", outcome, attention, day, project: "", section: "" };
  $: filtersChanged =
    query !== applied.query ||
    outcome !== applied.outcome ||
    attention !== applied.attention ||
    day !== applied.day ||
    projectFilter !== applied.project ||
    sectionFilter !== applied.section;
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
  async function focusTitle() {
    await tick();
    titleField?.focus({ preventScroll: true });
    // Collapsing the previous row can shift the viewport. Keep the selected
    // row (or fallback heading) alongside the title, using nearest scrolling.
    const heading =
      titleField
        ?.closest("li")
        ?.querySelector<HTMLElement>(".task-row-heading") ??
      titleField
        ?.closest(".task-editor")
        ?.querySelector<HTMLElement>(".task-editor-heading");
    heading?.scrollIntoView?.({ block: "nearest" });
    titleField?.scrollIntoView?.({ block: "nearest" });
  }
  async function openEditor() {
    editorOpen = true;
    // Existing-task reads and saves keep fields disabled until their owner
    // settles. Consume one focus intent after run releases that lock.
    if (busy) focusWhenReady = true;
    else await focusTitle();
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
        };
    const sameFilters =
      filters.query === applied.query &&
      filters.outcome === applied.outcome &&
      filters.attention === applied.attention &&
      filters.day === applied.day &&
      filters.project === applied.project &&
      filters.section === applied.section;
    // Retain the already-loaded extent while the active row is anchored. A
    // first-page refresh would otherwise move a later-page row and its editor.
    const wanted = !append && pinned && sameFilters ? items.length : 0;
    const args: Change = {
      action: "list",
      outcome: filters.outcome,
      attention: filters.attention,
      query: filters.query,
      day: filters.day,
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
    if (!append && !sameFilters) pinned = null;
    items = append ? [...items, ...rows] : rows;
    next = result.next_offset;
    total = result.total;
    openTotal = result.open_total;
    applied = filters;
    listed = true;
    if (!append) {
      if (completedOpen) await listGroup("done");
      if (removedOpen) await listGroup("cancelled");
    }
  }
  async function run(work: () => Promise<void>) {
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
        await focusTitle();
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
    if (pinned && result.task.id !== pinned.id) pinned = null;
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
      placementDraft();
    }
    conflict = false;
    saveDraft();
    editorOpen = true;
  }
  function fresh() {
    if (!selected && (title || due || description)) {
      void openEditor();
      return;
    }
    pinned = null;
    selected = null;
    title = "";
    due = "";
    description = "";
    projectId = projectFilter;
    sectionId = sectionFilter;
    placementChanged = !!(projectId || sectionId);
    history = [];
    sources = [];
    notice = "";
    saveDraft();
    void openEditor();
  }
  async function accepted(
    result: { status: string; revision?: number },
    quiet = false,
  ) {
    const wasOpen = editorOpen;
    if (!quiet) notice = "Task saved.";
    if (pending?.action === "transition" && pending.outcome === "cancelled")
      pinned = null;
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
      projectId = "";
      sectionId = "";
      placementChanged = false;
      editorOpen = false;
      saveDraft();
    }
  }
  async function send(change: Change, quiet = false) {
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
    await accepted(result, quiet);
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
      editorOpen = !!(ref || title || due || description || pending);
      ready = true;
      await list();
      if (ref) {
        await read(ref, true);
        if (!placementChanged) placementDraft();
      }
      if (pending) await resolve();
    });
  });
</script>

{#snippet taskEditor()}
  <div
    class="task-editor"
    id={editorId}
    role="region"
    aria-label={selected ? "Task details" : "New task"}
  >
    <div class="task-editor-heading">
      <h3>{selected ? "Task details" : "New task"}</h3>
      <button
        class="plain"
        disabled={busy || !!pending || !!switchTo}
        on:click={closeEditor}>Close editor</button
      >
    </div>
    {#if error}<p role="alert">{error}</p>{/if}
    {#if notice}<p role="status">{notice}</p>{/if}
    {#if pending}<div class="pending">
        <p>
          The last change hasn’t been confirmed. Check its saved result before
          continuing.
        </p>
        <button disabled={busy} on:click={() => run(resolve)}
          >Check saved result</button
        >{#if absent}<button disabled={busy} on:click={() => run(retry)}
            >Retry same change</button
          ><button disabled={busy} on:click={release}
            >Keep draft and edit</button
          >{/if}
      </div>{/if}
    {#if switchTo}<div
        class="pending"
        role="alert"
        tabindex="-1"
        bind:this={switchDecision}
      >
        <p>You have unsaved changes in this task.</p>
        <button
          disabled={busy || !!pending || !canSave}
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
        ><button disabled={busy || !!pending} on:click={discardAndOpen}
          >Discard changes</button
        ><button on:click={() => (switchTo = null)}>Keep editing</button>
      </div>{/if}
    {#if selected}<div class="completion">
        {#if selected.outcome === "cancelled"}<span>Removed</span><button
            disabled={busy || !!pending || dirty}
            on:click={() => run(() => complete(selected!, false))}
            >Restore task</button
          >{:else}<label
            ><input
              type="checkbox"
              checked={selected.outcome === "done"}
              disabled={busy || !!pending || dirty}
              on:change={(event) =>
                run(() => complete(selected!, event.currentTarget.checked))}
            />{selected.outcome === "done"
              ? "Completed"
              : "Mark as done"}</label
          >{/if}
      </div>{/if}
    <form class="task-edit-form" on:submit|preventDefault={() => run(save)}>
      <label
        >Task title<input
          bind:this={titleField}
          value={title}
          on:input={(e) => editDraft("title", e.currentTarget.value)}
          required
          maxlength="500"
          disabled={busy || !!pending}
        /></label
      >
      <label
        >Due date<input
          type="date"
          value={due}
          on:input={(e) => editDraft("due", e.currentTarget.value)}
          disabled={busy || !!pending}
        /></label
      >
      <TaskDescription
        value={description}
        disabled={busy || !!pending}
        onChange={(value) => {
          description = value;
          saveDraft();
        }}
      />
      {#if workspaceSnapshot?.settings.university_enabled}<div
          class="placement"
        >
          {#if (selected?.placements?.length ?? 0) > 1}<p class="hint">
              Linked to {selected?.placements?.length} places. Choosing a new location
              replaces those links.
            </p>{/if}
          <ChoicePicker
            id="task-project"
            label="Project (optional)"
            value={projectId}
            options={[
              { value: "", label: "No project" },
              ...projects.map((row) => ({
                value: row.id,
                label: row.data.title,
              })),
            ]}
            onChange={(value) => changePlacement(value, "")}
            disabled={busy || !!pending}
          />
          <ChoicePicker
            id="task-section"
            label="Section (optional)"
            value={sectionId}
            options={[
              {
                value: "",
                label: projectId ? "Whole project" : "No section",
              },
              ...sections
                .filter((row) => (row.data.semester_id ?? "") === projectId)
                .map((row) => ({ value: row.id, label: row.data.title })),
            ]}
            onChange={(value) => changePlacement(projectId, value)}
            disabled={busy || !!pending}
          />
        </div>{/if}
      <div class="save-row">
        <button class="save" disabled={busy || !!pending || !canSave}
          >Save task</button
        >{#if dirty}<span class="hint">Unsaved changes</span>{/if}
      </div>
    </form>
    {#if conflict && selected}<button
        disabled={busy || !!pending}
        on:click={() => run(() => read(selected!.read_ref, true))}
        >Load current task, keep draft</button
      >{/if}
    {#if selected}<details class="task-evidence">
        <summary>Details</summary>
        <p class="hint">
          {selected.source_status === "manual"
            ? "Created here."
            : selected.source_status === "available"
              ? "Linked to a source conversation."
              : "The original source is no longer available."}
          {#if selected.note_status === "available"}A supporting note is linked.{:else if selected.note_status === "unavailable"}The
            linked note is no longer available.{/if}
        </p>
        {#if selected.note_id}<button
            disabled={busy || !!pending || dirty}
            on:click={() =>
              run(() =>
                send({
                  action: "save",
                  read_ref: selected!.read_ref,
                  note_id: null,
                }),
              )}>Remove note link</button
          >{/if}
        <details>
          <summary>Task history</summary>
          <ol>
            {#each history as revision}<li>
                Revision {revision.revision}: {revision.title} · {revision.outcome ===
                "cancelled"
                  ? "Removed"
                  : revision.outcome === "done"
                    ? "Done"
                    : "Open"} · {revision.due ??
                  "No due date"}{#if revision.description}<pre>{revision.description}</pre>{/if}
              </li>{/each}
          </ol>
          {#if historyNext !== null}<button
              disabled={busy}
              on:click={() => run(() => more("history", historyNext!))}
              >{historyNext === 0
                ? "Read task history"
                : "More history"}</button
            >{/if}
        </details>
        <button disabled={busy} on:click={() => run(() => more("sources"))}
          >Read supporting sources</button
        >{#each sources as source}<blockquote>{source.quote}</blockquote>
          {#if source.conversation_id}<button
              on:click={() => openConversation(source.conversation_id!)}
              >Open source conversation</button
            >{/if}{/each}{#if sourceNext !== null}<button
            disabled={busy}
            on:click={() => run(() => more("sources", sourceNext!))}
            >More sources</button
          >{/if}
        {#if selected.outcome !== "cancelled"}<div class="remove">
            <button
              disabled={busy || !!pending || dirty}
              on:click={() =>
                run(() =>
                  send({
                    action: "transition",
                    read_ref: selected!.read_ref,
                    outcome: "cancelled",
                  }),
                )}>Remove task</button
            ><span class="hint">You can restore it from Removed tasks.</span>
          </div>{/if}
      </details>{/if}
  </div>
{/snippet}

<section class="tasks planning-page" aria-label="Personal tasks">
  <div class="task-heading">
    <h2 class="settings-title planning-title">Personal tasks</h2>
    <button
      class="task-add"
      aria-expanded={showEditor && !selected}
      aria-controls={showEditor && !selected ? editorId : undefined}
      disabled={busy || !!pending || !ready}
      on:click={() => requestOpen("new")}
      ><Icon name="plus" size={14} />New task</button
    >
  </div>
  <p class="panel-intro">Your next steps, with room for the details.</p>
  {#if !showEditor}
    {#if error}<p role="alert">{error}</p>{/if}
    {#if notice}<p role="status">{notice}</p>{/if}
  {/if}
  {#if !editorOpen && (selected || title || due || description)}<button
      class="plain task-resume"
      disabled={busy || !!pending}
      on:click={openEditor}
      >{selected ? "Return to task" : "Continue draft"}{#if title}
        · {title}{/if}</button
    >{/if}
  {#if showEditor && editorGroup === "top"}
    {#if selected}<p class="hint">
        This task is outside the visible list. Its details and your draft remain
        here.
      </p>{/if}
    {@render taskEditor()}
  {/if}
  <form class="task-filters" on:submit|preventDefault={() => run(() => list())}>
    <label class="task-search"
      >Find tasks<input
        type="search"
        bind:value={query}
        maxlength="256"
        placeholder="Search by title…"
      /></label
    >
    {#if workspaceSnapshot?.settings.university_enabled}<ChoicePicker
        id="task-project-filter"
        label="Project"
        value={projectFilter}
        options={[
          { value: "", label: "All projects" },
          ...projects.map((row) => ({ value: row.id, label: row.data.title })),
        ]}
        onChange={(value) => {
          projectFilter = value;
          sectionFilter = "";
        }}
        disabled={busy || !!pending}
      />{/if}
    <button disabled={busy || !!pending}>Apply filters</button>
  </form>
  {#if sectionFilter}<p class="scope">
      Section: {sections.find((row) => row.id === sectionFilter)?.data.title ||
        "Selected section"}
      <button
        class="plain"
        disabled={busy || !!pending}
        on:click={() => {
          sectionFilter = "";
          void run(() => list());
        }}>Show whole project</button
      >
    </p>{/if}
  <div class="task-list-heading">
    <span><strong>{total}</strong> open {total === 1 ? "task" : "tasks"}</span
    ><button
      class="plain"
      disabled={busy || !!pending}
      on:click={() => run(() => list())}
      ><Icon name="refresh" size={13} />Refresh tasks</button
    >
  </div>
  {#if filtersChanged}<p class="hint">
      Apply filters to update this list.
    </p>{/if}
  <TaskRows
    items={openRows}
    disabled={busy || !!pending}
    completionDisabled={dirty}
    selectedId={showEditor && editorGroup === "open"
      ? (selected?.id ?? "")
      : ""}
    editor={editorGroup === "open" ? taskEditor : undefined}
    {editorId}
    onOpen={requestOpen}
    onComplete={(task, done) => run(() => complete(task, done))}
    {placementLabel}
  />
  {#if listed && !openRows.length}<p class="empty">
      {query || projectFilter || sectionFilter
        ? "No open tasks match these filters."
        : "Nothing to do here yet. Add a task when you need one."}
    </p>{/if}
  {#if next !== null}<button
      class="plain"
      disabled={busy || !!pending || filtersChanged}
      on:click={() => run(() => list(true))}>More tasks</button
    >{/if}
  <details
    class="finished"
    bind:open={completedOpen}
    on:toggle={() => {
      if (completedOpen && !busy) void run(() => listGroup("done"));
    }}
  >
    <summary>Completed</summary><TaskRows
      items={doneRows}
      selectedId={showEditor && editorGroup === "done"
        ? (selected?.id ?? "")
        : ""}
      editor={editorGroup === "done" ? taskEditor : undefined}
      {editorId}
      disabled={busy || !!pending}
      completionDisabled={dirty}
      onOpen={requestOpen}
      onComplete={(task, done) => run(() => complete(task, done))}
      {placementLabel}
    />{#if !doneRows.length}<p class="hint">
        No completed tasks in this view.
      </p>{/if}{#if completedNext !== null}<button
        disabled={busy}
        on:click={() => run(() => listGroup("done", true))}
        >More completed tasks</button
      >{/if}
  </details>
  <details
    class="removed"
    bind:open={removedOpen}
    on:toggle={() => {
      if (removedOpen && !busy) void run(() => listGroup("cancelled"));
    }}
  >
    <summary>Removed tasks</summary><TaskRows
      items={removedRows}
      selectedId={showEditor && editorGroup === "cancelled"
        ? (selected?.id ?? "")
        : ""}
      editor={editorGroup === "cancelled" ? taskEditor : undefined}
      {editorId}
      disabled={busy || !!pending}
      onOpen={requestOpen}
      onComplete={(task, done) => run(() => complete(task, done))}
      {placementLabel}
    />{#if !removedRows.length}<p class="hint">
        No removed tasks in this view.
      </p>{/if}{#if removedNext !== null}<button
        disabled={busy}
        on:click={() => run(() => listGroup("cancelled", true))}
        >More removed tasks</button
      >{/if}
  </details>
  {#if onAccess}<div class="task-footer">
      <button
        class="plain"
        disabled={navigationLocked || ownerBusy}
        on:click={() => onAccess?.()}>Model permissions</button
      >
    </div>{/if}
</section>

<style>
  .tasks {
    max-width: 60rem;
    min-width: 0;
  }
  .task-heading,
  .task-editor-heading,
  .task-list-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .task-add,
  .task-list-heading button {
    display: inline-flex;
    align-items: center;
    gap: 7px;
  }
  .task-filters {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(140px, 200px) auto;
    align-items: end;
    gap: 12px;
    margin: 0 0 22px;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  input {
    width: 100%;
    box-sizing: border-box;
  }
  .task-list-heading {
    padding-bottom: 10px;
    border-bottom: 1px solid var(--ke-border);
    color: var(--ke-text-secondary);
  }
  .task-list-heading strong {
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .task-editor {
    margin: 24px 0;
    padding: 24px;
    background: var(--ke-surface-inset);
    border-radius: var(--ke-radius-sm);
  }
  .task-editor-heading h3 {
    margin: 0;
    font-size: 16px;
  }
  .task-edit-form {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 160px;
    gap: 18px;
    margin-top: 18px;
  }
  .placement {
    grid-column: 1/-1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .placement p {
    grid-column: 1/-1;
  }
  .save-row {
    grid-column: 1/-1;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .tasks button.save {
    background: var(--ke-accent);
    color: var(--ke-on-accent);
  }
  .hint,
  .scope {
    color: var(--ke-text-secondary);
    font-size: 12px;
  }
  .completion {
    margin-top: 16px;
  }
  .completion label {
    flex-direction: row;
    align-items: center;
    gap: 9px;
    color: var(--ke-text-primary);
  }
  .completion input {
    width: 17px;
    height: 17px;
    accent-color: var(--ke-accent);
  }
  .finished,
  .removed {
    margin-top: 20px;
  }
  .removed {
    color: var(--ke-text-secondary);
  }
  summary {
    cursor: pointer;
  }
  .task-evidence {
    margin-top: 22px;
    border-top: 1px solid var(--ke-border);
    padding-top: 14px;
  }
  .task-evidence pre,
  blockquote {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .remove {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 15px;
  }
  .empty {
    padding: 24px 10px;
    color: var(--ke-text-secondary);
  }
  .task-footer,
  .task-resume {
    margin-top: 22px;
  }
  .pending {
    border-left: 3px solid var(--ke-border-strong);
    padding-left: 12px;
    margin-bottom: 20px;
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
  @media (max-width: 680px) {
    .task-filters {
      grid-template-columns: 1fr auto;
    }
    .task-search {
      grid-column: 1/-1;
    }
    .task-editor {
      padding: 18px 14px;
    }
    .task-edit-form,
    .placement {
      grid-template-columns: 1fr;
    }
  }
</style>
