<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import PageHeading from "$lib/design/PageHeading.svelte";
  import type { DocumentReference } from "./document-assist";
  import { onMount, tick } from "svelte";
  import { request } from "$lib/api/client";
  import {
    workspaceRequest,
    type Workspace,
    type Source,
    type Event,
    type Course,
    type RecordRow,
    type Semester,
  } from "./workspace";
  import Material from "./Material.svelte";
  import ProjectSaveLocation from "./ProjectSaveLocation.svelte";
  import Practice from "./Practice.svelte";
  import EventEditor from "./EventEditor.svelte";
  import Timeline from "$lib/design/Timeline.svelte";
  import { courseTimeline } from "./course-timeline";
  export let workspace: Workspace;
  export let course: RecordRow<Course>;
  export let busy = false;
  export let onFileChosen: () => void = () => {};
  export let active = true;
  export let onWideChange: (wide: boolean) => void = () => {};
  export let fileSidebar: HTMLElement | null = null;
  let mounted = false,
    wasActive = false;
  $: if (mounted && active && !wasActive) {
    wasActive = true;
    void refresh();
  }
  $: if (!active) wasActive = false;
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  export let onReserve: (value: Partial<Event>) => void = () => {};
  export let onTasks: () => void = () => {};
  let tab = "overview",
    visited = new Set(["overview"]),
    source: Source | null = null,
    requestedSource: Source | null = null,
    error = "",
    loading = false;
  export let onTabChange: (tab: string) => void = () => {};
  let reportedTab = "";
  $: if (tab !== reportedTab) {
    reportedTab = tab;
    onTabChange(tab);
  }
  export function openMaterial(value: Source) {
    // Reuse the mounted reader when it still shows the discussed source. If it
    // has changed, restore the exact retained artifact, never a newer live file.
    if (source?.artifact_id !== value.artifact_id) requestedSource = value;
    select("material");
  }
  let material: Material;
  let saveLocation: ProjectSaveLocation;
  export async function showMaterial() {
    select("material");
    await tick();
  }
  export async function openDocument(reference: DocumentReference) {
    await showMaterial();
    await material.openDiscussion(reference);
  }
  let taskEditor = false;
  let editor = false,
    editing: RecordRow<Event> | null = null;
  let meetings: RecordRow<Event>[] = [],
    tasks: {
      id: string;
      title: string;
      due: string | null;
      outcome: string;
    }[] = [];
  type Link = {
    course_id: string;
    task_id: string;
    estimate_minutes: number;
    material: Source[];
  };
  let links: RecordRow<Link>[] = [],
    taskId = "",
    estimate = 45,
    editingLink: RecordRow<Link> | null = null;
  let eventNext: number | null = null,
    linkNext: number | null = null,
    taskNext: number | null = null;
  let refreshAgain = false;
  $: semester = $workspace.snapshot?.semesters.find(
    (row) => row.id === course.data.semester_id,
  ) as RecordRow<Semester> | undefined;
  $: timeline = courseTimeline(meetings, course.id);
  $: locked = busy || $workspace.busy || !!$workspace.pending;
  function select(value: string) {
    tab = value;
    visited = new Set([...visited, value]);
    if (value === "tasks") void refresh();
  }
  async function refresh(append = false) {
    if (loading) {
      refreshAgain = true;
      return;
    }
    refreshAgain = false;
    loading = true;
    error = "";
    try {
      const events =
        append && eventNext === null
          ? { items: [], next_offset: null }
          : await workspaceRequest<{
              items: RecordRow<Event>[];
              next_offset: number | null;
            }>("record.page", {
              kind: "event",
              limit: 100,
              offset: append ? eventNext || 0 : 0,
            });
      const linked =
        append && linkNext === null
          ? { items: [], next_offset: null }
          : await workspaceRequest<{
              items: RecordRow<Link>[];
              next_offset: number | null;
            }>("record.page", {
              kind: "task_link",
              limit: 100,
              offset: append ? linkNext || 0 : 0,
            });
      meetings = append ? [...meetings, ...events.items] : events.items;
      eventNext = events.next_offset;
      links = append ? [...links, ...linked.items] : linked.items;
      linkNext = linked.next_offset;
      if (!append) await loadTasks();
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
      if (refreshAgain) void refresh();
    }
  }
  async function loadTasks(more = false) {
    try {
      const original = await request("tasks", {
        action: "list",
        outcome: "all",
        attention: "all",
        limit: 50,
        offset: more ? taskNext || 0 : 0,
      });
      if (!("items" in original))
        throw new Error("The original task list could not be supplied.");
      tasks = more ? [...tasks, ...original.items] : original.items;
      taskNext = original.next_offset;
    } catch (caught) {
      error = String(caught);
    }
  }
  async function saveLink() {
    if (locked) return;
    const result = await workspace.mutate("record.save", {
      kind: "task_link",
      data: {
        course_id: course.id,
        task_id: taskId,
        estimate_minutes: estimate,
        material: source ? [source] : editingLink?.data.material || [],
      },
      ...(editingLink
        ? { id: editingLink.id, expected_revision: editingLink.revision }
        : {}),
    });
    if (result) {
      taskEditor = false;
      editingLink = null;
      taskId = "";
      await refresh();
    }
  }
  function newMeeting() {
    editing = null;
    editor = true;
  }
  onMount(() => {
    mounted = true;
  });
</script>

<ProjectSaveLocation
  bind:this={saveLocation}
  {workspace}
  {course}
  {busy}
  {active}
/>

<section
  class="uni course-view"
  class:reading={tab === "material"}
  aria-label={`Section ${course.data.title}`}
>
  <nav class="course-tabs" aria-label="Section views">
    {#each ["overview", "material", "practice", "tasks"] as value}<button
        aria-pressed={tab === value}
        on:click={() => select(value)}
        >{value === "material"
          ? "Resources"
          : value[0].toUpperCase() + value.slice(1)}</button
      >{/each}
  </nav>
  {#if tab !== "material"}<PageHeading
      title={course.data.title}
      subtitle={semester?.data.title || "Section"}
    />{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <section hidden={tab !== "overview"}>
    <div class="overview-grid">
      <article class="resource-entry material-entry">
        <div class="resource-heading">
          <span class="resource-symbol" aria-hidden="true">
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              ><path d="M4 5h7l2 3h7v12H4V5Z" /><path d="M4 11h16" /></svg
            >
          </span>
          <h2>Resources</h2>
        </div>
        <p class="resource-description">
          Read and work with your section files and documents.
        </p>
        <p class="source resource-folder">
          {course.data.folder ||
            "Choose a shared folder to add your resources."}
        </p>
        <button class="resource-link" on:click={() => select("material")}
          >Open resources
        </button>
      </article>
      <article class="resource-entry practice-entry">
        <div class="resource-heading">
          <span class="resource-symbol" aria-hidden="true">
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              ><path
                d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"
              /></svg
            >
          </span>
          <h2>Learn &amp; revisit</h2>
        </div>
        <p class="resource-description">
          Practise from your material, review feedback and return to what needs
          another pass.
        </p>
        <button class="resource-link" on:click={() => select("practice")}
          >Open practice
        </button>
      </article>
    </div>
    <div class="schedule-heading">
      <h2>Schedule</h2>
      <div class="schedule-actions">
        <button
          class="workspace-action"
          disabled={locked}
          on:click={() =>
            onReserve({
              kind: "study",
              title: `${course.data.title} · work session`,
              course_id: course.id,
              material: source ? [source] : [],
            })}
        >
          <Icon name="clock" size={14} /> Schedule work time
        </button>

        <button
          class="workspace-action"
          disabled={loading}
          on:click={() => refresh()}
        >
          <Icon name="refresh" size={14} /> Refresh schedule
        </button>
        <button
          class="workspace-action"
          disabled={locked}
          on:click={newMeeting}
        >
          <Icon name="plus" size={14} /> Add event
        </button>
      </div>
    </div>
    {#if timeline.length}
      <Timeline items={timeline} label="Schedule">
        <svelte:fragment slot="actions" let:item>
          {@const row = meetings.find((row) => row.id === item.id)!}
          <button
            class="timeline-edit"
            title={`Edit ${row.data.repeat === "none" ? "event" : "series"}: ${item.title}`}
            disabled={locked}
            aria-label={`Edit ${row.data.repeat === "none" ? "event" : "series"}: ${item.title}`}
            on:click={() => {
              editing = row;
              editor = true;
            }}
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              aria-hidden="true"
              ><path d="m4 12 9-9 4 4-9 9-5 1 1-5ZM11 5l4 4" /></svg
            >
          </button>
        </svelte:fragment>
      </Timeline>
    {:else}<p class="muted schedule-empty">
        Add events, work sessions and deadlines for this section.
      </p>{/if}
    {#if eventNext !== null}<p class="muted">
        More entries are available. Load them to complete this timeline.
      </p>
      <button class="plain" disabled={loading} on:click={() => refresh(true)}
        >More schedule entries</button
      >{/if}
    <p class="muted">Use Calendar to change or cancel a single occurrence.</p>
  </section>
  {#if visited.has("material")}<section hidden={tab !== "material"}>
      <Material
        bind:this={material}
        {onFileChosen}
        courseId={course.id}
        courseTitle={course.data.title}
        startProjectBrief
        projectPeriod={semester?.data.title ?? ""}
        {fileSidebar}
        {onWideChange}
        folder={course.data.folder}
        chooseSaveFolder={() => saveLocation.choose()}
        {requestedSource}
        active={active && tab === "material"}
        busy={locked}
        onPractice={(value) => {
          source = value;
          select("practice");
        }}
        onSource={(value) => {
          source = value;
        }}
        {onDiscuss}
      />
    </section>{/if}
  {#if visited.has("practice")}<section hidden={tab !== "practice"}>
      <Practice
        {workspace}
        {source}
        courseId={course.id}
        busy={locked}
        {onDiscuss}
        onReadSource={(value) => {
          requestedSource = value;
          source = value;
          select("material");
        }}
      />
    </section>{/if}
  <section hidden={tab !== "tasks"}>
    <div class="row spread">
      <h2>Section tasks</h2>
      <div class="row">
        <button
          disabled={locked}
          on:click={() => {
            editingLink = null;
            taskId = "";
            taskEditor = true;
          }}>Link existing task</button
        ><button on:click={onTasks}>Manage tasks</button>
      </div>
    </div>
    <p class="muted">Plan the work for this section and reserve time for it.</p>
    <div class="stack">
      {#each links.filter((row) => row.data.course_id === course.id) as row (row.id)}{@const original =
          tasks.find((task) => task.id === row.data.task_id)}
        <article class="card">
          <h3>
            {original?.title ||
              "Original task is on another page or unavailable"}
          </h3>
          <p class="muted">
            {original?.outcome || "Read original tasks for status"} · {original?.due
              ? `Due ${original.due} · `
              : ""}Estimated {row.data.estimate_minutes} minutes
          </p>
          <div class="row">
            <button
              disabled={locked}
              on:click={() =>
                onReserve({
                  kind: "study",
                  title: original?.title || course.data.title,
                  course_id: course.id,
                  task_id: row.data.task_id,
                  material: row.data.material,
                })}>Reserve a session</button
            ><button
              disabled={locked}
              on:click={() => {
                taskEditor = true;
                editingLink = row;
                taskId = row.data.task_id;
                estimate = row.data.estimate_minutes;
              }}>Edit estimate / link</button
            ><button
              disabled={locked}
              on:click={async () => {
                if (
                  await workspace.mutate("record.save", {
                    kind: "task_link",
                    id: row.id,
                    expected_revision: row.revision,
                    data: row.data,
                    cancelled: true,
                  })
                )
                  await refresh();
              }}>Unlink from section</button
            >
          </div>
        </article>{:else}<p class="empty">No linked tasks yet.</p>{/each}
    </div>
    {#if linkNext !== null}<button on:click={() => refresh(true)}
        >More section task links</button
      >{/if}
    <div hidden={!taskEditor}>
      <form class="card" on:submit|preventDefault={saveLink}>
        <h3>
          {editingLink
            ? "Edit section task link"
            : "Link an existing personal task"}
        </h3>
        <label
          >Task<select required bind:value={taskId}
            ><option value="">Choose task</option>{#each tasks as item}<option
                value={item.id}>{item.title} · {item.outcome}</option
              >{/each}</select
          ></label
        >{#if taskNext !== null}<button
            type="button"
            disabled={locked}
            on:click={() => loadTasks(true)}>More original tasks</button
          >{/if}<label
          >Estimated work (minutes)<input
            type="number"
            min="5"
            max="480"
            bind:value={estimate}
          /></label
        >
        <p class="muted">
          {source
            ? `Selected material will be linked: ${source.path}`
            : "Open resources first to link a specific source, or save without one."}
        </p>
        <button disabled={locked || !taskId}>Save task plan</button>
        <button
          type="button"
          disabled={locked}
          on:click={() => (taskEditor = false)}>Cancel</button
        >
      </form>
    </div>
  </section>
  {#if editor}<EventEditor
      {workspace}
      busy={locked}
      record={editing}
      initial={editing
        ? {}
        : {
            course_id: course.id,
            timezone:
              semester?.data.timezone ||
              Intl.DateTimeFormat().resolvedOptions().timeZone ||
              "UTC",
            kind: "appointment",
          }}
      onCancel={() => (editor = false)}
      onDone={() => {
        editor = false;
        void refresh();
      }}
    />{/if}
</section>

<style>
  .reading .course-tabs {
    margin: 0;
  }

  .course-tabs {
    display: flex;
    gap: calc(24px * var(--ke-density));
    min-height: 36px;
    align-items: center;
    border-bottom: 1px solid var(--ke-border);
    margin: 0 0 28px;
  }
  .course-tabs button {
    border: 0;
    border-bottom: 0;
    border-radius: 0;
    background: none;
    padding: 8px 0;
    font-size: var(--ke-label-size);
  }
  .course-tabs button[aria-pressed="true"] {
    font-weight: 500;
    background: none;
    color: var(--ke-text-primary);
  }

  .schedule-heading {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: var(--ke-space-3);
    margin-top: calc(32px * var(--ke-density));
  }
  .schedule-heading h2 {
    margin: 0;
  }
  .schedule-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .schedule-empty {
    padding: var(--ke-space-6) 0;
  }
  .overview-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: calc(32px * var(--ke-density));
  }
  .resource-entry {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    min-width: 0;
    padding: var(--ke-space-2) 0 var(--ke-space-5);
    border-bottom: 1px solid var(--ke-border);
  }
  .resource-heading {
    display: flex;
    align-items: center;
    gap: var(--ke-space-3);
  }
  .resource-heading h2 {
    margin: 0;
    font-size: var(--ke-resource-size);
    font-weight: 500;
  }
  .resource-symbol {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
    background: color-mix(in srgb, var(--ke-marker-sand) 12%, transparent);
  }
  .practice-entry .resource-symbol {
    background: color-mix(in srgb, var(--ke-marker-sage) 12%, transparent);
  }
  .resource-description {
    margin: var(--ke-space-3) 0 var(--ke-space-2);
    max-width: 44ch;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .resource-folder {
    margin: 0 0 var(--ke-space-3);
  }
  .resource-link {
    display: inline-flex;
    align-items: center;
    gap: var(--ke-space-3);
    padding: 6px 0;
    margin-top: auto;
    border: 0;
    background: transparent;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-primary);
  }
  .resource-link:hover:not(:disabled) {
    background: transparent;
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .timeline-edit {
    min-width: 32px;
    min-height: 32px;
    justify-content: center;
  }
  @media (max-width: 750px) {
    .overview-grid {
      grid-template-columns: 1fr;
      gap: var(--ke-space-4);
    }
  }
</style>
