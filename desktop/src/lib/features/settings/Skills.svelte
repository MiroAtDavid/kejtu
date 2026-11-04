<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import { NativeFailure, request } from "$lib/api/client";
  import type {
    SkillStatus,
    SkillItem,
    SkillPage,
  } from "$lib/api/wire.generated";

  export let ownerBusy = false;
  export let busy = false;
  let saved: SkillStatus | null = null;
  let selected: SkillItem | null = null;
  let adding = false;
  let kind: "folder" | "text" | "github" = "folder";
  let source = "";
  let update = false;
  let filter = "";
  let error = "";
  let notice = "";
  let page: SkillPage | null = null;
  let path = "SKILL.md";
  let filePath = "";
  let advanced = false;
  $: visible =
    saved?.items.filter((item) =>
      `${item.name} ${item.description}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
    ) ?? [];

  async function action(work: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await work();
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "The skill library could not be read.";
    } finally {
      busy = false;
    }
  }
  async function refresh(offset = 0) {
    await action(async () => {
      const value = await request("skills.status", { offset });
      saved =
        offset && saved
          ? { ...value, items: [...saved.items, ...value.items] }
          : value;
      if (selected)
        selected =
          saved.items.find((item) => item.name === selected?.name) ?? null;
      page = null;
    });
  }
  async function install() {
    if (ownerBusy || !saved || !source.trim()) return;
    await action(async () => {
      try {
        saved = await request("skills.import", {
          kind,
          source,
          update,
          expected_revision: saved!.revision,
        });
        source = "";
        adding = false;
        selected = null;
        page = null;
        notice =
          "Import complete. Select the skill to review its instructions and enabled status.";
      } catch (caught) {
        // These refusals occur before an import is committed. Keep the draft
        // and library usable so the person can correct the actual problem.
        if (
          caught instanceof NativeFailure &&
          [
            "skill_import",
            "skill_web",
            "skill_import_unavailable",
            "skill_exists",
            "skill_limit",
          ].includes(caught.code)
        )
          throw caught;
        // A stale revision or an uncertain transport result needs a fresh
        // read. Never retry an import whose result may already be saved.
        saved = null;
        if (caught instanceof NativeFailure && caught.code === "skill_conflict")
          throw caught;
        throw new Error(
          (caught instanceof Error
            ? caught.message
            : "Import could not be confirmed.") +
            " Refresh the library to check what was saved before trying again.",
        );
      }
    });
  }
  async function enable(enabled: boolean) {
    if (!saved || !selected || (ownerBusy && enabled)) return;
    await action(async () => {
      const name = selected!.name;
      try {
        saved = await request("skills.enable", {
          name,
          revision: selected!.revision,
          enabled,
          expected_revision: saved!.revision,
        });
        selected = saved.items.find((item) => item.name === name) ?? null;
        notice = enabled
          ? "Enabled. Kejtu can discover this skill, or you can choose it beside your message."
          : "Disabled. Kejtu can no longer use this skill.";
      } catch (caught) {
        saved = null;
        throw new Error(
          (caught instanceof Error
            ? caught.message
            : "Save could not be confirmed.") +
            " Refresh to inspect the saved state.",
        );
      }
    });
  }
  async function read(nextPath = "SKILL.md", offset = 0) {
    if (!selected || !selected.revision) return;
    await action(async () => {
      page = await request("skills.read", {
        name: selected!.name,
        revision: selected!.revision,
        path: nextPath,
        offset,
        max_bytes: 16384,
      });
      path = nextPath;
    });
  }
  function choose(item: SkillItem) {
    if (busy) return;
    selected = item;
    page = null;
    notice = "";
    error = "";
    advanced = false;
    filePath = "";
  }
  onMount(() => {
    void refresh();
  });
</script>

<section class="skills-panel" aria-label="Skill library">
  <h2 class="settings-title">Skills</h2>
  <p class="panel-intro">
    Semester organization, study planning, learning support and course research
    are built in and available automatically. No installation is needed.
  </p>
  <p class="panel-intro">Add your own optional procedures below.</p>
  <div class="actions">
    <button
      class="primary"
      disabled={busy || ownerBusy}
      on:click={() => (adding = !adding)}>Add skill</button
    >
    <button disabled={busy} on:click={() => refresh()}>Refresh library</button>
  </div>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if busy}<p role="status" class="hint">
      Working… An import can take up to 35 seconds.
    </p>{/if}
  {#if adding}
    <div class="settings-card">
      <h3>Add a procedure</h3>
      <label
        >Import from<select
          use:choiceSelect={kind}
          aria-label="Import from"
          bind:value={kind}
          disabled={busy}
        >
          <option value="folder">A skill folder on this computer</option>
          <option value="text">SKILL.md text</option>
          <option value="github"
            >A public GitHub repository or skill folder</option
          >
        </select></label
      >
      {#if kind === "text"}
        <label
          >SKILL.md<textarea
            rows="9"
            bind:value={source}
            spellcheck="false"
            placeholder={"---\nname: my-skill\ndescription: When to use this procedure.\n---\nWrite the procedure here."}
          ></textarea></label
        >
        <p class="hint">
          Include the standard name and description. Paste up to 12 KB here; use
          a folder for larger instructions or bundled files.
        </p>
      {:else}
        <label
          >{kind === "folder" ? "Skill folder path" : "GitHub URL"}<input
            bind:value={source}
            spellcheck="false"
            placeholder={kind === "folder"
              ? "/home/your-name/skills/my-skill"
              : "https://github.com/owner/repository/tree/main/skills/my-skill"}
          /></label
        >
        {#if kind === "github"}
          <p class="hint">
            Paste a repository link if it contains one skill. For a collection
            of skills, open the folder you want on GitHub and paste its URL.
          </p>
        {/if}
        <p class="hint">
          Choose one folder containing SKILL.md. The whole folder is copied,
          including references, assets, scripts and nested folders. Files keep
          their original contents and paths. Importing does not run scripts.
        </p>
      {/if}
      <label class="check"
        ><input type="checkbox" bind:checked={update} />Update an existing skill
        with this name</label
      >
      <p class="hint">
        New and updated skills start switched off. Review the instructions after
        importing, then choose Enable skill to use them. Earlier revisions are
        kept.
      </p>
      <button
        class="primary"
        disabled={busy || ownerBusy || !saved || !source.trim()}
        on:click={install}>Import skill</button
      >
    </div>
  {/if}
  {#if saved}
    {#if saved.items.length > 5}<label class="filter"
        >Find a skill<input
          type="search"
          bind:value={filter}
          placeholder="Name or description"
        /></label
      >{/if}
    {#if saved.total === 0}<div class="empty-state">
        <h3>A place for useful procedures</h3>
        <p>
          Add a standard SKILL.md package when you have a task Kejtu should
          approach consistently. Each skill keeps its own instructions and
          supporting files.
        </p>
      </div>{/if}
    <div class="skill-list">
      {#each visible as item (item.name)}
        <button
          class="skill-row"
          class:selected={selected?.name === item.name}
          disabled={busy}
          on:click={() => choose(item)}
        >
          <span class="row-title"
            ><strong>{item.name}</strong><span class="status-tag"
              >{item.error
                ? "Needs attention"
                : item.enabled
                  ? "Enabled"
                  : "Disabled"}</span
            ></span
          >
          <span class="hint">{item.error || item.description}</span>
        </button>
      {/each}
    </div>
    {#if saved.next_offset !== null}<button
        disabled={busy}
        on:click={() => refresh(saved!.next_offset!)}>More skills</button
      >{/if}
  {/if}
  {#if selected}
    <div class="settings-card detail">
      <h3>{selected.name}</h3>
      <p>{selected.description}</p>
      {#if selected.compatibility}<p class="hint">
          Compatibility: {selected.compatibility}
        </p>{/if}
      <div class="actions">
        <button disabled={busy || !!selected.error} on:click={() => read()}
          >Read instructions</button
        >
        <button
          class:primary={!selected.enabled}
          disabled={busy ||
            !saved ||
            !!selected.error ||
            (ownerBusy && !selected.enabled)}
          on:click={() => enable(!selected!.enabled)}
          >{selected.enabled ? "Disable skill" : "Enable skill"}</button
        >
        <button
          aria-expanded={advanced}
          disabled={busy}
          on:click={() => (advanced = !advanced)}>Advanced</button
        >
      </div>
      <p class="hint">
        A skill gives guidance. It does not grant tool, account or file access.
        Installing it is not proof that its procedure works.
      </p>
      {#if advanced}
        <div class="advanced-content">
          <p class="hint">
            {selected.files} installed files. Exact revision:
            <code>{selected.revision}</code>
          </p>
          <button disabled={busy || !!selected.error} on:click={() => read("")}
            >Inspect files and origin</button
          >
          <label
            >Resource path<input
              bind:value={filePath}
              placeholder="references/example.md"
            /></label
          >
          <button
            disabled={busy || !filePath.trim()}
            on:click={() => read(filePath)}>Read resource</button
          >
          <p class="hint">
            Resources are read as text. Scripts and binary assets are kept but
            are not executed by the skill library.
          </p>
        </div>
      {/if}
      {#if page}
        <div class="saved-file">
          <h4>{path || "Files and origin"}</h4>
          <p class="hint">
            Bytes {page.offset}–{page.end} of {page.total_bytes}{page.complete
              ? " · complete file"
              : " · exact page"}
          </p>
          <pre aria-label="Installed skill text">{page.content}</pre>
          {#if page.next_offset !== null}<button
              disabled={busy}
              on:click={() => read(path, page!.next_offset!)}>Next page</button
            >{/if}
        </div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin: 1rem 0;
  }
  label:not(.check) {
    display: grid;
    gap: 0.5rem;
    margin: 1rem 0;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    margin-top: 1.25rem;
  }
  .hint {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  .skill-list {
    display: grid;
    gap: 0.65rem;
    margin: 1.75rem 0;
  }
  .skill-row {
    display: grid;
    gap: 0.5rem;
    padding: 1.1rem 1.2rem;
    text-align: left;
  }
  .row-title {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
  }
  .row-title strong {
    overflow-wrap: anywhere;
  }
  .status-tag {
    flex-shrink: 0;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .skill-row.selected {
    border-color: var(--ke-text-secondary);
  }
  .detail h3,
  .settings-card h3 {
    margin-top: 0;
  }
  code {
    overflow-wrap: anywhere;
    font-size: var(--ke-text-xs);
  }
  .advanced-content {
    border-top: 1px solid var(--ke-border);
    margin-top: 1.25rem;
    padding-top: 1rem;
  }
  .saved-file {
    margin-top: 1.5rem;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 30rem;
    overflow: auto;
    font: var(--ke-text-xs)/1.7 var(--ke-font-mono);
    padding: 1rem;
    background: var(--ke-surface);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
  }
  .empty-state {
    padding: 1.5rem 0;
    color: var(--ke-text-secondary);
  }
</style>
