<script lang="ts">
  // The month as a grid of whole weeks (Monday first), like other calendars.
  // Days outside the month are shown dimmed with their plans; a day opens in
  // the day panel below, an entry in the editor, as in the week view.
  import {
    clockTime,
    dayInZone,
    dayLabel,
    onDay,
    type Occurrence,
    type Deadline,
  } from "./calendar-view";
  export let days: string[] = [];
  /** The month shown, as YYYY-MM. */
  export let month = "";
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
  $: today = days.length ? dayInZone(new Date(), zone) : "";
  $: weekdays = days
    .slice(0, 7)
    .map((day) =>
      new Date(day + "T12:00:00Z").toLocaleDateString(undefined, {
        weekday: "short",
        timeZone: "UTC",
      }),
    );
  function entryTime(item: Occurrence, day: string) {
    if (item.all_day || dayInZone(item.starts_at, zone) < day) return "";
    return clockTime(item.starts_at, zone);
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users scroll the bounded month region.) -->
<div
  class="month-scroll"
  tabindex="0"
  role="region"
  aria-label="Month calendar; scroll horizontally on small screens"
>
  <div class="month-board" class:compact>
    {#each weekdays as name}
      <div class="weekday" aria-hidden="true">{name}</div>
    {/each}
    {#each days as day}
      {@const events = onDay(items, day, zone)}
      {@const due = deadlines.filter((item) => item.due === day)}
      {@const total = events.length + due.length}
      {@const shown = total > limit ? limit - 1 : limit}
      {@const hiddenCount = total - Math.min(total, shown)}
      <section
        class="cell"
        class:outside={day.slice(0, 7) !== month}
        class:today={day === today}
        class:selected={day === selectedDay}
        aria-label={dayLabel(day)}
      >
        <button
          class="cell-day"
          aria-label={`Show ${dayLabel(day)}`}
          aria-pressed={day === selectedDay}
          on:click={() => onSelect(day)}
          ><span>{Number(day.slice(-2))}</span></button
        >
        <div class="cell-events">
          {#each events.slice(0, shown) as item (item.id)}
            {@const time = entryTime(item, day)}
            <button
              class="entry"
              class:all-day={item.all_day}
              data-kind={item.kind}
              disabled={busy}
              title={`${time || (item.all_day ? "All day" : "Continues")} · ${item.title}`}
              on:click={() => onEdit(item)}
              >{#if time}<time>{time}</time>{/if}<span>{item.title}</span></button
            >
          {/each}
          {#each due.slice(0, Math.max(0, shown - events.length)) as item (item.id)}
            <button
              class="entry"
              data-kind="deadline"
              title={`Due · ${item.title}`}
              on:click={onTasks}><time>Due</time><span>{item.title}</span></button
            >
          {/each}
          {#if hiddenCount}<button
              class="more"
              on:click={() => onSelect(day)}>+{hiddenCount} more</button
            >{/if}
        </div>
      </section>
    {/each}
  </div>
  {#if loading || unavailable}<p class="month-state">
      {loading ? "Loading…" : "Unavailable"}
    </p>{/if}
</div>

<style>
  /* No frame, as the week: a rule above, thin lines between the days. */
  .month-scroll {
    overflow-x: auto;
    border-top: 1px solid var(--ke-border);
  }
  .month-board {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    min-width: 700px;
  }
  .weekday {
    padding: 6px 8px 5px;
    text-align: right;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
    border-bottom: 1px solid var(--ke-border);
  }
  /* Every day box has the same fixed size, as in other month calendars: a day
     shows the lines that fit (3, or 5 expanded), the last one "+N more" when
     there are more. Content never makes a week taller. */
  .cell {
    box-sizing: border-box;
    min-width: 0;
    height: 120px;
    overflow: hidden;
    padding: 3px 4px 4px;
    border-right: 1px solid var(--ke-border);
    border-bottom: 1px solid var(--ke-border);
    display: flex;
    flex-direction: column;
  }
  .compact .cell {
    height: 86px;
  }
  .cell:nth-of-type(7n) {
    border-right: 0;
  }
  .cell.selected {
    background: var(--ke-surface-inset);
  }
  /* The page's general button style (.uni button: 32 px minimum, control
     background) must not stretch the grid's slim lines and day numbers. */
  .cell .cell-day {
    align-self: flex-end;
    min-height: 0;
    border: 0;
    background: none;
    padding: 1px;
    line-height: 1;
  }
  .cell-day span {
    display: inline-grid;
    place-items: center;
    min-width: 22px;
    height: 22px;
    padding: 0 3px;
    border-radius: 11px;
    font-size: 13px;
    font-weight: 500;
    vertical-align: top;
  }
  .outside .cell-day span,
  .outside .entry {
    opacity: 0.45;
  }
  /* Today: only a quiet change of colour, no filled marker. */
  .today .cell-day span {
    color: var(--ke-accent);
  }
  .cell-events {
    display: grid;
    align-content: start;
    gap: 1px;
    min-width: 0;
  }
  /* One slim line per entry, as in other month calendars. */
  .entry {
    display: flex;
    gap: 4px;
    align-items: baseline;
    width: 100%;
    min-width: 0;
    min-height: 0;
    height: 16px;
    padding: 0 4px;
    border: 0;
    border-left: 2px solid var(--ke-marker-sand);
    border-radius: 2px;
    background: var(--ke-surface);
    text-align: left;
    font-size: var(--ke-text-2xs);
    line-height: 16px;
  }
  .entry.all-day {
    background: var(--ke-surface-inset);
  }
  .entry[data-kind="study"] {
    border-left-color: var(--ke-marker-sage);
  }
  .entry[data-kind="work"] {
    border-left-color: var(--ke-marker-blue);
  }
  .entry[data-kind="deadline"],
  .entry[data-kind="exam"] {
    border-left-color: var(--ke-marker-rose);
  }
  .entry time {
    flex: none;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
  }
  .entry span {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .cell .more {
    width: 100%;
    min-height: 0;
    border: 0;
    background: none;
    padding: 0 4px;
    line-height: 15px;
    text-align: left;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-secondary);
  }
  .month-state {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
    margin: 10px 8px;
  }
  button {
    cursor: pointer;
    color: var(--ke-text-primary);
    font: inherit;
  }
  .entry,
  .more {
    font-size: var(--ke-text-2xs);
  }
  button:focus-visible,
  .month-scroll:focus-visible {
    outline: 2px solid var(--ke-accent);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .entry:hover:not(:disabled),
  .cell-day:hover span {
    background: var(--ke-surface-inset);
  }
  .cell .more:hover,
  .cell .cell-day:hover {
    background: none;
  }
</style>
