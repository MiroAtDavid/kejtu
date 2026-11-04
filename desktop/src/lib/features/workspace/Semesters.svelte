<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import PageHeading from "$lib/design/PageHeading.svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import WorkspaceNotice from "./WorkspaceNotice.svelte";
  import { drawerFocus } from "./drawer";
  import ProjectFolderPicker from "./ProjectFolderPicker.svelte";
  import {
    localDate,
    type Workspace,
    type Semester,
    type Course,
    type RecordRow,
  } from "./workspace";
  export let workspace: Workspace;
  export let busy = false;
  export let projectId = "";
  export let onTasks: (id: string) => void = () => {};
  let shownProject = "";
  $: if (projectId && projectId !== shownProject) {
    shownProject = projectId;
    selectedSemester = projectId;
  }
  export let onOpenCourse: (id: string) => void = () => {};
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
    detail:
      !row.data.starts_on && !row.data.ends_on
        ? "Ongoing project"
        : `${dateLabel(row.data.starts_on)} – ${row.data.ends_on ? dateLabel(row.data.ends_on) : "Open-ended"}`,
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

<div class="uni course-directory">
  <PageHeading
    title="Your projects"
    subtitle="Resources and tasks, gathered around what you’re working on."
  />
  <div class="semester-toolbar">
    <div class="semester-controls">
      <div class="semester-choice">
        <ChoicePicker
          id="directory-semester"
          label="Project"
          options={[
            { value: "", label: "All projects" },
            { value: "ungrouped", label: "Sections without a project" },
            ...semesterOptions,
          ]}
          bind:value={selectedSemester}
          placeholder="All projects"
          disabled={locked}
        />
      </div>
      <div class="semester-actions">
        {#if currentSemester}<button
            class="pencil-action"
            disabled={locked}
            title="Edit project"
            aria-label="Edit project"
            on:click={() => edit(currentSemester || null)}
            ><svg
              width="13"
              height="13"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              aria-hidden="true"
              ><path d="m4 12 9-9 4 4-9 9-5 1 1-5ZM11 5l4 4" /></svg
            ></button
          >
          <button
            class="workspace-action"
            disabled={locked}
            on:click={() => onTasks(currentSemester!.id)}
            ><Icon name="check" size={14} />Project tasks</button
          >{/if}
        <button
          class="workspace-action"
          disabled={locked}
          on:click={() => edit(null)}
          ><Icon name="plus" size={14} />New project</button
        >
      </div>
    </div>
    {#if currentSemester && (currentSemester.data.starts_on || currentSemester.data.ends_on)}<p
        class="semester-dates"
      >
        <time datetime={currentSemester.data.starts_on || undefined}
          >{dateLabel(currentSemester.data.starts_on)}</time
        ><span aria-hidden="true">{" – "}</span
        >{#if currentSemester.data.ends_on}<time
            datetime={currentSemester.data.ends_on}
            >{dateLabel(currentSemester.data.ends_on)}</time
          >{:else}<span>Open-ended</span>{/if}
      </p>{/if}
  </div>
  <div class="course-list-heading">
    <p class="course-count">
      <strong>Sections / Modules</strong> ·
      {courses.length}
      {courses.length === 1 ? "section" : "sections"}
    </p>
    <button
      class="workspace-action"
      disabled={locked}
      on:click={() => editCourse(null, currentSemester?.id || "")}
      ><Icon name="plus" size={14} />Add section</button
    >
  </div>
  <section class="course-grid" aria-label="Sections / Modules">
    {#each courses as item (item.id)}
      <article
        class="course-card"
        style={`--course-tone: var(--ke-marker-${tone(item.id)})`}
      >
        <span class="course-mark" aria-hidden="true"
          >{item.data.title.charAt(0)}</span
        >
        <div class="course-copy">
          <h2>
            <button
              class="course-open"
              disabled={locked}
              aria-label={`${item.data.title} — Open section`}
              on:click={() => onOpenCourse(item.id)}>{item.data.title}</button
            >
          </h2>
          <p>
            {item.data.folder
              ? "Resources folder linked"
              : "No resources folder yet"}
          </p>
        </div>
        <button
          class="pencil-action course-edit"
          title={`Edit ${item.data.title}`}
          aria-label={`Edit ${item.data.title}`}
          disabled={locked}
          on:click={() => editCourse(item)}
          ><svg
            width="13"
            height="13"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.3"
            aria-hidden="true"
            ><path d="m4 12 9-9 4 4-9 9-5 1 1-5ZM11 5l4 4" /></svg
          ></button
        >
      </article>
    {:else}<p class="empty">
        {currentSemester
          ? "Add a section when you need to divide this project."
          : "Create a project, then add sections when useful."}
      </p>{/each}
  </section>
  {#if editor}<div
      class="drawer"
      role="dialog"
      aria-label={editor === "semester" ? "Project editor" : "Section editor"}
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
            <h2>{editingCourse ? "Edit section" : "New section"}</h2>
            <ChoicePicker
              id="course-semester"
              label="Project (optional)"
              options={[
                { value: "", label: "Sections without a project" },
                ...semesterOptions,
              ]}
              bind:value={course.semester_id}
              disabled={locked}
              placeholder="No project"
            />
            <label
              >Section name<input
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
              disabled={locked || !course.title.trim()}>Save section</button
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
    margin-top: calc(20px * var(--ke-density));
  }

  .semester-toolbar {
    padding-bottom: 22px;
    border-bottom: 1px solid var(--ke-border);
  }
  .semester-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px 16px;
  }
  .semester-choice {
    width: min(100%, 340px);
  }
  .semester-dates {
    margin: 9px 0 0 12px;
    color: var(--ke-text-tertiary);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .semester-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    margin-left: auto;
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
  .course-count {
    margin: 0;
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .course-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    column-gap: calc(36px * var(--ke-density));
  }
  .course-card {
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr) 32px;
    align-items: start;
    gap: 12px;
    padding: calc(21px * var(--ke-density)) 0;
    border-bottom: 1px solid var(--ke-border);
  }
  .course-mark {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    margin-top: 2px;
    border-radius: var(--ke-radius-sm);
    background: color-mix(in srgb, var(--course-tone) 15%, transparent);
    color: var(--ke-text-secondary);
    font:
      17px/1 Georgia,
      "Times New Roman",
      serif;
  }
  .course-copy {
    min-width: 0;
  }
  .course-copy h2 {
    margin: 0;
    font: 500 var(--ke-course-size)/1.5 var(--ke-font-ui);
  }
  .course-directory .course-open {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: 32px;
    padding: 4px 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    font: inherit;
    color: var(--ke-text-primary);
    text-align: left;
    overflow-wrap: anywhere;
  }
  .course-directory .course-open:hover:not(:disabled) {
    background: transparent;
  }
  .course-copy p {
    margin: 6px 0 0;
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .course-grid .empty {
    grid-column: 1 / -1;
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
    font-size: 11px;
    color: var(--ke-text-secondary);
    padding-right: 8px;
  }
  .editor-fields label {
    font-size: 11px;
  }
  .course-directory .editor-fields input {
    min-height: 36px;
    padding: 8px 10px;
    border: 0;
    border-bottom: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm) var(--ke-radius-sm) 0 0;
    background: color-mix(in srgb, var(--ke-marker-sand) 5%, transparent);
    font-size: 13px;
  }
  .editor-fields .save-action {
    justify-self: start;
    margin-top: 10px;
  }
  .editor-fields h2 {
    font-size: 18px;
  }
  @media (max-width: 800px) {
    .course-grid {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 600px) {
    .semester-choice {
      width: 100%;
    }
    .course-card {
      gap: 10px;
      padding: 18px 0;
    }
  }
</style>
