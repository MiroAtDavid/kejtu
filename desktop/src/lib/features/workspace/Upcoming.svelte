<script lang="ts">
  import { agendaZone, appointmentDisplay, type Agenda } from "./upcoming";
  export let agenda: Agenda;
  export let timezone = "UTC";
  export let onMore: () => Promise<void> = async () => {};
  const step = 4;
  let limit = step;
  let loading = false;
  $: zone = agendaZone(agenda.timezone, timezone);
  $: names = [
    ...new Set(agenda.events.map((item) => item.calendar_name).filter(Boolean)),
  ];
  $: rows = agenda.events
    .slice(0, limit)
    .map((item) => ({ item, ...appointmentDisplay(item, zone) }));
  $: hasMore = agenda.events.length > limit || agenda.next_offset != null;
  async function more() {
    if (loading) return;
    if (agenda.events.length > limit) {
      limit += step;
      return;
    }
    loading = true;
    const count = agenda.events.length;
    try {
      await onMore();
      // A failed read keeps the current rows and the same explicit retry control.
      if (agenda.events.length > count) limit += step;
    } finally {
      loading = false;
    }
  }
</script>

{#if names.length === 1}<p class="calendar-source">{names[0]}</p>{/if}
<ol class="agenda" aria-label="Upcoming appointments">
  {#each rows as row, index}
    <li>
      {#if index === 0 || rows[index - 1].day !== row.day}
        <div class="agenda-day">
          <span class="dot"></span><time
            datetime={row.day === "unknown" ? undefined : row.day}
            title={row.day}>{row.label}</time
          >
        </div>
      {/if}
      <div class="agenda-entry">
        <time class="agenda-time" datetime={row.item.start} title={row.detail}
          >{row.time}</time
        >
        <div class="agenda-body">
          <p class="agenda-title" title={row.item.title}>{row.item.title}</p>
          {#if names.length > 1}<span class="entry-source"
              >{row.item.calendar_name}</span
            >{/if}
        </div>
      </div>
    </li>
  {:else}<li class="widget-muted">
      No upcoming appointments on this page.
    </li>{/each}
</ol>
{#if rows.length}<p class="agenda-zone" title={zone}>
    {zone.split("/").at(-1)?.replaceAll("_", " ")} time
  </p>{/if}
{#if hasMore || limit > step}
  <div class="agenda-controls">
    {#if hasMore}<button class="widget-link" disabled={loading} on:click={more}
        >{loading ? "Loading…" : "Show more"}</button
      >{/if}
    {#if limit > step}<button
        class="widget-link"
        disabled={loading}
        on:click={() => (limit = step)}>Show fewer</button
      >{/if}
  </div>
{/if}

<style>
  .calendar-source {
    margin: -4px 0 14px;
    color: var(--ke-text-tertiary);
    font-size: 10px;
  }
  .agenda {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .agenda-day {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 15px 0 7px;
    font-size: 11px;
    font-weight: 500;
    color: var(--ke-text-secondary);
  }
  .agenda > li:first-child .agenda-day {
    margin-top: 0;
  }
  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--ke-marker-blue);
    flex: none;
  }
  .agenda-entry {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr);
    gap: 10px;
    margin: 0 0 9px 2px;
    padding: 0 0 0 11px;
    border-left: 1px solid var(--ke-border);
  }
  .agenda-time {
    color: var(--ke-text-secondary);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    line-height: 1.65;
  }
  .agenda-title {
    margin: 0;
    font-size: 12px;
    line-height: 1.65;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  .entry-source {
    display: block;
    color: var(--ke-text-tertiary);
    font-size: 10px;
    margin-top: 2px;
  }
  .agenda-zone {
    color: var(--ke-text-tertiary);
    font-size: 10px;
    margin: 12px 0 0;
  }
  .agenda-controls {
    display: flex;
    gap: 18px;
    align-items: center;
    margin-top: 9px;
  }
</style>
