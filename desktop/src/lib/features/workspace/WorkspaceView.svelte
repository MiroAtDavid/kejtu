<script lang="ts">
  import { tick } from "svelte";
  import type { Workspace, View, Event, Source } from "./workspace";
  import Week from "./Week.svelte";
  import Semesters from "./Semesters.svelte";
  import Course from "./Course.svelte";
  export let workspace: Workspace;
  export let view: View = "journal";
  export let courseId = "";
  /** Counted visits: a plain Projects open, and one with a project/module. */
  export let projectListVisit = 0;
  export let projectScopeVisit = 0;
  export let conversationModule = "";
  export let documentModule = "";
  export let dockBounds: import("./document-dock").DocumentDockBounds | null =
    null;
  export let onOpenDock: (id: string) => boolean = () => false;
  export let onDocumentsChange: (id: string, count: number) => void = () => {};
  export let compactNavigation = false;
  export let fileSidebar: HTMLElement | null = null;
  export let busy = false;
  export let active = true;
  export let navigationLocked = false;
  export let onOpen: (view: View, id?: string) => void = () => {};
  export let onConversation: (id: string) => void = () => {};
  export let onNewConversation: (
    projectId: string,
    moduleId?: string,
  ) => void = () => {};
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  /** The module whose documents the sidebar shows on the project page. */
  export let onPageFiles: (id: string) => void = () => {};
  let visited = new Set<View>();
  let courses = new Set<string>();
  let week: Week | undefined;
  let courseViews: Record<string, Course> = {};
  export function openMaterial(id: string, source: Source) {
    courseViews[id]?.openMaterial(source);
  }
  $: if (view !== "journal") visited = new Set([...visited, view]);
  $: if (view === "journal" && conversationModule)
    courses = new Set([...courses, conversationModule]);
  let projectFilesModule = "";
  $: pageFiles =
    view === "semesters" && $workspace.snapshot?.settings.university_enabled
      ? projectFilesModule
      : "";
  $: if (pageFiles) courses = new Set([...courses, pageFiles]);
  $: onPageFiles(pageFiles);
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
          aria-pressed={view === "semesters"}
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
        onTask={(ref) => onOpen("tasks", "task-ref:" + ref)}
      />
    </div>{/if}
  {#if visited.has("semesters")}<div
      hidden={view !== "semesters" ||
        !$workspace.snapshot?.settings.university_enabled}
    >
      <Semesters
        active={active && view === "semesters"}
        {onConversation}
        {onNewConversation}
        {workspace}
        {busy}
        projectId={view === "semesters" ? courseId : ""}
        listVisit={projectListVisit}
        scopeVisit={projectScopeVisit}
        onTasks={(id) => onOpen("tasks", "project:" + id)}
        onTask={(ref) => onOpen("tasks", "task-ref:" + ref)}
        onReserve={(value) => reserve(value)}
        onFilesModule={(id) => (projectFilesModule = id)}
      />
    </div>{/if}
  {#each [...courses] as id (id)}{@const course =
      $workspace.snapshot?.courses.find((row) => row.id === id)}{#if course}<div
        hidden={!(view === "journal" && documentModule === id) ||
          !$workspace.snapshot?.settings.university_enabled}
      >
        <Course
          bind:this={courseViews[id]}
          {workspace}
          {course}
          {fileSidebar}
          {busy}
          active={active && view === "journal" && documentModule === id}
          conversation={view === "journal" && conversationModule === id}
          files={pageFiles === id}
          docked={view === "journal" && documentModule === id}
          {dockBounds}
          onOpenDock={() => onOpenDock(id)}
          onDocumentsChange={(count) => onDocumentsChange(id, count)}
          {onDiscuss}
        />
      </div>{/if}{/each}
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
    font-size: var(--ke-text-xs);
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
