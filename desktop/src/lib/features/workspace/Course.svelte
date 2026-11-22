<script lang="ts">
  // A module's documents beside its conversation: the docked reader/editor and
  // the module's file tree in the sidebar. Modules have no page of their own;
  // their schedule opens in place on the project page (Semesters).
  import type { Workspace, Source, Course, RecordRow } from "./workspace";
  import Material from "./Material.svelte";
  import ProjectSaveLocation from "./ProjectSaveLocation.svelte";
  export let workspace: Workspace;
  export let course: RecordRow<Course>;
  export let busy = false;
  export let active = true;
  export let conversation = false;
  /** Show the file tree in the sidebar outside a conversation (project page). */
  export let files = false;
  export let docked = false;
  export let dockBounds: import("./document-dock").DocumentDockBounds | null =
    null;
  export let onOpenDock: () => boolean = () => false;
  export let onDocumentsChange: (count: number) => void = () => {};
  export let fileSidebar: HTMLElement | null = null;
  export let onDiscuss: (text: string, source?: Source) => void = () => {};
  let requestedSource: Source | null = null;
  // Keep one editor and file tree per visited module, including unsaved tabs.
  let filesVisited = false;
  $: if (conversation || docked || files) filesVisited = true;
  export function openMaterial(value: Source) {
    requestedSource = value;
  }
  let saveLocation: ProjectSaveLocation;
  $: locked = busy || $workspace.busy || !!$workspace.pending;
</script>

<ProjectSaveLocation
  bind:this={saveLocation}
  {workspace}
  {course}
  {busy}
  {active}
/>

<section
  class="uni course-view conversation"
  aria-label={`Module ${course.data.title}`}
>
  {#if filesVisited}<section hidden={!docked || !dockBounds}>
      <Material
        courseId={course.id}
        courseTitle={course.data.title}
        {fileSidebar}
        folder={course.data.folder}
        chooseSaveFolder={() => saveLocation.choose()}
        {requestedSource}
        {dockBounds}
        {onDocumentsChange}
        active={active && docked}
        showFiles={conversation || files}
        onShowReader={onOpenDock}
        busy={locked}
        {onDiscuss}
      />
    </section>{/if}
</section>

<style>
  .conversation {
    margin: 0;
    padding: 0;
  }
</style>
