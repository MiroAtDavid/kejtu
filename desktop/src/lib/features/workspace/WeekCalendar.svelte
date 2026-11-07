<script lang="ts">
  import {
    dayInZone,
    dayLabel,
    onDay,
    timeLabel,
    type Occurrence,
    type Deadline,
  } from "./calendar-view";
  export let days: string[] = [];
  export let items: Occurrence[] = [];
  export let deadlines: Deadline[] = [];
  export let zone = "UTC";
  export let selectedDay = "";
  export let compact = true;
  export let busy = false;
  export let loading = false;
  export let unavailable = false;
  export let onEdit: (item: Occurrence) => void = () => {};
  export let onTasks: () => void = () => {};
  export let onSelect: (day: string) => void = () => {};
  $: limit = compact ? 3 : 5;
  // Refreshing the displayed days also refreshes the marker after midnight.
  $: today = days.length ? dayInZone(new Date(), zone) : "";
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users scroll the bounded week region.) -->
<div
  class="calendar-scroll"
  tabindex="0"
  role="region"
  aria-label="Week calendar; scroll horizontally on small screens"
>
  <div class="week-board" class:compact>
    {#each days as day}
      {@const events = onDay(items, day, zone)}
      {@const due = deadlines.filter((item) => item.due === day)}
      {@const hiddenCount = Math.max(0, events.length + due.length - limit)}
      <section
        class="day"
        class:today={day === today}
        class:selected={day === selectedDay}
        aria-label={dayLabel(day)}
      >
        <button
          class="day-heading"
          aria-label={`Show ${dayLabel(day)}`}
          aria-pressed={day === selectedDay}
          on:click={() => onSelect(day)}
        >
          <span
            >{new Date(day + "T12:00:00Z").toLocaleDateString(undefined, {
              weekday: "short",
              timeZone: "UTC",
            })}</span
          >
          <strong>{Number(day.slice(-2))}</strong>
          <small>{events.length + due.length || "—"}</small>
        </button>
        <div class="day-events">
          {#each events.slice(0, limit) as item (item.id)}
            <button
              class="calendar-event"
              data-kind={item.kind}
              disabled={busy}
              title={`${timeLabel(item, day, zone)} · ${item.title}`}
              on:click={() => onEdit(item)}
            >
              <time>{timeLabel(item, day, zone)}</time><span>{item.title}</span>
            </button>
          {/each}
          {#each due.slice(0, Math.max(0, limit - events.length)) as item (item.id)}
            <button
              class="calendar-event"
              data-kind="deadline"
              on:click={onTasks}
              title={`Due · ${item.title}`}
              ><time>Due</time><span>{item.title}</span></button
            >
          {/each}
          {#if !events.length && !due.length}<p class="day-empty">
              {loading ? "Loading…" : unavailable ? "Unavailable" : "No plans"}
            </p>{/if}
        </div>
        {#if hiddenCount}<button class="more-day" on:click={() => onSelect(day)}
            >+{hiddenCount} more <span aria-hidden="true">↓</span></button
          >{/if}
      </section>
    {/each}
  </div>
</div>

<style>
  .calendar-scroll {
    overflow-x: auto;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
  }
  .week-board {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    min-width: 700px;
  }
  .day {
    min-width: 0;
    padding: 10px 8px 8px;
    border-right: 1px solid var(--ke-border);
    min-height: 358px;
    display: flex;
    flex-direction: column;
  }
  .compact .day {
    min-height: 252px;
  }
  .day:last-child {
    border-right: 0;
  }
  .day.selected {
    background: var(--ke-surface-inset);
  }
  .day-heading {
    display: flex;
    align-items: center;
    gap: 7px;
    width: 100%;
    border: 0;
    background: none;
    padding: 2px 3px 12px;
    text-align: left;
  }
  .day-heading span {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
  }
  .day-heading strong {
    font-size: 17px;
    font-weight: 500;
  }
  .day-heading small {
    margin-left: auto;
    font-size: var(--ke-text-3xs);
    color: var(--ke-text-tertiary);
  }
  .today .day-heading strong {
    border-bottom: 2px solid var(--ke-accent);
  }
  .day-events {
    display: grid;
    align-content: start;
    gap: 5px;
  }
  .calendar-event {
    width: 100%;
    min-width: 0;
    height: 57px;
    display: block;
    text-align: left;
    padding: 5px 7px;
    border: 0;
    border-left: 2px solid var(--ke-marker-sand);
    background: var(--ke-surface);
    border-radius: 2px;
  }
  .calendar-event[data-kind="study"] {
    border-left-color: var(--ke-marker-sage);
  }
  .calendar-event[data-kind="work"] {
    border-left-color: var(--ke-marker-blue);
  }
  .calendar-event[data-kind="deadline"],
  .calendar-event[data-kind="exam"] {
    border-left-color: var(--ke-marker-rose);
  }
  .calendar-event time {
    display: block;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
    line-height: 14px;
  }
  .calendar-event span {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    font-size: var(--ke-text-2xs);
    line-height: 15px;
    overflow-wrap: anywhere;
  }
  .more-day {
    margin-top: auto;
    width: 100%;
    background: none;
    border: 0;
    text-align: left;
    padding: 8px 3px 0;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
  }
  .day-empty {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
    margin: 12px 3px;
  }
  button {
    cursor: pointer;
    color: var(--ke-text-primary);
    font: inherit;
  }
  button:focus-visible,
  .calendar-scroll:focus-visible {
    outline: 2px solid var(--ke-accent);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .calendar-event:hover:not(:disabled) {
    background: var(--ke-surface-inset);
  }
</style>
