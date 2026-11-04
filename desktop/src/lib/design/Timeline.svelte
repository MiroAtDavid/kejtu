<script lang="ts" context="module">
  /** Display-only rows: callers own dates, ordering, identity and all actions. */
  export type TimelineItem = {
    id: string;
    title: string;
    date: {
      datetime: string;
      label: string;
      weekday: string;
      day: string;
      month: string;
      year: string;
    };
    time: string;
    category: string;
    details: string[];
    tone?: "sand" | "sage" | "blue" | "rose";
  };
</script>

<script lang="ts">
  export let items: TimelineItem[] = [];
  export let label = "Timeline";
</script>

<ol class="timeline" aria-label={label}>
  {#each items as item (item.id)}
    <li
      class="timeline-entry"
      style:--marker={`var(--ke-marker-${item.tone || "sand"})`}
    >
      <time
        class="timeline-date"
        datetime={item.date.datetime}
        aria-label={item.date.label}
      >
        <span class="weekday">{item.date.weekday}</span>
        <span class="day">{item.date.day}</span>
        <span class="month">{item.date.month} {item.date.year}</span>
      </time>
      <div class="timeline-body">
        <div class="timeline-heading">
          <span class="timeline-time">{item.time}</span>
          <span class="timeline-category">{item.category}</span>
        </div>
        <h3>{item.title}</h3>
        {#if item.details.length}<ul
            class="timeline-details"
            aria-label="Details"
          >
            {#each item.details as detail}<li>{detail}</li>{/each}
          </ul>{/if}
      </div>
      {#if $$slots.actions}<div class="timeline-actions">
          <slot name="actions" {item} />
        </div>{/if}
    </li>
  {/each}
</ol>

<style>
  .timeline {
    --date-width: 80px;
    --rail-gap: 44px;
    list-style: none;
    padding: 0;
    margin: var(--ke-space-6) 0 var(--ke-space-2);
  }
  .timeline-entry {
    position: relative;
    display: grid;
    grid-template-columns: var(--date-width) minmax(0, 1fr) auto;
    column-gap: var(--rail-gap);
    padding-bottom: var(--ke-space-8);
    min-width: 0;
  }
  .timeline-entry::before,
  .timeline-entry::after {
    content: "";
    position: absolute;
    pointer-events: none;
  }
  .timeline-entry::before {
    left: calc(var(--date-width) + var(--rail-gap) / 2 - 4px);
    top: 8px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--marker);
    box-shadow: 0 0 0 4px var(--ke-surface);
    z-index: 1;
  }
  .timeline-entry::after {
    left: calc(var(--date-width) + var(--rail-gap) / 2 - 1px);
    top: 12px;
    bottom: -12px;
    width: 1px;
    background: var(--ke-border);
  }
  .timeline-entry:last-child::after {
    display: none;
  }
  .timeline-date {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    color: var(--ke-text-secondary);
    line-height: 1.3;
    font-variant-numeric: tabular-nums;
  }
  .weekday {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .day {
    font:
      400 28px/1.2 Georgia,
      "Times New Roman",
      serif;
    color: var(--ke-text-primary);
    margin: 2px 0;
  }
  .month {
    font-size: 11px;
    white-space: nowrap;
  }
  .timeline-body {
    min-width: 0;
  }
  .timeline-heading {
    display: flex;
    flex-wrap: wrap;
    gap: var(--ke-space-2) var(--ke-space-3);
    align-items: center;
    min-height: 24px;
  }
  .timeline-time {
    font-size: var(--ke-text-base);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--ke-text-primary);
  }
  .timeline-category {
    font-size: 10px;
    line-height: 1.6;
    color: var(--ke-text-secondary);
    border-radius: var(--ke-radius-sm);
    padding: 1px 7px;
    background: color-mix(in srgb, var(--marker) 12%, transparent);
  }
  .timeline h3 {
    margin: var(--ke-space-2) 0;
    font-size: var(--ke-text-base);
    font-weight: 500;
    line-height: var(--ke-leading-body);
    overflow-wrap: anywhere;
  }
  .timeline-details {
    display: flex;
    flex-wrap: wrap;
    gap: 3px 14px;
    list-style: none;
    padding: 0;
    margin: 0;
    font-size: var(--ke-text-xs);
    line-height: 1.65;
    color: var(--ke-text-tertiary);
    overflow-wrap: anywhere;
  }
  .timeline-details li:not(:first-child)::before {
    content: "·";
    margin-right: 8px;
    color: var(--ke-text-tertiary);
  }
  .timeline-actions {
    padding-top: 1px;
  }
  .timeline-actions :global(button) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: var(--ke-text-xs);
    white-space: nowrap;
    cursor: pointer;
  }
  .timeline-actions :global(button:hover:not(:disabled)) {
    background: var(--ke-surface-inset);
  }
  .timeline-actions :global(button:focus-visible) {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  .timeline-actions :global(button:disabled) {
    opacity: 0.5;
    cursor: default;
  }
  @media (max-width: 650px) {
    .timeline {
      --date-width: 58px;
      --rail-gap: 30px;
    }
    .timeline-entry {
      grid-template-columns: var(--date-width) minmax(0, 1fr);
      padding-bottom: var(--ke-space-6);
    }
    .timeline-actions {
      grid-column: 2;
      padding-top: var(--ke-space-2);
    }
    .timeline-actions :global(button) {
      padding-left: 0;
    }
    .day {
      font-size: 25px;
    }
    .timeline-details {
      display: block;
    }
    .timeline-details li:not(:first-child)::before {
      content: none;
    }
  }
</style>
