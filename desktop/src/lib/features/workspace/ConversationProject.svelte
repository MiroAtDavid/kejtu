<script lang="ts">
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import {
    workspaceRequest,
    type ConversationProject,
    type Course,
    type RecordRow,
    type Workspace,
  } from "./workspace";

  /** The conversation's project, and optionally one of its modules, as a chip
   *  in the composer's tool row. A project's modules open beside it; "No
   *  project" holds modules without a project. Choosing saves both at once; a
   *  new conversation keeps both choices until it is created. */
  export let workspace: Workspace;
  export let conversationId = "";
  export let draftProject = "";
  export let draftModule = "";
  export let disabled = false;
  export let onOpen: (id: string) => void = () => {};
  export let onChooseModule: (id: string) => void = () => {};
  export let onModule: (
    conversationId: string,
    projectId: string,
    moduleId: string,
  ) => void = () => {};
  $: onModule(
    conversationId,
    (conversationId ? link?.project_id : draftProject) || "",
    conversationId
      ? link?.module_available
        ? link.module_id || ""
        : ""
      : draftModule,
  );
  let link: ConversationProject | null = null;
  let choice = "";
  let changing = false;
  // The revision shown when the list opened: a change made elsewhere while
  // choosing is reported as a conflict, never silently replaced.
  let seenRevision: number | null = null;
  let error = "";
  let token = 0;
  let previousId = "";
  let observed: unknown;
  $: snapshot = $workspace.snapshot;
  $: if (conversationId !== previousId || snapshot !== observed) {
    const changed = conversationId !== previousId;
    previousId = conversationId;
    observed = snapshot;
    if (changed) link = null;
    void load(conversationId);
  }
  $: projectId = (conversationId ? link?.project_id : draftProject) || "";
  $: moduleId = (conversationId ? link?.module_id : draftModule) || "";
  $: title = conversationId
    ? link?.title
    : snapshot?.semesters.find((row) => row.id === draftProject)?.data.title;
  $: selection = choiceValue(projectId, moduleId);
  $: choice = selection;
  // Locked until this conversation's project is first known. A background
  // reload keeps an open list open; the save still carries the revision seen
  // when it opened.
  $: locked =
    changing ||
    disabled ||
    $workspace.busy ||
    !!$workspace.pending ||
    (!!conversationId && !link);
  // The snapshot lists every live module: beyond its bound the whole snapshot
  // fails rather than arriving shortened. Group it once per snapshot, so
  // hovering or redrawing the list never discovers modules again.
  $: modules = byProject(snapshot?.courses ?? []);
  $: offered = [
    { value: "", label: "No project", children: choices("", modules.get("")) },
    ...(snapshot?.semesters ?? []).map((project) => ({
      value: project.id,
      label: project.data.title,
      children: choices(project.id, modules.get(project.id)),
    })),
  ];
  // A saved or drafted choice that is no longer offered (cancelled, moved or
  // not yet listed) stays shown and selected; it is never silently replaced.
  $: missingProject = projectId ? title || "Unavailable project" : "No project";
  $: missing = moduleId
    ? `${(conversationId && link?.module_available && link.module_title) || "Unavailable module"} · ${missingProject}`
    : missingProject;
  $: options = [
    ...offered,
    ...(selection &&
    !offered.some(
      (option) =>
        option.value === selection ||
        option.children.some((child) => child.value === selection),
    )
      ? [{ value: selection, label: missing, disabled: true }]
      : []),
    ...(projectId && title
      ? [
          {
            value: "\u0000open",
            label: `Open ${title} →`,
            action: () => onOpen(projectId),
          },
        ]
      : []),
  ];
  /** One picker value per pair: the project ID alone for a project-only
   *  choice ("" for none), with "/module" appended for a module. */
  function choiceValue(project: string, module: string) {
    return module ? `${project}/${module}` : project;
  }
  function byProject(rows: RecordRow<Course>[]) {
    const groups = new Map<string, RecordRow<Course>[]>();
    for (const row of rows) {
      if (row.cancelled) continue;
      const key = row.data.semester_id ?? "";
      const group = groups.get(key);
      if (group) group.push(row);
      else groups.set(key, [row]);
    }
    return groups;
  }
  function choices(project: string, rows: RecordRow<Course>[] = []) {
    return rows.map((row) => ({
      value: choiceValue(project, row.id),
      label: row.data.title,
    }));
  }
  async function load(id: string) {
    const current = ++token;
    error = "";
    if (!id) {
      link = null;
      return;
    }
    try {
      const result = await workspaceRequest<ConversationProject>(
        "conversation.project",
        { conversation_id: id },
      );
      if (current === token) link = result;
    } catch (e) {
      if (current === token) error = String(e);
    }
  }
  function begin() {
    seenRevision = link?.revision ?? 0;
  }
  async function save(next: string) {
    if (next === selection) return;
    if (locked) {
      choice = selection;
      return;
    }
    const [project, module = ""] = next.split("/");
    if (!conversationId) {
      draftProject = project;
      draftModule = module;
      onChooseModule(module);
      return;
    }
    // Keep the existing selection locked until the saved link is read back.
    // Reopening with its earlier revision would create a spurious conflict.
    changing = true;
    try {
      const result = await workspace.mutate("conversation.assign", {
        conversation_id: conversationId,
        project_id: project || null,
        // A project-only choice omits the module: that clears an earlier one
        // and keeps the original project-only request identity.
        ...(module ? { module_id: module } : {}),
        expected_revision: seenRevision ?? link?.revision ?? 0,
      });
      seenRevision = null;
      // Show what is saved: the new choice, or the old one when it was refused.
      if (result) {
        await load(conversationId);
        onChooseModule(module);
      } else choice = selection;
    } catch (caught) {
      error = String(caught);
      choice = selection;
    } finally {
      changing = false;
    }
  }
</script>

{#if snapshot?.settings.university_enabled}
  <span class="conversation-project">
    <ChoicePicker
      id={`conversation-project-${conversationId || "new"}`}
      label="Conversation project"
      bind:value={choice}
      {options}
      placeholder="No project"
      disabled={locked}
      chip
      above
      hideLabel
      singleLine
      onReveal={begin}
      onChange={(next) => void save(next)}
    >
      <span slot="leading" class="leading" aria-hidden="true"
        ><Icon name="book" size={13} /></span
      >
    </ChoicePicker>
    {#if error}<button
        type="button"
        class="retry"
        title={error}
        {disabled}
        on:click={() => load(conversationId)}>Try again</button
      >{/if}
  </span>
{/if}

<style>
  .conversation-project {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
  }
  .leading {
    display: inline-flex;
    flex: none;
  }
  .retry {
    font: inherit;
    font-size: var(--ke-text-xs);
    border: 0;
    border-radius: var(--ke-radius-sm);
    padding: 4px 8px;
    color: var(--ke-text-secondary);
    background: transparent;
    cursor: pointer;
  }
  .retry:hover:not(:disabled) {
    color: var(--ke-text-primary);
  }
  .retry:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
</style>
