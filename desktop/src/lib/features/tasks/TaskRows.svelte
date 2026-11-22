<script lang="ts">
  import type { Snippet } from "svelte";
  import type { TaskRecord } from "$lib/api/wire.generated";
  import { descriptionPreview } from "./task-description";
  export let items: TaskRecord[] = [];
  export let disabled = false;
  export let completionDisabled = false;
  /** The completion tick; a summary elsewhere (a project page) leaves it to
   *  the Tasks page, which owns the durable change. */
  export let completion = true;
  /** Open tasks in due order read as Today / This week / Later. */
  export let grouped = false;
  /** Or headings of the caller's choosing (a project's modules), in the
   *  order the rows arrive. */
  export let groupBy: ((task: TaskRecord) => string) | null = null;
  export let onOpen: (task: TaskRecord) => void;
  export let onComplete: (task: TaskRecord, done: boolean) => void = () => {};
  export let placementLabel: (task: TaskRecord) => string = () => "";
  /** A small control at the end of each row (the Calendar's Reserve). */
  export let action: Snippet<[TaskRecord]> | undefined = undefined;
  const stamp = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const today = () => stamp(new Date());
  function weekEnd() {
    const d = new Date();
    d.setDate(d.getDate() + 7);
    return stamp(d);
  }
  function dateLabel(value: string) {
    if (value === today()) return "Today";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      ...(value.slice(0, 4) !== String(new Date().getFullYear())
        ? { year: "numeric" as const }
        : {}),
    }).format(new Date(value + "T12:00:00"));
  }
  // Carried-forward work stays on today's plate; undated work waits in Later.
  const labels = ["Today", "This week", "Later"];
  function groupOf(task: TaskRecord) {
    if (!task.due) return 2;
    if (task.due <= today()) return 0;
    return task.due <= weekEnd() ? 1 : 2;
  }
  function byLabel(rows: TaskRecord[], label: (task: TaskRecord) => string) {
    const map = new Map<string, TaskRecord[]>();
    for (const task of rows) {
      const key = label(task);
      map.set(key, [...(map.get(key) ?? []), task]);
    }
    return [...map].map(([label, rows]) => ({ label, rows }));
  }
  $: sections = groupBy
    ? byLabel(items, groupBy)
    : grouped
      ? labels
          .map((label, index) => ({
            label,
            rows: items.filter((task) => groupOf(task) === index),
          }))
          .filter((section) => section.rows.length)
      : [{ label: "", rows: items }];
</script>

{#each sections as section (section.label)}
  {#if section.label}<h3 class="cp-cap">{section.label}</h3>{/if}
  <ul class="task-list cp-lines" aria-label={section.label || undefined}>
    {#each section.rows as task (task.id)}
      {@const urgency = task.urgency ?? "medium"}
      {@const earlier =
        task.outcome === "open" && !!task.due && task.due < today()}
      {@const note = descriptionPreview(task.description ?? "")}
      {@const place = placementLabel(task)}
      <li class="cp-line" data-task={task.id}>
        {#if completion && task.outcome !== "cancelled"}<input
            type="checkbox"
            class="cp-tick"
            checked={task.outcome === "done"}
            disabled={disabled || completionDisabled}
            aria-label={`${task.outcome === "done" ? "Reopen" : "Complete"} ${task.title}`}
            on:change={(event) => onComplete(task, event.currentTarget.checked)}
          />{/if}
        <button
          class="task-row"
          aria-label={task.title}
          {disabled}
          on:click={() => onOpen(task)}
        >
          <span class="title" class:done={task.outcome === "done"}
            >{task.title}</span
          >
          <!-- Every row names its urgency, Medium included (owner, 19 Nov). -->
          <span
            class="cp-tag urgency"
            data-urgency={urgency}
            aria-label={`Urgency: ${urgency}`}
            >{urgency === "high"
              ? "High"
              : urgency === "low"
                ? "Low"
                : "Medium"}</span
          >
          <span class="note">{note}</span>
          {#if place}<span class="place">{place}</span>{/if}
          <span class="date" class:earlier
            >{#if task.due}<time datetime={task.due}
                >{earlier ? "From " : ""}{dateLabel(task.due)}</time
              >{:else}Anytime{/if}</span
          >
        </button>
        {#if action}{@render action(task)}{/if}
      </li>
    {/each}
  </ul>
{/each}

<style>
  h3.cp-cap {
    margin: 20px 0 7px;
  }
  h3.cp-cap:first-child {
    margin-top: 0;
  }
  .task-row {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 10px;
    min-width: 0;
    min-height: 42px;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .task-row:hover:not(:disabled) {
    background: transparent;
  }
  .title,
  .note,
  .place {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    flex: 0 1 auto;
    font-size: var(--ke-text-sm);
  }
  .note {
    flex: 1 1 0;
    color: var(--ke-text-tertiary);
  }
  .place {
    flex: 0 1 auto;
    max-width: 34%;
    color: var(--ke-text-tertiary);
    font-size: 11.5px;
  }
  .date {
    flex: none;
    min-width: 64px;
    color: var(--ke-text-tertiary);
    font-size: 11.5px;
    text-align: right;
    white-space: nowrap;
  }
  /* Carried-forward open work keeps its date, a touch firmer, without an alarm
     word or warning colour that makes the list feel like a backlog of failure. */
  .earlier {
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .urgency[data-urgency="high"] {
    color: var(--ke-text-primary);
    font-weight: 600;
  }
  .done {
    color: var(--ke-text-secondary);
    text-decoration: line-through;
  }
  @media (max-width: 560px) {
    .note,
    .place {
      display: none;
    }
    .title {
      flex: 1 1 auto;
    }
  }
</style>
