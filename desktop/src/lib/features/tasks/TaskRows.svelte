<script lang="ts">
  import type { Snippet } from "svelte";
  import type { TaskRecord } from "$lib/api/wire.generated";
  export let items: TaskRecord[] = [];
  export let disabled = false;
  export let completionDisabled = false;
  export let selectedId = "";
  export let editor: Snippet | undefined = undefined;
  export let editorId = "";
  export let onOpen: (task: TaskRecord) => void;
  export let onComplete: (task: TaskRecord, done: boolean) => void;
  export let placementLabel: (task: TaskRecord) => string = () => "";
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
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
</script>

<ul class="task-list">
  {#each items as task (task.id)}
    <li class:selected={selectedId === task.id}>
      <div class="task-row-heading">
        {#if task.outcome !== "cancelled"}<input
            type="checkbox"
            checked={task.outcome === "done"}
            disabled={disabled || completionDisabled}
            aria-label={`${task.outcome === "done" ? "Reopen" : "Complete"} ${task.title}`}
            on:change={(event) => onComplete(task, event.currentTarget.checked)}
          />{/if}
        <button
          class="task-row"
          aria-label={task.title}
          aria-pressed={selectedId === task.id}
          aria-expanded={selectedId === task.id}
          aria-controls={selectedId === task.id ? editorId : undefined}
          {disabled}
          on:click={() => onOpen(task)}
        >
          <span class="task-main"
            ><span class:done={task.outcome === "done"}>{task.title}</span
            >{#if placementLabel(task)}<small>{placementLabel(task)}</small
              >{/if}</span
          >
          <span
            class="date"
            class:earlier={task.outcome === "open" &&
              !!task.due &&
              task.due < today()}
            >{#if task.due}<time datetime={task.due}>{dateLabel(task.due)}</time
              >{#if task.outcome === "open" && task.due < today()}<small
                  >Still open</small
                >{/if}{:else}<small>No due date</small>{/if}</span
          >
        </button>
      </div>
      {#if selectedId === task.id && editor}
        <div class="task-row-editor">{@render editor()}</div>
      {/if}
    </li>
  {/each}
</ul>

<style>
  .task-list {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  li {
    min-width: 0;
    border-bottom: 1px solid var(--ke-border);
  }
  .task-row-heading {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 10px;
  }
  li.selected > .task-row-heading,
  .task-row-heading:hover {
    background: var(--ke-accent-soft);
    border-radius: var(--ke-radius-sm);
  }
  .task-row-editor {
    min-width: 0;
  }
  .task-row-editor :global(.task-editor) {
    margin: 8px 0 16px;
  }
  input {
    width: 17px;
    height: 17px;
    flex: none;
    accent-color: var(--ke-accent);
    cursor: pointer;
  }
  input:disabled {
    cursor: default;
  }
  .task-row {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    min-width: 0;
    text-align: left;
    padding: 15px 2px;
    margin: 0;
    border: 0;
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    cursor: pointer;
  }
  .task-main {
    flex: 1;
    min-width: 0;
    font-size: var(--ke-text-base);
    overflow-wrap: anywhere;
  }
  small {
    display: block;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-tertiary);
    margin-top: 3px;
  }
  .date {
    flex: none;
    text-align: right;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  /* Carried-forward open work stays visible with its date, without an alarm
     word or warning colour that makes the list feel like a backlog of failure. */
  .earlier small {
    font-style: italic;
  }
  .done {
    text-decoration: line-through;
    color: var(--ke-text-secondary);
  }
</style>
