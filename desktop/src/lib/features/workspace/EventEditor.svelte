<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import { drawerFocus } from "./drawer";
  import {
    civilInstant,
    civilValue,
    localDate,
    workspaceRequest,
    type Workspace,
    type Event,
    type RecordRow,
  } from "./workspace";
  export let workspace: Workspace;
  export let busy = false;
  export let record: RecordRow<Event> | null = null;
  export let initial: Partial<Event> = {};
  export let occurrenceAt: string | null = null;
  export let exception: RecordRow | null = null;
  export let onDone: () => void = () => {};
  export let onCancel: () => void = () => {};
  let title = "",
    start = localDate() + "T09:00",
    end = localDate() + "T10:00",
    timezone = "UTC",
    kind: Event["kind"] = "appointment",
    recurrence = "none",
    until = "",
    course = "",
    task = "",
    location = "",
    meeting = "",
    fold: "earlier" | "later" = "earlier";
  let endFold: "earlier" | "later" = "earlier";
  let scope = "occurrence",
    allDay = false,
    allow = false,
    error = "",
    checking = false;
  let previewed = "";
  let clashes: { title: string; starts_at: string; ends_at: string }[] = [];
  let tasks: { id: string; title: string }[] = [];
  let taskNext: number | null = null;
  async function loadTasks(more = false) {
    try {
      const result = await request("tasks", {
        action: "list",
        outcome: "open",
        attention: "all",
        limit: 50,
        offset: more ? taskNext || 0 : 0,
      });
      if (!("items" in result))
        throw new Error("The original task list could not be supplied.");
      tasks = more ? [...tasks, ...result.items] : result.items;
      taskNext = result.next_offset;
    } catch (caught) {
      error = String(caught);
    }
  }
  onMount(() => {
    const value = { ...record?.data, ...initial };
    timezone =
      value.timezone ||
      $workspace.snapshot?.settings.availability.timezone ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "UTC";
    title = value.title || "";
    kind = value.kind || "appointment";
    start = value.starts_at ? civilValue(value.starts_at, timezone) : start;
    end = value.ends_at ? civilValue(value.ends_at, timezone) : end;
    if (
      value.starts_at &&
      Date.parse(civilInstant(start, timezone)) !== Date.parse(value.starts_at)
    )
      fold = "later";
    if (
      value.ends_at &&
      Date.parse(civilInstant(end, timezone)) !== Date.parse(value.ends_at)
    )
      endFold = "later";
    recurrence =
      value.repeat === "weekly" && value.interval === 2
        ? "fortnightly"
        : value.repeat || "none";
    until = value.until || "";
    course = value.course_id || "";
    task = value.task_id || "";
    location = value.location || "";
    meeting = value.meeting_type || "";
    allDay = value.all_day || false;
    // The real task owner supplies original IDs/titles; no copied task records.
    void loadTasks();
  });
  function changeScope() {
    const value = scope === "series" ? record?.data : initial;
    if (value?.starts_at) start = civilValue(value.starts_at, timezone);
    if (value?.ends_at) end = civilValue(value.ends_at, timezone);
    fold =
      value?.starts_at &&
      Date.parse(civilInstant(start, timezone)) !== Date.parse(value.starts_at)
        ? "later"
        : "earlier";
    endFold =
      value?.ends_at &&
      Date.parse(civilInstant(end, timezone)) !== Date.parse(value.ends_at)
        ? "later"
        : "earlier";
    previewed = "";
    clashes = [];
    allow = false;
  }
  $: locked = busy || $workspace.busy || !!$workspace.pending || checking;
  function value() {
    if (occurrenceAt && scope === "occurrence")
      return {
        kind: "exception",
        data: {
          event_id: record!.id,
          occurrence_at: occurrenceAt,
          cancelled: false,
          starts_at: civilInstant(start, timezone, fold),
          ends_at: civilInstant(end, timezone, endFold),
        },
        ...(exception
          ? { id: exception.id, expected_revision: exception.revision }
          : {}),
      };
    return {
      kind: "event",
      data: {
        title,
        starts_at: civilInstant(start, timezone, fold),
        ends_at: civilInstant(end, timezone, endFold),
        timezone,
        kind,
        repeat: recurrence === "fortnightly" ? "weekly" : recurrence,
        interval: recurrence === "fortnightly" ? 2 : 1,
        until: recurrence === "none" ? null : until || null,
        course_id: course || null,
        task_id: task || null,
        location,
        meeting_type: meeting,
        all_day: allDay,
        material: initial.material ?? record?.data.material ?? [],
      },
      ...(record ? { id: record.id, expected_revision: record.revision } : {}),
    };
  }
  async function preview() {
    error = "";
    checking = true;
    try {
      const item = value();
      const result = await workspaceRequest<{ conflicts: typeof clashes }>(
        "calendar.preview",
        {
          kind: item.kind,
          data: item.data,
          ...(item.id ? { id: item.id } : {}),
        },
      );
      clashes = result.conflicts;
      previewed = JSON.stringify(item);
      allow = false;
    } catch (caught) {
      error = String(caught);
    } finally {
      checking = false;
    }
  }
  async function save() {
    try {
      const item = value();
      if (previewed !== JSON.stringify(item)) {
        error = "Preview this timetable change before saving.";
        return;
      }
      if (clashes.length && !allow) {
        error = "Review the overlaps before saving.";
        return;
      }
      const result = await workspace.mutate("record.save", {
        ...item,
        allow_conflicts: allow,
      });
      if (result) onDone();
    } catch (caught) {
      error = String(caught);
    }
  }
  async function cancelEvent() {
    try {
      const item =
        occurrenceAt && scope === "occurrence"
          ? {
              kind: "exception",
              data: {
                event_id: record!.id,
                occurrence_at: occurrenceAt,
                cancelled: true,
                starts_at: null,
                ends_at: null,
              },
              ...(exception
                ? { id: exception.id, expected_revision: exception.revision }
                : {}),
            }
          : {
              kind: "event",
              id: record!.id,
              expected_revision: record!.revision,
              data: record!.data,
              cancelled: true,
            };
      if (await workspace.mutate("record.save", item)) onDone();
    } catch (caught) {
      error = String(caught);
    }
  }
</script>

<div
  class="uni drawer"
  use:drawerFocus={{ close: onCancel, busy: $workspace.busy }}
  tabindex="-1"
  role="dialog"
  aria-modal="false"
  aria-label={record ? "Edit commitment" : "New commitment"}
>
  <div class="row spread">
    <h2>{record ? "Edit commitment" : "Reserve time / add commitment"}</h2>
    <button disabled={$workspace.busy} on:click={onCancel}>Close</button>
  </div>
  <form on:submit|preventDefault={save}>
    {#if occurrenceAt}<label
        >Change<select bind:value={scope} on:change={changeScope}
          ><option value="occurrence">This occurrence only</option><option
            value="series">The whole series</option
          ></select
        ></label
      >{/if}
    {#if !occurrenceAt || scope === "series"}
      <label>Title<input required maxlength="300" bind:value={title} /></label>
      <div class="row">
        <label
          >Type<select bind:value={kind}
            ><option value="class">Class</option><option value="study"
              >Work session</option
            ><option value="work">Work shift</option><option value="appointment"
              >Appointment</option
            ><option value="exam">Exam</option><option value="deadline"
              >Deadline</option
            ></select
          ></label
        ><label
          >Section<select bind:value={course}
            ><option value="">Independent</option
            >{#each $workspace.snapshot?.courses ?? [] as row}<option
                value={row.id}>{row.data.title}</option
              >{/each}</select
          ></label
        >
      </div>
      {#if kind === "class"}<label
          >Meeting<select bind:value={meeting}
            ><option value="">Choose meeting type</option><option
              value="lecture">Lecture</option
            ><option value="tutorial">Tutorial</option><option value="seminar"
              >Seminar</option
            ><option value="lab">Lab</option></select
          ></label
        >{/if}
    {/if}
    <div class="row">
      <label
        >Starts<input
          type="datetime-local"
          required
          bind:value={start}
        /></label
      ><label
        >Ends<input type="datetime-local" required bind:value={end} /></label
      >
    </div>
    <label>Timezone<input required bind:value={timezone} /></label>
    <details>
      <summary>Daylight-saving ambiguity</summary><label
        >When the start time occurs twice<select bind:value={fold}
          ><option value="earlier">Use the earlier occurrence</option><option
            value="later">Use the later occurrence</option
          ></select
        ></label
      >
      <label
        >When the end time occurs twice<select bind:value={endFold}
          ><option value="earlier">Use the earlier occurrence</option><option
            value="later">Use the later occurrence</option
          ></select
        ></label
      >
    </details>
    {#if !occurrenceAt || scope === "series"}
      <div class="row">
        <label
          >Repeats<select bind:value={recurrence}
            ><option value="none">Specific date only</option><option
              value="weekly">Every week</option
            ><option value="fortnightly">Every two weeks</option><option
              value="daily">Every day</option
            ></select
          ></label
        >{#if recurrence !== "none"}<label
            >Last date<input type="date" bind:value={until} /><small
              >Uses the project end when set. Otherwise, choose a last date.</small
            ></label
          >{/if}
      </div>
      <label>Location<input bind:value={location} /></label>
      <label
        >Original task<select bind:value={task}
          ><option value="">No task linked</option
          >{#if task && !tasks.some((item) => item.id === task)}<option
              value={task}>Saved original task · {task.slice(0, 8)}</option
            >{/if}{#each tasks as item}<option value={item.id}
              >{item.title}</option
            >{/each}</select
        ><small>Task completion and reminders remain separate.</small></label
      >
      {#if taskNext !== null}<button
          type="button"
          disabled={locked}
          on:click={() => loadTasks(true)}>More original tasks</button
        >{/if}
      <label
        ><input type="checkbox" bind:checked={allDay} />All-day marker</label
      >
    {/if}
    <button type="button" disabled={locked} on:click={preview}
      >Preview timetable change</button
    >
    {#if previewed}<p class="muted">
        {clashes.length
          ? `${clashes.length} overlapping commitments`
          : "No overlapping commitments in the proposed range."}
      </p>{/if}
    {#each clashes as clash}<p class="notice">
        {clash.title} · {clash.starts_at} – {clash.ends_at}
      </p>{/each}
    {#if clashes.length}<label
        ><input type="checkbox" bind:checked={allow} />I have reviewed these
        overlaps and want to reserve this time anyway.</label
      >{/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="row">
      <button disabled={locked || !previewed}>Save commitment</button
      >{#if record}<button
          type="button"
          disabled={locked}
          on:click={cancelEvent}
          >{occurrenceAt && scope === "occurrence"
            ? "Cancel this occurrence"
            : "Cancel this series / event"}</button
        >{/if}
    </div>
  </form>
</div>
