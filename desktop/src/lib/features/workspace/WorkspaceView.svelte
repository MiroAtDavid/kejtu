<script lang="ts">
  import type { DocumentReference } from "./document-assist";
  import { tick } from "svelte";
  import type { Workspace, View, Event, Source } from "./workspace";
  import Week from "./Week.svelte";
  import Semesters from "./Semesters.svelte";
  import Course from "./Course.svelte";
  import Media from "./Media.svelte";
  export let workspace: Workspace;
  export let view: View = "journal";
  export let courseId = "";
  export let compactNavigation = false;
  export let fileSidebar: HTMLElement | null = null;
  export let busy = false;
  export let onFileChosen: () => void = () => {};
  export let active = true;
  export let navigationLocked = false;
  export let onOpen: (view: View, id?: string) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  let visited = new Set<View>();
  let courses = new Set<string>();
  let week: Week | undefined;
  export let onReadingChange: (value: boolean) => void = () => {};
  export let onWideChange: (wide: boolean) => void = () => {};
  let courseWide: Record<string, boolean> = {};
  $: onWideChange(reading && (courseWide[courseId] ?? false));
  let courseTabs: Record<string, string> = {};
  let courseViews: Record<string, Course> = {};
  let reportedReading = false;
  $: reading = view === "course" && courseTabs[courseId] === "material";
  $: if (reading !== reportedReading) {
    reportedReading = reading;
    onReadingChange(reading);
  }
  export function openMaterial(id: string, source: Source) {
    courseViews[id]?.openMaterial(source);
  }
  export async function showMaterial(id: string) {
    await courseViews[id]?.showMaterial();
  }
  export async function openDocument(reference: DocumentReference) {
    const course = courseViews[reference.course_id];
    if (!course) throw new Error("This section is unavailable.");
    await course.openDocument(reference);
  }
  $: if (view !== "journal") visited = new Set([...visited, view]);
  $: if (view === "course" && courseId)
    courses = new Set([...courses, courseId]);
  export async function reserve(value: Partial<Event>) {
    onOpen("overview");
    await tick();
    week?.reserve(value);
  }
</script>

<div class="uni">
  {#if compactNavigation}<nav
      class="workspace-nav"
      aria-label="Workspace views"
    >
      {#if $workspace.snapshot?.settings.university_enabled}
        <button
          disabled={navigationLocked}
          aria-pressed={view === "overview"}
          on:click={() => onOpen("overview")}>Overview</button
        >
        <button
          disabled={navigationLocked}
          aria-pressed={view === "semesters" || view === "course"}
          on:click={() => onOpen("semesters")}>Projects</button
        >
      {/if}
    </nav>{/if}
  {#if visited.has("overview")}<div
      hidden={view !== "overview" ||
        !$workspace.snapshot?.settings.university_enabled}
    >
      <Week
        bind:this={week}
        {workspace}
        {busy}
        active={active && view === "overview"}
        onTasks={() => onOpen("tasks")}
        onCourse={(id) => onOpen("course", id)}
        onProject={(id) => onOpen("semesters", id)}
      />
    </div>{/if}
  {#if visited.has("semesters")}<div
      hidden={view !== "semesters" ||
        !$workspace.snapshot?.settings.university_enabled}
    >
      <Semesters
        {workspace}
        {busy}
        projectId={view === "semesters" ? courseId : ""}
        onTasks={(id) => onOpen("tasks", "project:" + id)}
        onOpenCourse={(id) => onOpen("course", id)}
      />
    </div>{/if}
  {#each [...courses] as id (id)}{@const course =
      $workspace.snapshot?.courses.find((row) => row.id === id)}{#if course}<div
        hidden={view !== "course" ||
          courseId !== id ||
          !$workspace.snapshot?.settings.university_enabled}
      >
        <Course
          bind:this={courseViews[id]}
          {onFileChosen}
          onWideChange={(wide) => {
            if (courseWide[id] !== wide)
              courseWide = { ...courseWide, [id]: wide };
          }}
          onTabChange={(tab) => (courseTabs = { ...courseTabs, [id]: tab })}
          {workspace}
          {course}
          {fileSidebar}
          {busy}
          active={active && view === "course" && courseId === id}
          {onDiscuss}
          onTasks={() => onOpen("tasks", "section:" + id)}
          onReserve={(value) => reserve(value)}
        />
      </div>{/if}{/each}
  {#if view === "course" && !$workspace.snapshot?.courses.some((row) => row.id === courseId)}<p
      class="notice"
    >
      Choose an available section in Projects.
    </p>{/if}
  {#if visited.has("media")}<div hidden={view !== "media"}>
      <Media {workspace} {busy} active={active && view === "media"} />
    </div>{/if}
</div>

<style>
  .workspace-nav {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 20px;
    margin-bottom: 24px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--ke-border);
  }
  .workspace-nav button {
    padding: 7px 0;
    margin: 0;
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    background: none;
    color: var(--ke-text-secondary);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .workspace-nav button[aria-pressed="true"] {
    border-bottom-color: var(--ke-text-secondary);
    color: var(--ke-text-primary);
    background: transparent;
  }
  .workspace-nav button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    background: transparent;
  }
  .workspace-nav button:disabled {
    cursor: default;
    opacity: 0.5;
  }
  .workspace-nav button:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 3px;
  }
</style>
