<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import type { Snapshot, View } from "./workspace";
  export let snapshot: Snapshot | null = null;
  export let view: View = "overview";
  export let courseId = "";
  $: course = snapshot?.courses.find((row) => row.id === courseId);
  export let disabled = false;
  export let researchNew = 0;
  export let onOpen: (view: View, id?: string) => void = () => {};
  const sections = [
    { id: "overview", label: "Calendar", icon: "calendar", planning: true },
    { id: "semesters", label: "Projects", icon: "book", planning: true },
    { id: "research", label: "Research", icon: "search", planning: false },
    { id: "tasks", label: "Tasks", icon: "tasks", planning: false },
  ] as const;
</script>

<nav class="study-nav" aria-label="Workspace destinations">
  {#each sections as item}<button
      class="sidebar-menu-row"
      aria-label={item.id === "research" && researchNew > 0
        ? `Research · ${researchNew} ${researchNew === 1 ? "finding" : "findings"} to review`
        : undefined}
      class:current={view === item.id ||
        (view === "course" && item.id === "semesters")}
      aria-current={view === item.id ||
      (view === "course" && item.id === "semesters")
        ? "page"
        : undefined}
      disabled={disabled ||
        (item.planning && !snapshot?.settings.university_enabled)}
      title={item.planning && !snapshot?.settings.university_enabled
        ? "Enable Projects in Settings to use this workspace"
        : undefined}
      on:click={() => onOpen(item.id)}
      ><Icon name={item.icon} size={15} />{item.label}
      {#if item.id === "research" && researchNew > 0}<span
          class="research-count"
          aria-hidden="true">{researchNew}</span
        >{/if}</button
    >{/each}
  {#if view === "course" && course}<div class="course-context">
      <span>Current section</span>
      <p>{course.data.title}</p>
    </div>{/if}
</nav>

<style>
  .research-count {
    margin-left: auto;
    min-width: 20px;
    padding: 1px 5px;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-accent-soft);
    color: var(--ke-text-primary);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    text-align: center;
  }
  .course-context {
    margin-top: calc(24px * var(--ke-density));
    padding-top: calc(20px * var(--ke-density));
    border-top: 1px solid var(--ke-border);
  }
  .course-context span {
    font: 9px var(--ke-font-mono);
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: var(--ke-text-tertiary);
  }
  .course-context p {
    font-size: var(--ke-label-size);
    line-height: 1.6;
    margin: 10px 0 0;
    color: var(--ke-text-primary);
  }

  .study-nav {
    padding-top: 0;
    color: var(--ke-text-secondary);
  }
</style>
