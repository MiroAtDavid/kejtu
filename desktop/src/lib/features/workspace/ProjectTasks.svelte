<script lang="ts">
  import { onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type { TaskRecord } from "$lib/api/wire.generated";
  import TaskRows from "$lib/features/tasks/TaskRows.svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import "$lib/design/collection.css";

  export let projectId: string;
  export let active: boolean;
  export let disabled: boolean;
  export let onOpen: (ref: string) => void;
  export let onAll: () => void;
  /** The project's modules in page order, for the module column and sorting. */
  export let modules: { id: string; title: string }[] = [];

  type Order = "due" | "urgency" | "module";
  let order: Order = "due";
  const PAGE = 20;
  let next: number | null = null;
  function moduleOf(task: TaskRecord) {
    return (
      (task.placements ?? []).find((place) => place.project_id === projectId)
        ?.section_id ?? ""
    );
  }
  function moduleTitle(task: TaskRecord) {
    return modules.find((row) => row.id === moduleOf(task))?.title ?? "";
  }
  // By module: the modules in page order, then tasks for the whole project.
  $: rank = (task: TaskRecord) => {
    const index = modules.findIndex((row) => row.id === moduleOf(task));
    return index < 0 ? modules.length : index;
  };
  $: shown =
    order === "module" ? [...rows].sort((a, b) => rank(a) - rank(b)) : rows;

  let rows: TaskRecord[] = [];
  let loading = false;
  let error = "";
  let generation = 0;

  async function refresh(
    id: string,
    visible: boolean,
    sort: Order,
    append = false,
  ) {
    const version = ++generation;
    if (!visible) return;
    loading = !append;
    error = "";
    if (!append) rows = [];
    try {
      const page = await request("tasks", {
        action: "list",
        outcome: "open",
        attention: "all",
        project_id: id,
        limit: PAGE,
        offset: append ? (next ?? 0) : 0,
        ...(sort === "urgency" ? { order: "urgency" as const } : {}),
      });
      if (version !== generation) return;
      if (!("items" in page)) throw new Error("Could not read project tasks.");
      rows = append ? [...rows, ...page.items] : page.items;
      next = page.next_offset;
    } catch (caught) {
      if (version === generation)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (version === generation) loading = false;
    }
  }
  $: void refresh(projectId, active, order);
  onDestroy(() => {
    generation++;
  });
</script>

<section class="cp-section project-tasks" aria-label="Open project tasks">
  <div class="task-heading">
    <h2>Open tasks</h2>
    <button {disabled} on:click={onAll}>Project tasks</button>
  </div>
  {#if error}<p role="alert">
      {error}
      <button class="cp-text" on:click={() => refresh(projectId, active, order)}
        >Try again</button
      >
    </p>
  {:else if loading}<p class="cp-hint cp-late" role="status">Reading tasks…</p>
  {:else if rows.length}
    <div class="task-order">
      <ChoicePicker
        chip
        arrow={false}
        hideLabel
        id={`project-task-order-${projectId}`}
        label="Sort project tasks"
        value={order}
        options={[
          { value: "due", label: "Due date first" },
          { value: "urgency", label: "Highest urgency" },
          ...(modules.length ? [{ value: "module", label: "By module" }] : []),
        ]}
        onChange={(value) => (order = value as Order)}
        {disabled}
      />
    </div>
    <TaskRows
      items={shown}
      grouped={order === "due"}
      groupBy={order === "module"
        ? (task) => moduleTitle(task) || "Whole project"
        : null}
      completion={false}
      {disabled}
      placementLabel={order === "module" ? () => "" : moduleTitle}
      onOpen={(task) => onOpen(task.read_ref)}
    />
    {#if next !== null}<button
        class="more-tasks"
        {disabled}
        on:click={() => refresh(projectId, active, order, true)}
        >More tasks</button
      >{/if}
  {:else}<p class="cp-hint">No open tasks in this project.</p>{/if}
</section>

<style>
  .task-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }
  .task-order {
    margin-bottom: 4px;
  }
  .more-tasks {
    margin-top: 10px;
  }
  .project-tasks h2 {
    margin: 0;
    color: var(--ke-text-primary);
    font: 500 var(--ke-text-sm)/1.5 var(--ke-font-ui);
  }
</style>
