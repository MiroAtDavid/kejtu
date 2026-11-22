<script lang="ts">
  import "$lib/design/collection.css";
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import type { TaskRecord } from "$lib/api/wire.generated";
  import TaskRows from "$lib/features/tasks/TaskRows.svelte";
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
  import MonthCalendar from "./MonthCalendar.svelte";
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
  import Timeline from "$lib/design/Timeline.svelte";
  import { dueTimelineItem, eventTimelineItem } from "./course-timeline";
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
  /** Open one task (its read reference) on the Tasks page. */
  export let onTask: (ref: string) => void = () => {};
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
  let tasks: TaskRecord[] = [],
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
  // Week or month view. The month shows whole weeks (Monday first), so a few
  // days of the neighbouring months appear with their plans; totals and the
  // slot search cover the month itself.
  const VIEW_KEY = "kejtu.calendar.view";
  let mode: "week" | "month" = "week";
  try {
    if (localStorage.getItem(VIEW_KEY) === "month") mode = "month";
  } catch {
    // A per-viewer convenience only; the week is the default.
  }
  let month = localDate().slice(0, 7);
  $: period = mode === "month" ? "month" : "week";
  const nextMonth = (first: string) => {
    const date = new Date(first + "T12:00:00Z");
    date.setUTCMonth(date.getUTCMonth() + 1, 1);
    return date.toISOString().slice(0, 10);
  };
  const previousMonth = (first: string) => {
    const date = new Date(first + "T12:00:00Z");
    date.setUTCMonth(date.getUTCMonth() - 1, 1);
    return date.toISOString().slice(0, 10);
  };
  function shownDays() {
    if (mode === "week")
      return Array.from({ length: 7 }, (_, i) => addDay(start, i));
    const first = month + "-01";
    const until = addDay(firstMonday(addDay(nextMonth(first), -1)), 7);
    const result: string[] = [];
    for (let day = firstMonday(first); day < until; day = addDay(day, 1))
      result.push(day);
    return result;
  }
  function bounds(firstDay: string, endDay: string) {
    return {
      first: civilInstant(firstDay + "T00:00", zone),
      last: civilInstant(endDay + "T00:00", zone),
      timezone: zone,
    };
  }
  /** Everything displayed: the week, or the month's whole weeks. */
  function shownRange() {
    return bounds(days[0], addDay(days[days.length - 1], 1));
  }
  function setMode(value: "week" | "month") {
    if (value === mode || loading) return;
    const anchor = selectedDay || start;
    if (value === "month") month = anchor.slice(0, 7);
    else
      start = firstMonday(
        anchor.slice(0, 7) === month ? anchor : month + "-01",
      );
    mode = value;
    try {
      localStorage.setItem(VIEW_KEY, value);
    } catch {
      // Not remembered; the choice still applies now.
    }
    void refresh();
  }
  function step(direction: -1 | 1) {
    if (mode === "month")
      month = (
        direction < 0 ? previousMonth(month + "-01") : nextMonth(month + "-01")
      ).slice(0, 7);
    else start = addDay(start, 7 * direction);
    void refresh();
  }
  function current() {
    const today = dayInZone(new Date(), zone);
    month = today.slice(0, 7);
    start = firstMonday(today);
    void refresh();
  }
  /** The period itself: one week, or one month. */
  function range() {
    if (mode === "month")
      return bounds(month + "-01", nextMonth(month + "-01"));
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
      days = shownDays();
      if (!days.includes(selectedDay))
        selectedDay = days.includes(dayInZone(new Date(), zone))
          ? dayInZone(new Date(), zone)
          : mode === "month"
            ? month + "-01"
            : start;
      const counted = range();
      firstMs = Date.parse(counted.first);
      lastMs = Date.parse(counted.last);
      calendar = await workspaceRequest<Calendar>(
        "calendar.view",
        shownRange(),
      );
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
        ...shownRange(),
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
          : `No future time remains in this ${period}. Choose a later ${period}.`;
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
            ? `No study slots fit this ${period}'s search before ${showDate(before)}. That day is excluded. Clear the date or choose a later one to include it.`
            : `No study slots fit the remaining time in this ${period}. Try another ${period} or a shorter session.`) +
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
    month = dayInZone(new Date(), zone).slice(0, 7);
    mounted = true;
    if (initial) create(initial);
  });
  $: timed =
    calendar?.occurrences.filter((item) => item.kind !== "deadline") || [];
  $: studyMinutes = timed
    .filter((item) => item.kind === "study")
    .reduce((sum, item) => sum + duration(item), 0);
  $: locked = busy || $workspace.busy || !!$workspace.pending;
  // The selected day as the module appointments show theirs: the same
  // timeline rows, events first, then the tasks due that day.
  $: dayItems = onDay(calendar?.occurrences || [], selectedDay, zone);
  $: dayDue =
    calendar?.deadlines.filter((item) => item.due === selectedDay) || [];
  $: dayEntries = [
    // An occurrence has no repeat rule or zone of its own: it is shown in the
    // Calendar's zone, as the week above shows it.
    ...dayItems.map((item) =>
      eventTimelineItem(item.id, {
        ...item,
        timezone: zone,
        repeat: "none",
        interval: 1,
        until: null,
      }),
    ),
    ...dayDue.map((item) =>
      dueTimelineItem("due:" + item.id, item.title, item.due),
    ),
  ];
  // The line under the title, as on the other pages: the week, what it holds,
  // and the zone only when it is not this computer's.
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  $: classes = timed.filter((item) => item.kind === "class").length;
  $: status = [
    mode === "month"
      ? new Date(month + "-15T12:00:00Z").toLocaleDateString(undefined, {
          month: "long",
          year: "numeric",
          timeZone: "UTC",
        })
      : `${showDate(start)} – ${showDate(addDay(start, 6))}`,
    ...(calendar
      ? [
          `${classes} ${classes === 1 ? "class" : "classes"}`,
          `${Math.round(studyMinutes / 6) / 10} h reserved`,
        ]
      : []),
    ...(zone !== localZone ? [zone] : []),
  ].join(" · ");
  // A task's place, as the task rows name it elsewhere: its module, else its project.
  function placeOf(task: TaskRecord) {
    const place = task.placements?.[0];
    if (!place) return "";
    return (
      $workspace.snapshot?.courses.find((row) => row.id === place.section_id)
        ?.data.title ??
      $workspace.snapshot?.semesters.find((row) => row.id === place.project_id)
        ?.data.title ??
      ""
    );
  }
  // The "···" options close on a click outside them, or on Escape.
  let calendarMenu: HTMLDetailsElement | undefined;
  function dismissMenu(event: PointerEvent | KeyboardEvent) {
    if (!calendarMenu?.open) return;
    if (event instanceof KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (calendarMenu.contains(document.activeElement))
        calendarMenu.querySelector("summary")?.focus({ preventScroll: true });
    } else if (
      event.target instanceof Node &&
      calendarMenu.contains(event.target)
    )
      return;
    calendarMenu.open = false;
  }
</script>

<svelte:window on:pointerdown={dismissMenu} on:keydown={dismissMenu} />

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

{#snippet reserveButton(task: TaskRecord)}
  {@const taskLinks = links.filter((row) => row.data.task_id === task.id)}
  {@const link =
    taskLinks.length === 1 && linkNext === null ? taskLinks[0] : null}
  {@const planned = minutesFor(task.id)}
  <span class="reserve">
    {#if planned}<span class="reserved">{planned} min this {period}</span>{/if}
    <button
      class="mini"
      disabled={locked}
      title="Reserve a session"
      aria-label={`Reserve a session for ${task.title}`}
      on:click={() =>
        create({
          kind: "study",
          title: task.title,
          task_id: task.id,
          course_id: link?.data.course_id || null,
          material: link?.data.material || [],
        })}>Reserve</button
    >
  </span>
{/snippet}

<section class="uni study-week" aria-label="Calendar">
  <div class="collection-page calendar-head">
    <div class="cp-head">
      <div>
        <h1 class="cp-title">Calendar</h1>
        <p class="cp-status">{status}</p>
      </div>
    </div>
    <div class="cp-bar calendar-bar">
      <div class="week-nav" role="group" aria-label={`Change ${period}`}>
        <button
          disabled={loading}
          aria-label={`Previous ${period}`}
          on:click={() => step(-1)}>‹</button
        >
        <button disabled={loading} on:click={current}>This {period}</button>
        <button
          disabled={loading}
          aria-label={`Next ${period}`}
          on:click={() => step(1)}>›</button
        >
        <span class="view-switch" role="group" aria-label="Calendar view">
          <button
            disabled={loading}
            aria-pressed={mode === "week"}
            on:click={() => setMode("week")}>Week</button
          ><button
            disabled={loading}
            aria-pressed={mode === "month"}
            on:click={() => setMode("month")}>Month</button
          >
        </span>
      </div>
      <div class="bar-actions">
        <button disabled={locked || loading} on:click={() => create()}
          >Add event</button
        >
        <button
          disabled={locked}
          aria-expanded={planningOpen}
          on:click={() => (planningOpen = !planningOpen)}>Plan work time</button
        >
        <details class="calendar-menu" bind:this={calendarMenu}>
          <summary aria-label="Calendar options" title="Calendar options"
            >···</summary
          >
          <div class="calendar-menu-list">
            <button
              class="plain"
              aria-expanded={expanded}
              aria-controls="study-week-calendar"
              on:click={() => (expanded = !expanded)}
              >{expanded ? "Collapse calendar" : "Expand calendar"}</button
            >
            <label class="week-pick"
              >{mode === "month" ? "Month" : "Week"} containing<input
                type="date"
                disabled={loading}
                value={mode === "month" ? month + "-01" : start}
                on:change={(event) => {
                  const value = event.currentTarget.value;
                  if (value) {
                    if (mode === "month") month = value.slice(0, 7);
                    else start = firstMonday(value);
                    void refresh();
                  }
                }}
              /></label
            >
            <button class="plain" disabled={loading} on:click={refresh}
              >{loading ? "Loading…" : "Refresh"}</button
            >
            <p class="muted">
              This view holds your Kejtu schedule. Selected desktop calendars
              are shown separately in the Upcoming widget and do not block work
              suggestions.{#if calendar}{" "}
                {calendar.reminder_notice} A calendar block creates no reminder automatically.{/if}
            </p>
          </div>
        </details>
      </div>
    </div>
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div id="study-week-calendar">
    {#if mode === "month"}
      <MonthCalendar
        {days}
        {month}
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
    {:else}
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
    {/if}
  </div>
  {#if calendar?.deadline_next_offset !== null && calendar?.deadline_next_offset !== undefined}<button
      disabled={loading}
      on:click={moreDeadlines}>More deadlines</button
    >{/if}
  <div class="planning-panel" hidden={!planningOpen}>
    <div class="row spread">
      <h2>Find time to work</h2>
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
            >Find slots this {period}</button
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
      <div class="section-head">
        <h2>{selectedDay ? dayLabel(selectedDay) : "Your day"}</h2>
      </div>
      <div
        class="agenda-list"
        role="region"
        aria-label="All events for the selected day"
      >
        {#if dayEntries.length}<Timeline
            compact
            items={dayEntries}
            label={`Schedule for ${selectedDay ? dayLabel(selectedDay) : "the day"}`}
          >
            <svelte:fragment slot="actions" let:item={entry}>
              {@const occurrence = dayItems.find((row) => row.id === entry.id)}
              {#if occurrence}<button
                  class="timeline-edit"
                  title={`Edit ${occurrence.repeat === "none" ? "event" : "series"}: ${entry.title}`}
                  aria-label={`Edit ${occurrence.repeat === "none" ? "event" : "series"}: ${entry.title}`}
                  disabled={locked || loading}
                  on:click={() => edit(occurrence)}>{@render pencil()}</button
                >{:else}<button
                  class="timeline-edit"
                  title={`Open task: ${entry.title}`}
                  aria-label={`Open task: ${entry.title}`}
                  on:click={onTasks}>{@render pencil()}</button
                >{/if}
            </svelte:fragment>
          </Timeline>{:else}<p class="empty-day">
            {loading
              ? "Loading your day…"
              : error && !calendar
                ? "The schedule could not be loaded."
                : "Nothing scheduled. A little breathing room."}
          </p>{/if}
      </div>
    </section>
    <section class="task-backlog">
      <div class="section-head">
        <h2>Tasks to plan</h2>
        <button disabled={locked} on:click={onTasks}>All tasks</button>
      </div>
      {#if linkNext !== null}<p class="notice">
          Task links are partially loaded. Load more before choosing a module or
          estimate.
        </p>
        <button disabled={loading} on:click={moreLinks}>More task links</button
        >{/if}
      {#if tasks.length}<div class="collection-page task-plan">
          <TaskRows
            items={tasks.slice(0, visibleTasks)}
            grouped
            completion={false}
            disabled={locked}
            placementLabel={placeOf}
            onOpen={(task) => onTask(task.read_ref)}
            action={reserveButton}
          />
        </div>{:else}<p class="muted">
          {loading
            ? "Loading tasks…"
            : tasksReady
              ? "No open tasks on this page."
              : "Tasks could not be loaded. Try Refresh."}
        </p>{/if}
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
  /* The width of the other pages: the shared 800px column, head to tasks. */
  .study-week {
    max-width: 800px;
  }
  .view-switch {
    display: inline-flex;
    margin-left: 8px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    overflow: hidden;
  }
  .view-switch button {
    border: 0;
    border-radius: 0;
  }
  .view-switch button[aria-pressed="true"] {
    background: var(--ke-surface-inset);
    font-weight: 600;
  }
  .calendar-bar {
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .week-nav,
  .bar-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .calendar-menu {
    position: relative;
  }
  .calendar-menu summary {
    padding: 5px 9px;
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
    cursor: pointer;
    list-style: none;
  }
  .calendar-menu summary::-webkit-details-marker {
    display: none;
  }
  .calendar-menu-list {
    position: absolute;
    z-index: 6;
    top: calc(100% + 6px);
    right: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    width: max-content;
    max-width: min(300px, 80vw);
    padding: 10px 12px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-overlay-shadow);
  }
  .week-pick {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
  }
  /* Half a page wide: the title keeps the room the note preview would take. */
  .task-plan :global(.task-row .note) {
    display: none;
  }
  .week-pick input {
    width: 150px;
    padding: 5px;
    font-size: var(--ke-text-2xs);
  }
  .calendar-menu-list .muted {
    margin: 2px 0 0;
    font-size: var(--ke-text-3xs);
    white-space: normal;
  }
  /* Both section heads are rows of one height, so the two titles line up
     whether or not a button sits beside them. */
  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: var(--ke-control-height);
    margin-bottom: 16px;
  }
  .section-head h2 {
    margin: 0;
  }
  /* Reserve sits at the end of a task row, a small button as on the project page. */
  .reserve {
    display: flex;
    flex: none;
    align-items: center;
    gap: 8px;
  }
  .reserved {
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
    white-space: nowrap;
  }
  .mini {
    min-height: 24px;
    padding: 0 9px;
    border: 0;
    border-radius: 6px;
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: 11.5px;
    font-weight: 500;
    white-space: nowrap;
  }
  .mini:hover:not(:disabled) {
    background: var(--ke-border-strong);
  }

  .week-bottom {
    margin-top: 20px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: calc(36px * var(--ke-density));
  }
  .week-bottom h2,
  .planning-panel h2 {
    font: var(--ke-title-section);
    letter-spacing: var(--ke-title-section-tracking);
    margin: 0 0 16px;
  }
  .week-bottom > section {
    min-width: 0;
    border-top: 1px solid var(--ke-border);
    padding-top: 20px;
  }
  .timeline-edit {
    min-width: 28px;
    min-height: 28px;
    justify-content: center;
  }
  .empty-day {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-tertiary);
    padding: 12px 0;
  }
  .planning-panel {
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    padding: 20px;
    margin: 0 0 24px;
  }
  .planning-panel[hidden] {
    display: none;
  }
  .cutoff-help {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .cutoff-help button {
    font-size: inherit;
    padding: 2px 0;
  }
  .slot {
    display: block;
    margin-top: 8px;
    font-size: var(--ke-text-xs);
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
  }
</style>
