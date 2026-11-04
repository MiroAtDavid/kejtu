<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import PageHeading from "$lib/design/PageHeading.svelte";
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import {
    civilInstant,
    localDate,
    workspaceRequest,
    type Workspace,
    type Event,
    type RecordRow,
    type Source,
  } from "./workspace";
  import WeekCalendar from "./WeekCalendar.svelte";
  import {
    dayInZone,
    clockTime,
    dayLabel,
    onDay,
    timeLabel,
    type Occurrence,
    type Deadline,
  } from "./calendar-view";
  import EventEditor from "./EventEditor.svelte";
  export let workspace: Workspace;
  export let busy = false;
  export let active = true;
  let expanded = false;
  let selectedDay = "",
    planningOpen = false,
    visibleTasks = 4;
  let dayPanel: HTMLElement;
  function selectDay(day: string) {
    selectedDay = day;
    if (document.activeElement?.matches(":focus-visible"))
      dayPanel?.focus({ preventScroll: true });
    dayPanel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  let mounted = false,
    wasActive = false;
  $: if (mounted && active && !wasActive) {
    wasActive = true;
    void refresh();
  }
  $: if (!active) wasActive = false;
  export let initial: Partial<Event> | null = null;
  export let onTasks: () => void = () => {};
  export let onCourse: (id: string) => void = () => {};
  export let onProject: (id: string) => void = () => {};
  type Calendar = {
    occurrences: Occurrence[];
    deadlines: Deadline[];
    deadline_next_offset: number | null;
    reminder_notice: string;
  };
  let start = localDate(),
    zone = "UTC",
    loading = false,
    error = "",
    calendar: Calendar | null = null,
    days: string[] = [];
  let editor = false,
    record: RecordRow<Event> | null = null,
    exception: RecordRow | null = null,
    occurrence: string | null = null,
    seed: Partial<Event> = {};
  let minutes = 45,
    count = 2,
    suggestions: { starts_at: string; ends_at: string; timezone: string }[] =
      [],
    suggestionNotice = "",
    suggestionError = "";
  let tasks: { id: string; title: string; due: string | null }[] = [],
    links: RecordRow<{
      task_id: string;
      course_id: string | null;
      semester_id?: string;
      estimate_minutes: number;
      material: Source[];
    }>[] = [];
  let tasksReady = false;
  let taskNext: number | null = null;
  let linkNext: number | null = null;
  async function loadLinks(append = false) {
    const result = await workspaceRequest<{
      items: typeof links;
      next_offset: number | null;
    }>("record.page", {
      kind: "task_link",
      limit: 100,
      offset: append ? linkNext || 0 : 0,
    });
    links = append ? [...links, ...result.items] : result.items;
    linkNext = result.next_offset ?? null;
  }
  async function moreLinks() {
    if (loading || linkNext === null) return;
    loading = true;
    try {
      await loadLinks(true);
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
      if (refreshAgain) void refresh();
    }
  }
  let refreshAgain = false;
  let before = "";
  async function moreTasks() {
    try {
      const result = await request("tasks", {
        action: "list",
        outcome: "open",
        attention: "all",
        limit: 50,
        offset: taskNext || 0,
      });
      if (!("items" in result))
        throw new Error("The original task list could not be supplied.");
      tasks = [...tasks, ...result.items];
      taskNext = result.next_offset;
    } catch (caught) {
      error = String(caught);
    }
  }
  let firstMs = 0,
    lastMs = 0;
  const duration = (item: Occurrence) =>
    Math.max(
      0,
      Math.min(Date.parse(item.ends_at), lastMs) -
        Math.max(Date.parse(item.starts_at), firstMs),
    ) / 60000;
  const addDay = (value: string, number: number) => {
    const date = new Date(value + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + number);
    return date.toISOString().slice(0, 10);
  };
  function firstMonday(value: string) {
    const date = new Date(value + "T12:00:00Z");
    return addDay(value, -((date.getUTCDay() + 6) % 7));
  }
  function range() {
    return {
      first: civilInstant(start + "T00:00", zone),
      last: civilInstant(addDay(start, 7) + "T00:00", zone),
      timezone: zone,
    };
  }
  function dayOf(value: string) {
    return new Intl.DateTimeFormat("sv-SE", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(value));
  }
  const time = (value: string) => clockTime(value, zone);
  const minutesFor = (id: string) =>
    calendar?.occurrences
      .filter((item) => item.task_id === id && item.kind === "study")
      .reduce((total, item) => total + duration(item), 0) || 0;
  function clearSuggestions() {
    suggestions = [];
    suggestionNotice = suggestionError = "";
  }
  async function refresh() {
    // Returning to a mounted workspace must not lose a refresh behind its
    // preceding request. Coalesce it instead of showing an old week's records.
    if (loading) {
      refreshAgain = true;
      return;
    }
    refreshAgain = false;
    loading = true;
    error = "";
    clearSuggestions();
    zone = $workspace.snapshot?.settings.availability.timezone || zone;
    calendar = null;
    tasks = [];
    tasksReady = false;
    links = [];
    taskNext = linkNext = null;
    try {
      days = Array.from({ length: 7 }, (_, i) => addDay(start, i));
      if (!days.includes(selectedDay))
        selectedDay = days.includes(dayInZone(new Date(), zone))
          ? dayInZone(new Date(), zone)
          : start;
      const dates = range();
      firstMs = Date.parse(dates.first);
      lastMs = Date.parse(dates.last);
      calendar = await workspaceRequest<Calendar>("calendar.view", dates);
      const result = await request("tasks", {
        action: "list",
        outcome: "open",
        attention: "all",
        limit: 50,
      });
      if (!("items" in result))
        throw new Error("The original task list could not be supplied.");
      tasks = result.items;
      tasksReady = true;
      taskNext = result.next_offset;
      await loadLinks();
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
      if (refreshAgain) void refresh();
    }
  }
  async function moreDeadlines() {
    if (!calendar?.deadline_next_offset) return;
    try {
      const result = await workspaceRequest<Calendar>("calendar.view", {
        ...range(),
        deadline_offset: calendar.deadline_next_offset,
      });
      calendar = {
        ...result,
        deadlines: [...calendar.deadlines, ...result.deadlines],
      };
    } catch (caught) {
      error = String(caught);
    }
  }
  async function findSlots() {
    if (loading || locked) return;
    loading = true;
    clearSuggestions();
    try {
      const { timezone, ...dates } = range();
      const first = Math.max(Date.parse(dates.first), Date.now());
      const last = before
        ? Math.min(
            Date.parse(dates.last),
            Date.parse(civilInstant(before + "T00:00", zone)),
          )
        : Date.parse(dates.last);
      if (first >= last) {
        suggestions = [];
        suggestionNotice = before
          ? `No future time remains in this search. The cutoff excludes ${showDate(before)}; clear it or choose a later date.`
          : "No future time remains in this week. Choose a later week.";
        return;
      }
      dates.first = new Date(first).toISOString();
      dates.last = new Date(last).toISOString();
      const result = await workspaceRequest<{
        suggestions: typeof suggestions;
        notice: string | null;
        found: number;
      }>("calendar.suggest", { ...dates, minutes, count });
      suggestions = result.suggestions;
      suggestionNotice =
        (result.found
          ? `${result.found} suitable ${result.found === 1 ? "slot" : "slots"} found. Select one to preview and reserve it.`
          : before
            ? `No study slots fit this week's search before ${showDate(before)}. That day is excluded. Clear the date or choose a later one to include it.`
            : "No study slots fit the remaining time in this week. Try another week or a shorter session.") +
        (result.notice ? ` ${result.notice}` : "");
    } catch (caught) {
      // Search feedback belongs beside its controls, not above the calendar.
      suggestionError = String(caught);
    } finally {
      loading = false;
      if (refreshAgain) void refresh();
    }
  }
  function create(value: Partial<Event> = {}) {
    record = null;
    exception = null;
    occurrence = null;
    seed = {
      starts_at: civilInstant((selectedDay || start) + "T09:00", zone),
      ends_at: civilInstant((selectedDay || start) + "T10:00", zone),
      timezone: zone,
      ...value,
    };
    editor = true;
  }
  export function reserve(value: Partial<Event>) {
    create(value);
  }
  async function edit(item: Occurrence) {
    error = "";
    try {
      record = await workspaceRequest<RecordRow<Event>>("record.read", {
        kind: "event",
        id: item.event_id,
      });
      const result = await workspaceRequest<{
        items: RecordRow[];
        next_offset: number | null;
      }>("record.page", { kind: "exception", limit: 100 });
      let rows = result.items,
        next = result.next_offset;
      while (next !== null) {
        const more = await workspaceRequest<typeof result>("record.page", {
          kind: "exception",
          limit: 100,
          offset: next,
        });
        rows.push(...more.items);
        next = more.next_offset;
      }
      exception =
        rows.find(
          (row) =>
            row.data.event_id === item.event_id &&
            new Date(String(row.data.occurrence_at)).getTime() ===
              new Date(item.occurrence_at).getTime(),
        ) || null;
      occurrence =
        record.data.repeat === "none" && !exception ? null : item.occurrence_at;
      seed = { ...item };
      editor = true;
    } catch (caught) {
      error = String(caught);
    }
  }
  function showDate(value: string) {
    return new Date(value + "T12:00:00Z").toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }
  onMount(() => {
    zone =
      $workspace.snapshot?.settings.availability.timezone ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "UTC";
    start = firstMonday(dayInZone(new Date(), zone));
    mounted = true;
    if (initial) create(initial);
  });
  $: timed =
    calendar?.occurrences.filter((item) => item.kind !== "deadline") || [];
  $: studyMinutes = timed
    .filter((item) => item.kind === "study")
    .reduce((sum, item) => sum + duration(item), 0);
  $: locked = busy || $workspace.busy || !!$workspace.pending;
</script>

<section class="uni study-week" aria-label="Weekly overview">
  <PageHeading
    title="Weekly overview"
    subtitle="Events, deadlines and time for your projects."
  />
  <div class="calendar-toolbar">
    <div class="row">
      <button
        disabled={loading}
        aria-label="Previous week"
        on:click={() => {
          start = addDay(start, -7);
          void refresh();
        }}>‹</button
      >
      <button
        disabled={loading}
        on:click={() => {
          start = firstMonday(dayInZone(new Date(), zone));
          void refresh();
        }}>This week</button
      >
      <button
        disabled={loading}
        aria-label="Next week"
        on:click={() => {
          start = addDay(start, 7);
          void refresh();
        }}>›</button
      >
      <span class="date-range"
        >{showDate(start)} – {showDate(addDay(start, 6))}</span
      >
    </div>
    <div class="row">
      <button
        class="workspace-action"
        disabled={locked || loading}
        on:click={() => create()}
        ><Icon name="plus" size={14} /> Add event</button
      ><button
        class="workspace-action"
        disabled={locked}
        aria-expanded={planningOpen}
        on:click={() => (planningOpen = !planningOpen)}>Plan work time</button
      >
    </div>
  </div>
  <details class="calendar-options">
    <summary>Calendar options</summary>
    <div class="calendar-controls">
      <button
        class="plain"
        aria-expanded={expanded}
        aria-controls="study-week-calendar"
        on:click={() => (expanded = !expanded)}
        >{expanded ? "Collapse calendar" : "Expand calendar"}</button
      >
      <input
        aria-label="Week containing"
        type="date"
        disabled={loading}
        value={start}
        on:change={(event) => {
          if (event.currentTarget.value) {
            start = firstMonday(event.currentTarget.value);
            void refresh();
          }
        }}
      /><button class="plain refresh" disabled={loading} on:click={refresh}
        >{loading ? "Loading…" : "Refresh"}</button
      >
    </div>
  </details>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div id="study-week-calendar">
    <WeekCalendar
      {days}
      items={calendar?.occurrences || []}
      deadlines={calendar?.deadlines || []}
      {zone}
      {selectedDay}
      compact={!expanded}
      {loading}
      unavailable={!!error && !calendar}
      busy={locked || loading}
      onEdit={edit}
      {onTasks}
      onSelect={selectDay}
    />
  </div>
  <div class="calendar-caption">
    <span>Kejtu schedule · {zone}</span>{#if calendar}<span
        >{timed.filter((item) => item.kind === "class").length} classes · {Math.round(
          studyMinutes / 6,
        ) / 10}h reserved for study</span
      >{/if}
  </div>
  {#if calendar?.deadline_next_offset !== null && calendar?.deadline_next_offset !== undefined}<button
      disabled={loading}
      on:click={moreDeadlines}>More deadlines</button
    >{/if}
  <div class="planning-panel" hidden={!planningOpen}>
    <div class="row spread">
      <div>
        <p class="eyebrow">Make a little time</p>
        <h2>Find time to work</h2>
      </div>
      <button
        class="plain"
        disabled={locked}
        on:click={() => (planningOpen = false)}>Close planning</button
      >
    </div>
    <section class="slot-search" aria-label="Work time suggestions">
      <p class="muted">
        Suggestions try 09:00–18:00 each day, including weekends, around Kejtu’s
        schedule and your break / travel time. Describe different hours in chat.
        External calendar widgets are not checked. Slots stay unreserved until
        you choose and save one.
      </p>
      <form
        class="slot-form"
        on:submit|preventDefault={findSlots}
        on:input={clearSuggestions}
      >
        <div class="row">
          <label
            >Minutes<input
              type="number"
              min="5"
              max="480"
              step="1"
              required
              disabled={loading || locked}
              bind:value={minutes}
            /></label
          ><label
            >Sessions<input
              type="number"
              min="1"
              max="20"
              step="1"
              required
              disabled={loading || locked}
              bind:value={count}
            /></label
          ><label
            >Before (optional)<input
              type="date"
              bind:value={before}
              disabled={loading || locked}
              aria-describedby="study-cutoff-help"
            /></label
          ><button type="submit" disabled={loading || locked}
            >Find slots this week</button
          >
        </div>
        <div class="cutoff-help">
          <span id="study-cutoff-help"
            >The selected cutoff day is not included.</span
          >
          {#if before}<button
              class="plain"
              type="button"
              disabled={loading || locked}
              on:click={() => {
                before = "";
                clearSuggestions();
              }}>Clear date</button
            >{/if}
        </div>
      </form>
      {#if suggestionError}<p class="error" role="alert">
          {suggestionError}
        </p>{/if}
      {#if suggestionNotice}<p class="muted" role="status">
          {suggestionNotice}
        </p>{/if}{#each suggestions as item}<button
          class="slot"
          disabled={locked}
          on:click={() =>
            create({ ...item, kind: "study", title: "Work session" })}
          >{showDate(dayOf(item.starts_at))} · {time(item.starts_at)}–{time(
            item.ends_at,
          )} · Preview reservation</button
        >{/each}
    </section>
  </div>
  <div class="week-bottom">
    <section
      class="day-agenda"
      bind:this={dayPanel}
      tabindex="-1"
      aria-label="Selected day agenda"
    >
      <div class="row spread">
        <div>
          <p class="eyebrow">Day at a glance</p>
          <h2>{selectedDay ? dayLabel(selectedDay) : "Your day"}</h2>
        </div>
        <button
          class="workspace-action"
          disabled={locked || loading}
          on:click={() => create()}
          ><Icon name="plus" size={14} /> Add event</button
        >
      </div>
      <div
        class="agenda-list"
        role="region"
        aria-label="All events for the selected day"
      >
        {#each onDay(calendar?.occurrences || [], selectedDay, zone) as item (item.id)}
          <button
            class="agenda-event"
            disabled={locked || loading}
            on:click={() => edit(item)}
            ><span class="agenda-time"
              >{timeLabel(item, selectedDay, zone)}{#if !item.all_day}<small
                  >– {time(item.ends_at)}</small
                >{/if}</span
            ><span
              ><strong>{item.title}</strong><small
                >{item.location ||
                  item.meeting_type ||
                  (item.kind === "study" ? "Work session" : item.kind)}</small
              ></span
            ></button
          >
        {:else}<p class="empty-day">
            {loading
              ? "Loading your day…"
              : error && !calendar
                ? "The schedule could not be loaded."
                : "Nothing scheduled. A little breathing room."}
          </p>{/each}
        {#each calendar?.deadlines.filter((item) => item.due === selectedDay) || [] as item}<button
            class="agenda-event due"
            on:click={onTasks}
            ><span class="agenda-time">Due</span><strong>{item.title}</strong
            ></button
          >{/each}
      </div>
    </section>
    <section class="task-backlog">
      <div class="row spread">
        <div>
          <p class="eyebrow">Make progress</p>
          <h2>Tasks to plan</h2>
        </div>
        <button class="workspace-action" on:click={onTasks}
          ><Icon name="arrow" size={14} /> All tasks</button
        >
      </div>
      {#if linkNext !== null}<p class="notice">
          Task links are partially loaded. Load more before choosing a section
          or estimate.
        </p>
        <button disabled={loading} on:click={moreLinks}>More task links</button
        >{/if}
      {#each tasks.slice(0, visibleTasks) as task}{@const taskLinks =
          links.filter((row) => row.data.task_id === task.id)}{@const link =
          taskLinks.length === 1 && linkNext === null
            ? taskLinks[0]
            : null}{@const planned = minutesFor(task.id)}
        <div class="task-line">
          <strong>{task.title}</strong>
          <p class="muted">
            {task.due ? `Due ${task.due} · ` : ""}{planned
              ? `${planned} min reserved this week`
              : "No session this week"}{link
              ? ` · Estimate ${link.data.estimate_minutes} min`
              : ""}
          </p>
          {#if taskLinks.length > 1}<p class="muted">
              Linked to multiple places. Choose a section when reserving a
              session.
            </p>{/if}
          <button
            disabled={locked}
            on:click={() =>
              create({
                kind: "study",
                title: task.title,
                task_id: task.id,
                course_id: link?.data.course_id || null,
                material: link?.data.material || [],
              })}>Reserve a session</button
          >{#each taskLinks as courseLink}<button
              class="plain"
              on:click={() =>
                courseLink.data.course_id
                  ? onCourse(courseLink.data.course_id)
                  : onProject(courseLink.data.semester_id!)}
              >{taskLinks.length === 1
                ? courseLink.data.course_id
                  ? "Open section"
                  : "Open project"
                : $workspace.snapshot?.courses.find(
                    (row) => row.id === courseLink.data.course_id,
                  )?.data.title ||
                  $workspace.snapshot?.semesters.find(
                    (row) => row.id === courseLink.data.semester_id,
                  )?.data.title ||
                  "Open linked project"}</button
            >{/each}
        </div>{:else}<p class="muted">
          {loading
            ? "Loading tasks…"
            : tasksReady
              ? "No open tasks on this page."
              : "Tasks could not be loaded. Try Refresh."}
        </p>{/each}
      {#if tasks.length > visibleTasks}<button
          class="plain"
          on:click={() => (visibleTasks += 6)}
          >Show more tasks ({tasks.length - visibleTasks})</button
        >{/if}
      {#if taskNext !== null}<button disabled={loading} on:click={moreTasks}
          >More original tasks</button
        >{/if}
    </section>
  </div>
  <details class="calendar-notes">
    <summary>Calendar sources &amp; reminders</summary>
    <p class="muted">
      This view contains your Kejtu schedule. Selected desktop calendars are
      shown separately in the Upcoming widget and do not block work suggestions.
    </p>
    {#if calendar}<p class="muted">
        {calendar.reminder_notice} A calendar block creates no reminder automatically.
      </p>{/if}
  </details>
  {#if editor}<EventEditor
      {workspace}
      busy={locked}
      {record}
      {exception}
      initial={seed}
      occurrenceAt={occurrence}
      onCancel={() => (editor = false)}
      onDone={() => {
        editor = false;
        void refresh();
      }}
    />{/if}
</section>

<style>
  .calendar-options {
    margin: 0 0 12px;
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .calendar-options summary {
    width: fit-content;
    margin-left: auto;
    cursor: pointer;
  }
  .calendar-controls {
    justify-content: flex-end;
    margin-top: 8px;
  }

  .eyebrow {
    font: 9px var(--ke-font-mono);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ke-text-tertiary);
    margin: 0 0 12px;
  }
  .calendar-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 14px;
  }
  .calendar-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .calendar-controls input {
    width: 142px;
    flex: 0 0 142px;
    font-size: 11px;
    padding: 5px;
  }
  .calendar-toolbar button:not(.workspace-action) {
    font-size: 11px;
    padding: 5px 9px;
  }
  .date-range {
    font-size: 12px;
    margin-left: 10px;
  }
  .calendar-caption {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    color: var(--ke-text-tertiary);
    font-size: 10px;
    padding: 10px 0 24px;
  }
  .week-bottom {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: calc(36px * var(--ke-density));
  }
  .week-bottom h2,
  .planning-panel h2 {
    font:
      400 22px Georgia,
      serif;
    margin: 0 0 16px;
  }
  .week-bottom .eyebrow {
    margin-bottom: 8px;
  }
  .week-bottom > section {
    min-width: 0;
    border-top: 1px solid var(--ke-border);
    padding-top: 20px;
  }
  .agenda-event {
    display: grid;
    grid-template-columns: 80px minmax(0, 1fr);
    gap: 12px;
    width: 100%;
    text-align: left;
    border: 0;
    border-bottom: 1px solid var(--ke-border);
    border-radius: 0;
    padding: 12px 0;
    background: none;
  }
  .agenda-event strong {
    font-size: 12px;
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  .agenda-event small {
    display: block;
    font-size: 10px;
    margin-top: 4px;
    color: var(--ke-text-tertiary);
  }
  .agenda-time {
    font-size: 11px;
    color: var(--ke-text-secondary);
  }
  .empty-day {
    font-size: 12px;
    color: var(--ke-text-tertiary);
    padding: 12px 0;
  }
  .task-line {
    padding: 12px 0;
    border-bottom: 1px solid var(--ke-border);
  }
  .task-line strong {
    font-size: 12px;
    font-weight: 500;
  }
  .task-line p {
    font-size: 11px;
    margin: 5px 0;
  }
  .task-line button {
    font-size: 11px;
    padding: 4px 8px;
    margin-right: 8px;
  }
  .planning-panel {
    border: 1px solid var(--ke-border);
    border-radius: 8px;
    padding: 20px;
    margin: 0 0 24px;
  }
  .planning-panel[hidden] {
    display: none;
  }
  .planning-panel .eyebrow {
    margin-bottom: 8px;
  }
  .cutoff-help {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
    color: var(--ke-text-tertiary);
    font-size: 11px;
  }
  .cutoff-help button {
    font-size: inherit;
    padding: 2px 0;
  }
  .slot {
    display: block;
    margin-top: 8px;
    font-size: 12px;
  }
  .calendar-notes {
    margin-top: 24px;
    color: var(--ke-text-tertiary);
  }
  .calendar-notes summary {
    font-size: 10px;
  }
  .day-agenda:focus-visible {
    outline: 2px solid var(--ke-accent);
    outline-offset: 4px;
  }
  @media (max-width: 850px) {
    .week-bottom {
      grid-template-columns: 1fr;
      gap: 22px;
    }
    .date-range {
      margin-left: 0;
    }
    .calendar-caption {
      flex-wrap: wrap;
      gap: 5px;
    }
  }
</style>
