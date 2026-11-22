<script lang="ts">
  import { onMount } from "svelte";
  import ProjectTasks from "./ProjectTasks.svelte";
  import ProjectConversations from "./ProjectConversations.svelte";
  import { dayInZone } from "./calendar-view";
  export let active = true;
  export let onConversation: (id: string) => void = () => {};
  /** A new conversation in a project, or in one of its modules (its
   *  documents then sit beside the chat). */
  export let onNewConversation: (
    projectId: string,
    moduleId?: string,
  ) => void = () => {};
  import "$lib/design/collection.css";
  import Icon from "$lib/design/Icon.svelte";
  import { toPageTop } from "$lib/design/collection";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import WorkspaceNotice from "./WorkspaceNotice.svelte";
  import { drawerFocus } from "./drawer";
  import ProjectFolderPicker from "./ProjectFolderPicker.svelte";
  import EventEditor from "./EventEditor.svelte";
  import Timeline from "$lib/design/Timeline.svelte";
  import { courseTimeline } from "./course-timeline";
  import {
    localDate,
    workspaceRequest,
    type Workspace,
    type Semester,
    type Course,
    type Event,
    type RecordRow,
  } from "./workspace";
  export let workspace: Workspace;
  export let busy = false;
  export let projectId = "";
  export let onTasks: (id: string) => void = () => {};
  /** A plain Projects visit shows the projects list, modules closed; a
   *  counted visit with a target applies it again even if it is unchanged. */
  export let listVisit = 0;
  export let scopeVisit = 0;
  let appliedListVisit = listVisit,
    appliedScopeVisit = scopeVisit;
  let shownProject = "";
  // "module:<id>" opens that module's project page with the module expanded.
  let requestedModule = "";
  $: if (projectId !== shownProject || scopeVisit !== appliedScopeVisit) {
    shownProject = projectId;
    appliedScopeVisit = scopeVisit;
    if (projectId.startsWith("module:")) requestedModule = projectId.slice(7);
    else if (projectId) selectedSemester = projectId;
  }
  $: if (listVisit !== appliedListVisit) {
    appliedListVisit = listVisit;
    selectedSemester = "";
    expanded = new Set();
  }
  // There is no list page: Projects shows the project opened last, else the
  // first active one; the other projects sit in the list beside it.
  // Remembered on this computer, so a restart opens the same project. Storage
  // is a convenience: without it the choice lasts while Kejtu runs.
  const LAST_PROJECT = "kejtu.projects.last";
  let lastProject = "";
  try {
    lastProject = window.localStorage.getItem(LAST_PROJECT) || "";
  } catch {
    // No storage: the first active project opens.
  }
  $: if (selectedSemester) remember(selectedSemester);
  function remember(id: string) {
    if (id === lastProject) return;
    lastProject = id;
    try {
      window.localStorage.setItem(LAST_PROJECT, id);
    } catch {
      // As above.
    }
  }
  $: if (!selectedSemester && $workspace.snapshot)
    selectedSemester = firstProject(projects, activeProjects, ungrouped);
  function firstProject(
    all: RecordRow<Semester>[],
    open: RecordRow<Semester>[],
    loose: RecordRow<Course>[],
  ) {
    const known = (id: string) =>
      id === "ungrouped" ? loose.length > 0 : all.some((row) => row.id === id);
    if (known(lastProject)) return lastProject;
    return (open[0] ?? all[0])?.id || (loose.length ? "ungrouped" : "");
  }
  $: if (requestedModule && $workspace.snapshot) {
    const row = $workspace.snapshot.courses.find(
      (item) => item.id === requestedModule,
    );
    if (row) openModule(row);
    requestedModule = "";
  }
  export let onTask: (ref: string) => void = () => {};
  /** Reserve time for a module in Calendar (the work-session flow). */
  export let onReserve: (value: Partial<Event>) => void = () => {};
  /** The module whose documents the sidebar shows: the last module opened on
   *  this project page, when it has a folder; "" otherwise. */
  export let onFilesModule: (id: string) => void = () => {};
  let editing: RecordRow<Semester> | null = null;
  let editor: "semester" | "course" | "" = "";
  let selectedSemester = "";
  const dateFormat = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
  // Semester dates are civil dates, independent of the computer's timezone.
  function dateLabel(day: string | null) {
    if (!day) return "No start date";
    return dateFormat.format(new Date(day + "T12:00:00Z"));
  }
  function tone(id: string) {
    const index =
      [...id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4;
    return ["sage", "blue", "sand", "rose"][index];
  }
  $: semesterOptions = ($workspace.snapshot?.semesters ?? []).map((row) => ({
    value: row.id,
    label: row.data.title,
    detail: period(row),
  }));
  $: courses =
    $workspace.snapshot?.courses.filter(
      (row) =>
        !selectedSemester ||
        (selectedSemester === "ungrouped"
          ? row.data.semester_id === null
          : row.data.semester_id === selectedSemester),
    ) ?? [];
  $: if (
    $workspace.snapshot &&
    selectedSemester &&
    selectedSemester !== "ungrouped" &&
    !$workspace.snapshot.semesters.some((row) => row.id === selectedSemester)
  ) {
    selectedSemester = "";
  }
  $: currentSemester = $workspace.snapshot?.semesters.find(
    (row) => row.id === selectedSemester,
  );
  // The landing page: projects as cards, each with its modules one click away.
  $: projects = $workspace.snapshot?.semesters ?? [];
  $: allCourses = $workspace.snapshot?.courses ?? [];
  $: ungrouped = allCourses.filter((row) => row.data.semester_id === null);
  // A project whose end date has passed is finished; it stays one pill away.
  // "Passed" is judged by the civil date in the project's own timezone, and
  // read again as time goes by: each minute, and when the page is returned to.
  let now = Date.now();
  const reread = () => (now = Date.now());
  onMount(() => {
    const timer = setInterval(reread, 60_000);
    window.addEventListener("focus", reread);
    document.addEventListener("visibilitychange", reread);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", reread);
      document.removeEventListener("visibilitychange", reread);
    };
  });
  $: if (active) reread();
  function over(row: RecordRow<Semester>, at: number) {
    if (!row.data.ends_on) return false;
    let day: string;
    try {
      day = dayInZone(new Date(at), row.data.timezone);
    } catch {
      // A timezone name the computer does not know: its own date stands in.
      day = localDate(new Date(at));
    }
    return row.data.ends_on < day;
  }
  $: finished = projects.filter((row) => over(row, now));
  $: activeProjects = projects.filter((row) => !finished.includes(row));
  let showFinished = false;
  $: if (showFinished && !finished.length) showFinished = false;
  // Opening a finished project (from the chooser, or remembered) unfolds them.
  let foldedFor = "";
  $: if (selectedSemester !== foldedFor) {
    foldedFor = selectedSemester;
    if (finished.some((row) => row.id === selectedSemester))
      showFinished = true;
  }
  function period(row: RecordRow<Semester>) {
    return !row.data.starts_on && !row.data.ends_on
      ? "Ongoing project"
      : `${dateLabel(row.data.starts_on)} – ${row.data.ends_on ? dateLabel(row.data.ends_on) : "Open-ended"}`;
  }
  const count = (n: number, word: string) =>
    `${n} ${word}${n === 1 ? "" : "s"}`;
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const dayMonth = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  const short = (day: string) => dayMonth.format(new Date(day + "T12:00:00Z"));
  // Only the break that is on now or comes next; the editor lists them all.
  function nextBreak(row: RecordRow<Semester>, at: number) {
    let today: string;
    try {
      today = dayInZone(new Date(at), row.data.timezone);
    } catch {
      today = localDate(new Date(at));
    }
    const next = [...row.data.breaks]
      .sort((a, b) => a.starts_on.localeCompare(b.starts_on))
      .find((item) => item.ends_on >= today);
    if (!next) return [];
    return [
      next.starts_on <= today
        ? `On break until ${short(next.ends_on)}`
        : `Break ${short(next.starts_on)} – ${short(next.ends_on)}`,
    ];
  }
  // What the side column used to list, in one line under the title. The
  // timezone shows only when it is not this computer's.
  function facts(row: RecordRow<Semester>, at: number) {
    return [
      period(row),
      ...nextBreak(row, now),
      count(courses.length, "module"),
      ...(row.data.timezone !== localZone ? [row.data.timezone] : []),
    ].join(" · ");
  }
  // A module opens in place on its project page: its schedule, and ways to
  // add work time or an event. There is no separate module page.
  let expanded = new Set<string>();
  let meetings: RecordRow<Event>[] = [],
    eventNext: number | null = null,
    eventsRead = false,
    eventsLoading = false,
    eventError = "";
  let eventEditor = false,
    editingEvent: RecordRow<Event> | null = null,
    eventCourse: RecordRow<Course> | null = null;
  async function loadEvents(append = false) {
    if (eventsLoading) return;
    eventsLoading = true;
    eventError = "";
    try {
      const page = await workspaceRequest<{
        items: RecordRow<Event>[];
        next_offset: number | null;
      }>("record.page", {
        kind: "event",
        limit: 100,
        offset: append ? (eventNext ?? 0) : 0,
      });
      meetings = append ? [...meetings, ...page.items] : page.items;
      eventNext = page.next_offset;
      eventsRead = true;
    } catch (caught) {
      eventError = caught instanceof Error ? caught.message : String(caught);
    } finally {
      eventsLoading = false;
    }
  }
  function openProject(id: string) {
    if (id === selectedSemester) return;
    selectedSemester = id;
    conversations = false;
    expanded = new Set();
  }
  function toggleModule(id: string) {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expanded = next;
    if (next.has(id) && !eventsRead) void loadEvents();
  }
  function openModule(row: RecordRow<Course>) {
    selectedSemester = row.data.semester_id || "ungrouped";
    // Re-adding moves it to the end: it is the module opened last.
    const next = new Set(expanded);
    next.delete(row.id);
    expanded = next.add(row.id);
    void loadEvents();
  }
  // Sets keep insertion order, so the last open module on this page is the one
  // opened last. Its files follow it into the sidebar, as on the old module page.
  $: lastOpen = selectedSemester
    ? [...expanded]
        .reverse()
        .map((id) => courses.find((row) => row.id === id))
        .find((row) => row)
    : undefined;
  $: onFilesModule(
    lastOpen?.data.folder && !lastOpen.cancelled ? lastOpen.id : "",
  );
  // Returning to the page reads the schedule again (Calendar may have changed it).
  let wasActive = active;
  $: if (active !== wasActive) {
    wasActive = active;
    if (active && expanded.size) void loadEvents();
  }
  function newEvent(row: RecordRow<Course>) {
    eventCourse = row;
    editingEvent = null;
    eventEditor = true;
  }
  function editEvent(row: RecordRow<Event>, course: RecordRow<Course>) {
    eventCourse = course;
    editingEvent = row;
    eventEditor = true;
  }
  // A project (or the modules without one) has its own page.
  let root: HTMLElement;
  let shownPage = "";
  let conversations = false;
  $: if (selectedSemester !== shownPage) {
    shownPage = selectedSemester;
    conversations = false;
    toPageTop(root);
  }
  let semester: Semester = {
    title: "",
    starts_on: null,
    ends_on: null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    breaks: [],
  };
  let course: Omit<Course, "semester_id"> & { semester_id: string } = {
    title: "",
    semester_id: "",
    folder: null,
  };
  let editingCourse: RecordRow<Course> | null = null;
  let choosingFolder = false;
  function edit(row: RecordRow<Semester> | null) {
    choosingFolder = false;
    editor = "semester";
    editing = row;
    semester = row
      ? structuredClone(row.data)
      : {
          ...semester,
          title: "",
          starts_on: null,
          ends_on: null,
          breaks: [],
        };
  }
  function editCourse(row: RecordRow<Course> | null, parent = "") {
    choosingFolder = false;
    editor = "course";
    editingCourse = row;
    course = row
      ? { ...row.data, semester_id: row.data.semester_id || "" }
      : { title: "", semester_id: parent, folder: null };
  }
  async function saveSemester() {
    const result = await workspace.mutate("record.save", {
      kind: "semester",
      data: {
        ...semester,
        starts_on: semester.starts_on || null,
        ends_on: semester.ends_on || null,
      },
      ...(editing
        ? { id: editing.id, expected_revision: editing.revision }
        : {}),
    });
    if (result) {
      editor = "";
      selectedSemester = result.id || selectedSemester;
      editing = null;
      semester = { ...semester, title: "", breaks: [] };
    }
  }
  async function saveCourse() {
    if (locked || !course.title.trim()) return;
    const result = await workspace.mutate("record.save", {
      kind: "course",
      data: {
        ...course,
        semester_id: course.semester_id || null,
        folder: course.folder?.trim() || null,
      },
      ...(editingCourse
        ? { id: editingCourse.id, expected_revision: editingCourse.revision }
        : {}),
    });
    if (result) {
      editor = "";
      selectedSemester = course.semester_id;
      editingCourse = null;
      course = { ...course, title: "", folder: null };
    }
  }
  $: locked = busy || $workspace.busy || !!$workspace.pending || choosingFolder;
</script>

{#snippet pencil()}
  <svg
    width="13"
    height="13"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    stroke-width="1.3"
    aria-hidden="true"><path d="m4 12 9-9 4 4-9 9-5 1 1-5ZM11 5l4 4" /></svg
  >
{/snippet}

{#snippet modules()}
  <div class="course-list-heading">
    <h2 class="section-title">Modules</h2>
    <button
      disabled={locked}
      on:click={() => editCourse(null, currentSemester?.id || "")}
      >Add module</button
    >
  </div>
  <section class="course-grid" aria-label="Modules">
    {#each courses as item (item.id)}
      {@const open = expanded.has(item.id)}
      <article
        class="course-card"
        class:expanded={open}
        style={`--course-tone: var(--ke-marker-${tone(item.id)})`}
      >
        <button
          class="course-open"
          disabled={locked}
          aria-expanded={open}
          aria-controls={`module-schedule-${item.id}`}
          aria-label={`${item.data.title} — ${open ? "Hide" : "Show"} schedule`}
          on:click={() => toggleModule(item.id)}
        >
          <span class="mark" aria-hidden="true"
            >{item.data.title.charAt(0)}</span
          >
          <span class="names">
            <span class="title">{item.data.title}</span>
            <span class="sub"
              >{item.data.folder || "No resources folder yet"}</span
            >
          </span>
        </button>
        <button
          class="cp-icon course-edit"
          title={`Edit ${item.data.title}`}
          aria-label={`Edit ${item.data.title}`}
          disabled={locked}
          on:click={() => editCourse(item)}>{@render pencil()}</button
        >
        {#if open}{@const entries = courseTimeline(meetings, item.id)}
          <div class="module-schedule" id={`module-schedule-${item.id}`}>
            <div class="module-actions">
              <button
                class="mini"
                disabled={locked}
                on:click={() =>
                  onReserve({
                    kind: "study",
                    title: `${item.data.title} · work session`,
                    course_id: item.id,
                    material: [],
                  })}>Schedule work time</button
              >
              <button
                class="mini"
                disabled={locked}
                on:click={() => newEvent(item)}>Add event</button
              >
              <button
                class="mini"
                disabled={locked}
                aria-label={`New conversation in ${item.data.title}`}
                on:click={() =>
                  onNewConversation(item.data.semester_id || "", item.id)}
                >Conversation</button
              >
            </div>
            {#if entries.length}
              <Timeline
                compact
                items={entries}
                label={`${item.data.title} schedule`}
              >
                <svelte:fragment slot="actions" let:item={entry}>
                  {@const row = meetings.find((row) => row.id === entry.id)}
                  {#if row}<button
                      class="timeline-edit"
                      title={`Edit ${row.data.repeat === "none" ? "event" : "series"}: ${entry.title}`}
                      aria-label={`Edit ${row.data.repeat === "none" ? "event" : "series"}: ${entry.title}`}
                      disabled={locked}
                      on:click={() => editEvent(row, item)}
                      >{@render pencil()}</button
                    >{/if}
                </svelte:fragment>
              </Timeline>
            {:else if eventError}<p class="cp-hint" role="alert">
                {eventError}
              </p>
            {:else if eventsLoading || !eventsRead}<p
                class="cp-hint cp-late"
                role="status"
              >
                Reading schedule…
              </p>
            {:else}<p class="cp-hint">
                No events, work sessions or deadlines yet.
              </p>{/if}
            {#if eventNext !== null}<button
                class="mini"
                disabled={eventsLoading}
                on:click={() => loadEvents(true)}>More schedule entries</button
              >{/if}
          </div>{/if}
      </article>
    {:else}<p class="cp-empty">
        {currentSemester
          ? "Add a module when you need to divide this project."
          : "Create a project, then add modules when useful."}
      </p>{/each}
  </section>
{/snippet}

{#snippet projectRow(row: RecordRow<Semester>)}<li>
    <button
      class="project-row"
      class:current={row.id === selectedSemester}
      aria-current={row.id === selectedSemester ? "page" : undefined}
      disabled={locked}
      on:click={() => openProject(row.id)}>{row.data.title}</button
    >
  </li>{/snippet}

{#snippet projectChooser()}
  <div class="project-chooser">
    <ChoicePicker
      chip
      arrow={false}
      hideLabel
      id="project-chooser"
      label="Project"
      value={selectedSemester}
      disabled={locked}
      options={[
        ...activeProjects.map((row) => ({
          value: row.id,
          label: row.data.title,
          shortLabel: "Projects",
        })),
        ...(ungrouped.length
          ? [
              {
                value: "ungrouped",
                label: "Without a project",
                shortLabel: "Projects",
              },
            ]
          : []),
        ...finished.map((row) => ({
          value: row.id,
          label: row.data.title,
          shortLabel: "Projects",
          detail: "Finished",
        })),
      ]}
      onChange={openProject}
    />
  </div>
{/snippet}

{#snippet projectList()}
  <nav class="cp-side project-list" aria-label="Projects">
    <div class="list-head">
      <h2 class="section-title">Projects</h2>
    </div>
    <ul>
      {#each activeProjects as row (row.id)}{@render projectRow(row)}{/each}
      {#if ungrouped.length}<li>
          <button
            class="project-row"
            class:current={selectedSemester === "ungrouped"}
            aria-current={selectedSemester === "ungrouped" ? "page" : undefined}
            disabled={locked}
            on:click={() => openProject("ungrouped")}>Without a project</button
          >
        </li>{/if}
    </ul>
    {#if finished.length}<button
        class="finished-toggle"
        aria-expanded={showFinished}
        on:click={() => (showFinished = !showFinished)}
        >Finished <span class="cp-count">{finished.length}</span></button
      >{#if showFinished}<ul>
          {#each finished as row (row.id)}{@render projectRow(row)}{/each}
        </ul>{/if}{/if}
  </nav>
{/snippet}

<div class="uni course-directory">
  <div class="collection-page" bind:this={root}>
    {#if currentSemester}
      <!-- The same head as the other pages: title, then the line under it
           (here the way back to Projects), then the bar. -->
      <div class="cp-head project-head">
        <div class="project-heading">
          <div class="project-names">
            <h1 class="cp-title">{currentSemester.data.title}</h1>
            <p class="cp-status">{facts(currentSemester, now)}</p>
          </div>
        </div>
        <div class="head-actions">
          <button
            class="cp-icon"
            disabled={locked}
            title="Edit project"
            aria-label="Edit project"
            on:click={() => edit(currentSemester || null)}
            >{@render pencil()}</button
          >
          <!-- In the head's place where Research and Tasks keep theirs. -->
          <button
            class="cp-action"
            disabled={locked}
            on:click={() => edit(null)}>New project</button
          >
        </div>
      </div>
      <div class="cp-bar">
        <nav class="cp-pills" aria-label="Project views">
          <button
            class="cp-pill"
            aria-pressed={!conversations}
            on:click={() => (conversations = false)}>Overview</button
          >
          <button
            class="cp-pill"
            aria-pressed={conversations}
            on:click={() => (conversations = true)}>Conversations</button
          >
        </nav>
        {@render projectChooser()}
      </div>
      <div class="cp-item">
        <div class="cp-main">
          {#if conversations}
            <ProjectConversations
              {workspace}
              projectId={currentSemester.id}
              {active}
              disabled={locked}
              onOpen={onConversation}
              onCreate={onNewConversation}
            />
          {:else}
            <section class="cp-section">{@render modules()}</section>
            <ProjectTasks
              projectId={currentSemester.id}
              {active}
              disabled={locked}
              onOpen={onTask}
              onAll={() => onTasks(currentSemester!.id)}
              modules={courses.map((row) => ({
                id: row.id,
                title: row.data.title,
              }))}
            />
          {/if}
        </div>
        {@render projectList()}
      </div>
    {:else if selectedSemester === "ungrouped" && projects.length}
      <div class="cp-head">
        <div>
          <h1 class="cp-title">Modules without a project</h1>
          <p class="cp-status">{count(courses.length, "module")}</p>
        </div>
        <button class="cp-action" disabled={locked} on:click={() => edit(null)}
          >New project</button
        >
      </div>
      <div class="cp-bar">{@render projectChooser()}</div>
      <div class="cp-item">
        <div class="cp-main">
          <section class="cp-section">{@render modules()}</section>
        </div>
        {@render projectList()}
      </div>
    {:else if selectedSemester === "ungrouped" || !projects.length}
      <div class="cp-head">
        <div>
          <h1 class="cp-title">Your projects</h1>
          <p class="cp-status">
            Conversations, resources and tasks, gathered around what you’re
            working on.
          </p>
        </div>
        <button class="cp-action" disabled={locked} on:click={() => edit(null)}
          ><Icon name="plus" size={14} />New project</button
        >
      </div>
      {@render modules()}
    {/if}
  </div>
  {#if eventEditor && eventCourse}<EventEditor
      {workspace}
      busy={locked}
      record={editingEvent}
      initial={editingEvent
        ? {}
        : {
            course_id: eventCourse.id,
            timezone:
              $workspace.snapshot?.semesters.find(
                (row) => row.id === eventCourse?.data.semester_id,
              )?.data.timezone ||
              Intl.DateTimeFormat().resolvedOptions().timeZone ||
              "UTC",
            kind: "appointment",
          }}
      onCancel={() => (eventEditor = false)}
      onDone={() => {
        eventEditor = false;
        void loadEvents();
      }}
    />{/if}
  {#if editor}<div
      class="drawer"
      role="dialog"
      aria-label={editor === "semester" ? "Project editor" : "Module editor"}
      tabindex="-1"
      use:drawerFocus={{ close: () => (editor = ""), busy: locked }}
    >
      <button
        class="close-editor pencil-action"
        disabled={locked}
        title="Close editor"
        aria-label="Close editor"
        on:click={() => (editor = "")}
        ><svg
          width="14"
          height="14"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          stroke-width="1.3"
          aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg
        ></button
      >
      <!-- Recovery remains reachable while an uncertain save locks the form. -->
      <WorkspaceNotice {workspace} />
      <fieldset class="editor-fields" disabled={locked}>
        {#if editor === "semester"}
          <form on:submit|preventDefault={saveSemester}>
            <div class="row spread">
              <h2>{editing ? "Edit project" : "New project"}</h2>
              {#if editing}<button
                  class="workspace-action"
                  type="button"
                  on:click={() => edit(null)}>New instead</button
                >{/if}
            </div>
            <label
              >Name<input
                required
                maxlength="300"
                bind:value={semester.title}
              /></label
            >
            <details class="project-schedule">
              <summary>Schedule (optional)</summary>
              <div class="row">
                <label
                  >Start date (optional)<input
                    type="date"
                    bind:value={semester.starts_on}
                  /></label
                ><label
                  >End date (optional)<input
                    type="date"
                    bind:value={semester.ends_on}
                    aria-describedby="period-end-hint"
                  /></label
                >
              </div>
              <p class="muted" id="period-end-hint">
                Dates and breaks are optional. Leave them blank for an ongoing
                project.
              </p>
              <label
                >Timezone<input
                  required
                  bind:value={semester.timezone}
                /></label
              >
              <fieldset>
                <legend>Breaks</legend>
                {#each semester.breaks as item, index}
                  <div class="row">
                    <label
                      >From<input
                        type="date"
                        required
                        bind:value={item.starts_on}
                      /></label
                    ><label
                      >Through<input
                        type="date"
                        required
                        bind:value={item.ends_on}
                      /></label
                    ><button
                      class="workspace-action"
                      type="button"
                      on:click={() =>
                        (semester.breaks = semester.breaks.filter(
                          (_, i) => i !== index,
                        ))}
                      aria-label={`Remove break ${index + 1}`}>Remove</button
                    >
                  </div>
                {/each}
                <button
                  class="workspace-action"
                  type="button"
                  on:click={() =>
                    (semester.breaks = [
                      ...semester.breaks,
                      {
                        starts_on: semester.starts_on || localDate(),
                        ends_on: semester.starts_on || localDate(),
                      },
                    ])}>Add break</button
                >
              </fieldset>
            </details>
            <button
              class="workspace-action save-action"
              disabled={locked || !semester.title.trim()}>Save project</button
            >
          </form>
        {:else}
          <form on:submit|preventDefault={saveCourse}>
            <h2>{editingCourse ? "Edit module" : "New module"}</h2>
            <ChoicePicker
              id="course-semester"
              label="Project (optional)"
              options={[
                { value: "", label: "Modules without a project" },
                ...semesterOptions,
              ]}
              bind:value={course.semester_id}
              disabled={locked}
              placeholder="No project"
            />
            <label
              >Module name<input
                required
                maxlength="300"
                bind:value={course.title}
              /></label
            >
            {#key editingCourse?.id}
              <ProjectFolderPicker
                bind:value={course.folder}
                bind:picking={choosingFolder}
                disabled={locked}
              />
            {/key}
            <button
              class="workspace-action save-action"
              disabled={locked || !course.title.trim()}>Save module</button
            >
          </form>
        {/if}
      </fieldset>
    </div>{/if}
</div>

<style>
  .course-list-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 24px 0 8px;
  }
  .course-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
  .course-card {
    min-width: 0;
    border-top: 1px solid var(--ke-border);
  }
  .course-card {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 9px 0;
  }
  /* An expanded module: its schedule under the row, a step smaller. */
  .module-schedule {
    flex: 1 0 100%;
    box-sizing: border-box;
    min-width: 0;
    padding: 2px 0 6px 42px;
  }
  .module-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .module-schedule .cp-hint {
    margin-top: 8px;
  }
  .module-schedule > .mini {
    margin-top: 4px;
  }
  .timeline-edit {
    min-width: 28px;
    min-height: 28px;
    justify-content: center;
  }
  .course-directory .section-title {
    margin: 0;
    font: 500 var(--ke-text-sm)/1.5 var(--ke-font-ui);
  }
  .cp-main .course-list-heading {
    margin-top: 0;
  }
  .course-directory .course-open {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 10px;
    min-width: 0;
    min-height: 32px;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    text-align: left;
  }
  .course-directory .course-open:hover:not(:disabled) {
    background: transparent;
  }
  .mark {
    display: grid;
    flex: none;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 9px;
    background: color-mix(in srgb, var(--course-tone) 15%, transparent);
    color: var(--ke-text-secondary);
    font: 16px/1 var(--ke-font-heading);
  }
  .names {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .title,
  .sub {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    font-size: var(--ke-text-sm);
    font-weight: 500;
  }
  .sub {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .course-directory .course-edit {
    width: 26px;
    min-height: 26px;
    margin: 0;
  }
  .course-directory .mini {
    min-height: 24px;
    padding: 0 9px;
    border-radius: 6px;
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: 11.5px;
    font-weight: 500;
    white-space: nowrap;
  }
  .course-directory .mini:hover:not(:disabled) {
    background: var(--ke-border-strong);
  }
  .project-heading {
    display: flex;
    flex: 1 1 240px;
    align-items: center;
    gap: 14px;
    min-width: 0;
  }
  .project-names {
    min-width: 0;
  }
  .project-names .cp-title {
    overflow-wrap: anywhere;
  }

  .head-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
  }
  /* The other projects sit beside the open one; where the page is too narrow
     for a side column they move into a chooser beside the tabs. */
  .course-directory .collection-page {
    container: project-page / inline-size;
  }
  .project-chooser {
    display: none;
    margin-left: auto;
  }
  /* It sits at the right edge: its menu opens leftwards, inside the page. */
  .project-chooser :global(.choice-popup) {
    right: 0;
    left: auto;
    transform-origin: top right;
  }
  @container project-page (max-width: 569px) {
    .project-chooser {
      display: block;
    }
    .collection-page .project-list {
      display: none;
    }
  }
  /* Quiet rows like the sidebar's. */
  .project-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .project-list ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  /* Titled like Modules and as tall as its row, so the two titles line up. */
  .project-list .list-head {
    display: flex;
    align-items: center;
    min-height: var(--ke-control-height);
    margin-bottom: 8px;
    padding: 0 8px;
  }
  .project-row,
  .finished-toggle {
    display: block;
    width: 100%;
    min-height: 30px;
    margin: 1px 0;
    padding: 5px 8px;
    overflow: hidden;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
    text-align: left;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
  }
  .project-row:hover:not(:disabled):not(.current),
  .finished-toggle:hover:not(:disabled) {
    background: transparent;
    color: var(--ke-text-primary);
  }
  .project-row.current {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .finished-toggle {
    margin-top: 6px;
    font-size: var(--ke-text-xs);
  }
  .course-directory .pencil-action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .course-directory .pencil-action:hover:not(:disabled) {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .course-directory .close-editor {
    display: flex;
    margin-left: auto;
    margin-bottom: 18px;
  }
  fieldset {
    border: 0;
    border-top: 1px solid var(--ke-border);
    padding: 16px 0 0;
    min-width: 0;
  }
  fieldset.editor-fields {
    border: 0;
    padding: 0;
    margin: 0;
  }
  legend {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
    padding-right: 8px;
  }
  .editor-fields label {
    font-size: var(--ke-text-2xs);
  }
  .course-directory .editor-fields input {
    min-height: 36px;
    padding: 8px 10px;
    border: 0;
    border-bottom: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm) var(--ke-radius-sm) 0 0;
    background: color-mix(in srgb, var(--ke-marker-sand) 5%, transparent);
    font-size: var(--ke-text-sm);
  }
  .editor-fields .save-action {
    justify-self: start;
    margin-top: 10px;
  }
  .editor-fields h2 {
    font-size: 18px;
  }
</style>
