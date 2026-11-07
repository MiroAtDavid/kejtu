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
          class="research-dot"
          title={`${researchNew} ${researchNew === 1 ? "finding" : "findings"} to review`}
          aria-hidden="true"
        ></span>{/if}</button
    >{/each}
  {#if view === "course" && course}<div class="course-context">
      <span>Current section</span>
      <p>{course.data.title}</p>
    </div>{/if}
</nav>

<style>
  /* Something is waiting: a quiet dot, not a number asking to be cleared. The
     exact count stays in the accessible name and the tooltip. */
  .research-dot {
    flex: none;
    margin-left: auto;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ke-accent);
    animation: ke-fade-in var(--ke-motion-slow) var(--ke-motion-ease);
  }
  .course-context {
    margin-top: calc(24px * var(--ke-density));
    padding-top: calc(20px * var(--ke-density));
    border-top: 1px solid var(--ke-border);
  }
  .course-context span {
    font: var(--ke-label-font);
    letter-spacing: var(--ke-label-tracking);
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
