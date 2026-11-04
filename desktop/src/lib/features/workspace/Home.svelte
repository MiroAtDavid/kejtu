<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import type { TaskRecord } from "$lib/api/wire.generated";
  import {
    civilInstant,
    workspaceRequest,
    type Workspace,
    type View,
  } from "./workspace";
  import { dayInZone, timeLabel, type Occurrence } from "./calendar-view";
  import { agendaZone } from "./upcoming";
  import Icon from "$lib/design/Icon.svelte";
  import HomeResearch from "./HomeResearch.svelte";

  export let workspace: Workspace;
  export let active = true;
  export let disabled = false;
  export let firstRun = false;
  export let researchNew = 0;
  export let researchVersion = 0;
  import type { FindingView } from "../tasks/research-view";
  export let onOpen: (view: View, id?: string) => void = () => {};
  export let onResearch: (
    jobId?: string,
    view?: FindingView,
  ) => void = () => {};
  export let onHelp: () => void = () => {};
  export let onSettings: () => void = () => {};
  let projectPage = 0;
  let homeElement: HTMLElement;
  let projectColumns = 3;
  $: projectPageSize = projectColumns * 2;
  const rowLimit = 3;
  let now = new Date(),
    mounted = false,
    seen = "",
    generation = 0;
  let loading = false,
    agendaError = "",
    tasksError = "",
    refreshVersion = 0;
  let events: Occurrence[] = [],
    tasks: TaskRecord[] = [],
    total = 0;
  $: zone = agendaZone(
    $workspace.snapshot?.settings.availability.timezone,
    "UTC",
  );
  $: today = dayInZone(now, zone);
  $: projects = !!$workspace.snapshot?.settings.university_enabled;
  $: courses = ($workspace.snapshot?.courses.filter((c) => !c.cancelled) ?? [])
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  $: nextEvents = events.filter(
    (event) =>
      Date.parse(event.kind === "deadline" ? event.starts_at : event.ends_at) >=
      now.getTime(),
  );
  $: shownEvents = nextEvents.slice(0, rowLimit);
  $: projectPages = Math.ceil(projectEntries.length / projectPageSize);
  // Keep the selected page valid if a project is removed while Home is open.
  $: if (projectPage >= projectPages)
    projectPage = Math.max(0, projectPages - 1);
  $: projectEntries = [
    ...($workspace.snapshot?.semesters ?? []).map((row) => ({
      ...row,
      destination: "semesters" as const,
    })),
    ...courses
      .filter((row) => !row.data.semester_id)
      .map((row) => ({ ...row, destination: "course" as const })),
  ];
  $: shownProjects = projectEntries.slice(
    projectPage * projectPageSize,
    (projectPage + 1) * projectPageSize,
  );
  $: hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      hour: "numeric",
      hourCycle: "h23",
    }).format(now),
  );
  $: greeting =
    hour < 12
      ? "Good morning."
      : hour < 18
        ? "Good afternoon."
        : "Good evening.";
  $: date = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(now);
  const order = ["research", "today", "projects", "tasks"];
  $: key = `${today}:${zone}:${$workspace.snapshot?.revision}`;
  $: if (mounted && active && $workspace.snapshot && seen !== key) {
    seen = key;
    void refresh();
  }
  $: if (!active) seen = "";

  async function refresh() {
    const token = ++generation;
    loading = true;
    agendaError = tasksError = "";
    events = [];
    tasks = [];
    total = 0;
    refreshVersion++;
    const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 86400000)
      .toISOString()
      .slice(0, 10);
    // Civil midnights use the existing timezone owner, including 23/25-hour days.
    const calendar = projects
      ? Promise.resolve().then(() =>
          workspaceRequest<{ occurrences: Occurrence[] }>("calendar.view", {
            first: civilInstant(today + "T00:00", zone),
            last: civilInstant(tomorrow + "T00:00", zone),
            timezone: zone,
          }),
        )
      : Promise.resolve({ occurrences: [] });
    const [schedule, list] = await Promise.allSettled([
      calendar,
      request("tasks", {
        action: "list",
        outcome: "open",
        attention: "active",
        day: today,
        limit: rowLimit,
      }),
    ]);
    if (token !== generation) return;
    if (schedule.status === "fulfilled") events = schedule.value.occurrences;
    else agendaError = String(schedule.reason);
    if (list.status === "fulfilled" && "items" in list.value) {
      tasks = list.value.items;
      total = list.value.total;
    } else
      tasksError =
        list.status === "rejected"
          ? String(list.reason)
          : "The task list could not be read.";
    loading = false;
  }
  onMount(() => {
    mounted = true;
    function sizeProjects() {
      const list = homeElement.querySelector(".project-list");
      if (!list) return;
      // CSS owns the column breakpoints; page the same grid at two rows per page.
      const columns = Number(
        getComputedStyle(list).getPropertyValue("--project-columns"),
      );
      if (![1, 2, 3].includes(columns) || columns === projectColumns) return;
      // Keep the first visible project in view when the window/sidebar changes.
      projectPage = Math.floor((projectPage * projectColumns) / columns);
      projectColumns = columns;
    }
    sizeProjects();
    const layout =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(sizeProjects);
    layout?.observe(homeElement);
    const timer = setInterval(() => {
      if (active && !document.hidden) now = new Date();
    }, 60000);
    return () => {
      generation++;
      layout?.disconnect();
      clearInterval(timer);
    };
  });
</script>

<section class="home" aria-label="Home" bind:this={homeElement}>
  <header class="home-heading">
    <div>
      <p class="eyebrow">{date}</p>
      <h1>{firstRun ? "Make yourself at home." : greeting}</h1>
    </div>
    <button
      class="refresh"
      disabled={loading}
      on:click={refresh}
      aria-label="Refresh Home"
      title="Refresh Home"><Icon name="refresh" size={15} /></button
    >
  </header>
  {#if firstRun}
    <div class="start" aria-label="Getting started">
      <p>Choose a model in Settings, then start with a conversation.</p>
      <button class="primary" {disabled} on:click={onSettings}
        >Open Settings <span aria-hidden="true">→</span></button
      >
      <button class="text-button" {disabled} on:click={onHelp}
        >Getting started & capabilities</button
      >
    </div>
  {/if}
  <div class="home-grid">
    {#each order as section (section)}
      <section
        class="home-section"
        class:project-section={section === "projects"}
        data-section={section}
        aria-label={section === "today"
          ? "Today’s schedule"
          : section === "research"
            ? "Research"
            : section === "tasks"
              ? "Tasks"
              : "Your projects"}
      >
        {#if section === "today"}
          <header>
            <h2>
              Today’s schedule {#if !loading && events.length}<span
                  class="section-count">{events.length}</span
                >{/if}
            </h2>
            {#if projects}<button
                class="text-button more"
                {disabled}
                on:click={() => onOpen("overview")}
                >Calendar <span aria-hidden="true">→</span></button
              >{/if}
          </header>
          {#if loading}<p class="muted" role="status">Reading today’s plans…</p>
          {:else if agendaError}<p class="read-error" role="status">
              Today’s plans could not be read. {agendaError}
            </p>
          {:else if !projects}<p class="muted">Planning is not set up yet.</p>
            <button
              class="text-button"
              {disabled}
              on:click={() => onOpen("workspace-settings")}
              >Set up Projects →</button
            >
          {:else}
            <ol class="agenda">
              {#each shownEvents as event}<li>
                  <time>{timeLabel(event, today, zone)}</time>
                  <div>
                    <strong title={event.title}>{event.title}</strong
                    >{#if event.course_id}<span
                        >{courses.find((c) => c.id === event.course_id)?.data
                          .title || "Project"}</span
                      >{:else if event.location}<span>{event.location}</span
                      >{/if}
                  </div>
                </li>{:else}<li class="muted">
                  {events.length
                    ? "Nothing else scheduled today."
                    : "No commitments saved for today."}
                </li>{/each}
            </ol>
            {#if events.length}<p class="footnote">
                {zone.replaceAll("_", " ")}{#if nextEvents.length}
                  · {nextEvents.length} upcoming{#if nextEvents.length > rowLimit}
                    · {rowLimit} shown{/if}
                {/if}
              </p>{/if}
          {/if}
        {:else if section === "tasks"}
          <header>
            <h2>
              Tasks {#if !loading && total}<span class="section-count"
                  >{total}</span
                >{/if}
            </h2>
            <button
              class="text-button more"
              {disabled}
              on:click={() => onOpen("tasks")}>All tasks →</button
            >
          </header>
          {#if loading}<p class="muted" role="status">Reading your tasks…</p>
          {:else if tasksError}<p class="read-error" role="status">
              Your tasks could not be read. {tasksError}
            </p>
          {:else}
            <ul class="task-list">
              {#each tasks.slice(0, rowLimit) as task}<li>
                  <button
                    class="row"
                    {disabled}
                    on:click={() => onOpen("tasks")}
                  >
                    <span class="task-dot" aria-hidden="true"></span><span
                      class="row-title"
                      title={task.title}>{task.title}</span
                    >
                    <span
                      class="task-date"
                      class:overdue={task.due && task.due < today}
                      >{task.due === today
                        ? "Today"
                        : task.due && task.due < today
                          ? "Overdue"
                          : task.due || "Anytime"}</span
                    >
                  </button>
                </li>{:else}<li class="muted">No active tasks.</li>{/each}
            </ul>
            {#if total > Math.min(tasks.length, rowLimit)}<p class="footnote">
                Showing {Math.min(tasks.length, rowLimit)} of {total} active tasks.
              </p>{/if}
          {/if}
        {:else if section === "research"}
          <HomeResearch
            {active}
            {disabled}
            {researchNew}
            {researchVersion}
            {refreshVersion}
            {onResearch}
            {zone}
          />
        {:else if section === "projects"}
          <header>
            <h2>
              Your projects {#if projectEntries.length}<span
                  class="section-count">{projectEntries.length}</span
                >{/if}
            </h2>
            <button
              class="text-button more"
              {disabled}
              on:click={() =>
                onOpen(projects ? "semesters" : "workspace-settings")}
              >{projects ? "All projects" : "Set up Projects"} →</button
            >
            {#if projectPages > 1}
              <nav class="project-pages" aria-label="Project pages">
                <span role="status" aria-live="polite"
                  >{projectPage * projectPageSize + 1}–{Math.min(
                    (projectPage + 1) * projectPageSize,
                    projectEntries.length,
                  )} of {projectEntries.length}</span
                >
                <button
                  disabled={disabled || projectPage === 0}
                  on:click={() => projectPage--}
                  aria-label="Previous projects">←</button
                >
                <button
                  disabled={disabled || projectPage >= projectPages - 1}
                  on:click={() => projectPage++}
                  aria-label="Next projects">→</button
                >
              </nav>
            {/if}
          </header>
          <ul class="project-list">
            {#each shownProjects as course, i (course.id)}<li>
                <button
                  class="project"
                  {disabled}
                  on:click={() => onOpen(course.destination, course.id)}
                >
                  <span class="project-index" aria-hidden="true"
                    >{String(projectPage * projectPageSize + i + 1).padStart(
                      2,
                      "0",
                    )}</span
                  >
                  <span class="project-title" title={course.data.title}
                    >{course.data.title}</span
                  ><span class="project-action"
                    >{course.destination === "course"
                      ? "Open section"
                      : "Open project"} <span aria-hidden="true">↗</span></span
                  >
                </button>
              </li>{:else}<li class="muted">
                A place for your documents and ongoing work.
              </li>{/each}
          </ul>
        {/if}
      </section>
    {/each}
  </div>
</section>

<style>
  .home {
    max-width: 1020px;
    margin: 0 auto;
    padding: 0 0 24px;
    container: home / inline-size;
  }
  .home-heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    padding-bottom: 20px;
  }
  h1,
  h2,
  p {
    margin: 0;
  }
  h1 {
    font:
      400 29px/1.25 Georgia,
      serif;
    letter-spacing: -0.6px;
  }
  .eyebrow {
    color: var(--ke-text-tertiary);
    font: 400 10px/1.6 var(--ke-font-mono);
    letter-spacing: 1.2px;
    text-transform: uppercase;
    margin: 0 0 12px;
  }
  button {
    font: inherit;
    cursor: pointer;
  }
  button:disabled {
    cursor: default;
    opacity: 0.45;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 4px;
  }
  .refresh {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: 5px;
    background: none;
    color: var(--ke-text-secondary);
  }
  .refresh:hover {
    background: var(--ke-surface-inset);
  }
  .home-grid {
    display: grid;
    gap: 0 36px;
  }
  .home-section {
    min-width: 0;
    padding: 20px 0;
    border-top: 1px solid var(--ke-border);
  }
  .home-section > header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px 16px;
    margin-bottom: 14px;
  }
  .home-section h2 {
    font: 550 13px/1.6 var(--ke-font-ui);
  }
  .primary {
    display: inline-flex;
    justify-content: space-between;
    align-items: center;
    gap: 25px;
    padding: 9px 12px;
    border: 0;
    border-radius: 6px;
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: 12px;
  }
  .primary:hover:not(:disabled) {
    background: var(--ke-border);
  }
  .text-button {
    font-size: 12px;
    color: var(--ke-text-secondary);
    border: 0;
    background: none;
    padding: 5px 0;
    text-align: left;
  }
  .text-button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .more {
    font-size: 11px;
  }
  .muted,
  .read-error {
    color: var(--ke-text-secondary);
    font-size: 12px;
    line-height: 1.8;
  }
  .read-error {
    color: var(--ke-danger);
    overflow-wrap: anywhere;
  }
  ul,
  ol {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .agenda li:not(.muted) {
    display: grid;
    grid-template-columns: 55px 1fr;
    gap: 12px;
    padding: 10px 0;
  }
  .agenda time {
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    color: var(--ke-text-secondary);
    padding-top: 2px;
  }
  .agenda li > div {
    border-left: 2px solid var(--ke-accent);
    padding-left: 12px;
    min-width: 0;
  }
  .agenda strong {
    font-size: 12px;
    font-weight: 450;
    overflow-wrap: anywhere;
  }
  .agenda li span {
    display: block;
    font-size: 11px;
    color: var(--ke-text-secondary);
    margin-top: 4px;
  }
  .footnote {
    font-size: 10px;
    line-height: 1.6;
    color: var(--ke-text-tertiary);
    margin: 12px 0 0;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    border: 0;
    background: none;
    padding: 12px 0;
    text-align: left;
    color: var(--ke-text-primary);
    font-size: 12px;
    line-height: 1.65;
  }
  .row:hover:not(:disabled) .row-title {
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .row-title {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .task-list li + li {
    border-top: 1px solid var(--ke-border);
  }
  .task-dot {
    width: 5px;
    height: 5px;
    flex: none;
    border-radius: 50%;
    background: var(--ke-accent);
  }
  .task-date {
    font-size: 10px;
    color: var(--ke-text-secondary);
    white-space: nowrap;
  }
  .task-date.overdue {
    color: var(--ke-danger);
  }
  .start {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
    padding: 0 0 24px;
  }
  .start p {
    flex-basis: 100%;
    font-size: 13px;
    color: var(--ke-text-secondary);
  }
  @container home (min-width: 660px) {
    .home-grid {
      grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
    }
    [data-section="today"] {
      grid-area: agenda;
    }
    [data-section="research"] {
      grid-area: research;
    }
    [data-section="tasks"] {
      grid-area: tasks;
    }
    [data-section="projects"] {
      grid-area: projects;
    }
  }
  .section-count {
    color: var(--ke-text-tertiary);
    font: 10px/1.5 var(--ke-font-mono);
    margin-left: 8px;
  }
  .project-pages {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 10px;
    margin-top: 14px;
  }
  .project-pages > span {
    color: var(--ke-text-secondary);
    font: 10px/1.5 var(--ke-font-mono);
    margin-right: 8px;
  }
  .project-pages button {
    display: grid;
    place-items: center;
    width: 30px;
    height: 28px;
    border: 1px solid var(--ke-border);
    border-radius: 4px;
    color: var(--ke-text-primary);
    background: transparent;
  }
  .project-pages button:hover:not(:disabled) {
    background: var(--ke-surface-inset);
  }
  .home-heading {
    padding: 8px 0 28px;
    gap: 12px;
  }
  .home-heading > div:first-child {
    min-width: 0;
  }
  .home-heading .refresh {
    align-self: flex-start;
    margin-top: 16px;
    flex: none;
  }
  .home-section {
    padding: 20px 0;
  }
  .agenda li:not(.muted) {
    min-height: 43px;
    padding: 7px 0;
  }
  .agenda strong,
  .agenda li span,
  .row-title {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .agenda li span {
    margin-top: 1px;
  }
  .row {
    min-height: 43px;
    padding: 10px 0;
  }
  @container home (min-width: 660px) {
    .home-grid {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      grid-template-areas: "research agenda" "projects projects" "tasks tasks";
      gap: 0 32px;
    }
  }
  @container home (max-width: 430px) {
    .home-heading {
      position: relative;
      min-height: 90px;
      padding-right: 28px;
    }
    h1 {
      font-size: 25px;
    }
    .eyebrow {
      font-size: 9px;
      letter-spacing: 0.5px;
    }
    .home-heading .refresh {
      position: absolute;
      top: -6px;
      right: 0;
    }
  }
  .home-grid {
    row-gap: 8px;
  }
  [data-section="research"] {
    border: 0;
    background: color-mix(
      in srgb,
      var(--ke-accent-soft) 70%,
      var(--ke-surface)
    );
    padding: 20px 22px;
    border-radius: 8px;
    margin-bottom: 16px;
  }
  [data-section="today"] {
    margin-bottom: 16px;
  }
  @container home (min-width: 660px) {
    .home-grid {
      grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
    }
    .task-list {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 24px;
    }
    .task-list li + li {
      border-top: 0;
    }
    .task-list .row {
      display: grid;
      grid-template-columns: 5px minmax(0, 1fr);
      align-items: start;
      gap: 4px 10px;
    }
    .task-dot {
      margin-top: 7px;
    }
    .task-date {
      grid-column: 2;
    }
    .task-list .row-title {
      white-space: normal;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      -webkit-box-orient: vertical;
    }
  }
  .home-heading {
    padding: 0 0 22px;
  }
  .home-section {
    padding-top: 16px;
    padding-bottom: 16px;
  }
  [data-section="research"] {
    padding: 16px 20px;
  }
  .project-section > header {
    justify-content: flex-start;
  }
  .project-section > header h2 {
    margin-right: auto;
  }
  .project-pages {
    margin: 0;
    gap: 6px;
  }
  .project-pages > span {
    margin-right: 4px;
  }
  .project-list {
    --project-columns: 1;
    display: grid;
    grid-template-columns: repeat(var(--project-columns), minmax(0, 1fr));
    gap: 12px;
  }
  .project-list > li {
    min-width: 0;
  }
  .project-list > .muted {
    grid-column: 1 / -1;
  }
  .project {
    display: grid;
    grid-template-rows: auto 1fr auto;
    gap: 8px;
    width: 100%;
    height: 100%;
    min-height: 126px;
    padding: 14px 16px;
    text-align: left;
    color: var(--ke-text-primary);
    background: color-mix(
      in srgb,
      var(--ke-accent-soft) 40%,
      var(--ke-surface)
    );
    border: 1px solid var(--ke-border);
    border-radius: 8px;
  }
  .project:hover:not(:disabled) {
    background: color-mix(
      in srgb,
      var(--ke-accent-soft) 65%,
      var(--ke-surface)
    );
    border-color: var(--ke-border-strong);
  }
  .project-index {
    color: var(--ke-text-tertiary);
    font: 10px/1.6 var(--ke-font-mono);
  }
  .project-title {
    font:
      400 21px/1.35 Georgia,
      serif;
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .project-action {
    color: var(--ke-text-secondary);
    font-size: 10px;
    margin-top: 3px;
  }
  .project:hover:not(:disabled) .project-action {
    color: var(--ke-text-primary);
  }
  @container home (min-width: 440px) {
    .project-list {
      --project-columns: 2;
    }
  }
  @container home (min-width: 660px) {
    .project-list {
      --project-columns: 3;
    }
    .project {
      padding: 16px 18px;
    }
    .project-title {
      font-size: 23px;
    }
  }
</style>
