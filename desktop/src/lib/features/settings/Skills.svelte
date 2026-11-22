<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import PackageIcon from "./PackageIcon.svelte";
  import ShelfItemLayout from "./ShelfItemLayout.svelte";
  import ShelfItemHeading from "./ShelfItemHeading.svelte";
  import ShelfCard from "./ShelfCard.svelte";
  import ShelfDialog from "./ShelfDialog.svelte";
  import {
    partIcon,
    partTitle,
    packageFacts,
    type ShelfOwner,
  } from "./shelf-library";
  import { readSkillLibrary } from "./skill-library";
  import { onMount } from "svelte";
  import { NativeFailure, request } from "$lib/api/client";
  import type {
    SkillStatus,
    SkillItem,
    SkillPage,
  } from "$lib/api/wire.generated";

  export let ownerBusy = false;
  export let busy = false;
  /** A skill another panel asks to open; the tick changes with each request. */
  export let focus: { name: string; tick: number } = { name: "", tick: 0 };
  let focused = 0;
  export let saved: SkillStatus | null = null;
  let selected: SkillItem | null = null;
  /** On the Shelf, the page's Add skill button opens the import window. */
  export let adding = false;
  let kind: "folder" | "text" | "github" = "folder";
  let source = "";
  let update = false;
  export let filter = "";
  export let owners = new Map<string, ShelfOwner[]>();
  export let parent: ShelfOwner | undefined = undefined;
  export let onOpenItem: ((name: string) => void) | undefined = undefined;
  let error = "";
  let notice = "";
  let page: SkillPage | null = null;
  let path = "SKILL.md";
  let filePath = "";
  let advanced = false;
  $: visible =
    saved?.items.filter((item) =>
      `${item.name} ${partTitle("skill", item.name, owners.get(item.name)?.[0])} ${item.description} ${
        owners
          .get(item.name)
          ?.map((owner) => owner.title)
          .join(" ") ?? ""
      }`
        .toLowerCase()
        .includes(filter.trim().toLowerCase()),
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
  async function refresh() {
    await action(async () => {
      try {
        saved = await readSkillLibrary();
      } catch (cause) {
        saved = null;
        throw cause;
      }
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
        saved = await readSkillLibrary(
          await request("skills.import", {
            kind,
            source,
            update,
            expected_revision: saved!.revision,
          }),
        );
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
        saved = await readSkillLibrary(
          await request("skills.enable", {
            name,
            revision: selected!.revision,
            enabled,
            expected_revision: saved!.revision,
          }),
        );
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
  // Opened from Packages: the library is read again first, since the skill may
  // have been installed after this panel last read it.
  $: if (focus.tick !== focused && !busy) {
    focused = focus.tick;
    void open(focus.name);
  }
  async function open(name: string) {
    await refresh();
    const item = saved?.items.find((row) => row.name === name);
    if (item) choose(item);
    else if (!error) {
      selected = null;
      error = "This skill is no longer in the library.";
    }
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
    if (!focus.tick && !onOpenItem) void refresh();
  });
</script>

{#snippet power(item: SkillItem)}
  <button
    class:primary={!item.enabled}
    class:cp-action={!!focus.name}
    disabled={busy || !saved || !!item.error || (ownerBusy && !item.enabled)}
    on:click={() => enable(!item.enabled)}
    >{item.enabled ? "Disable skill" : "Enable skill"}</button
  >
{/snippet}

<section class="skills-panel" aria-label="Skill library">
  {#if focus.name && (selected?.name === focus.name || error)}
    <ShelfItemHeading
      title={partTitle("skill", focus.name, parent)}
      icon={partIcon("skill", focus.name, parent)}
      category={parent?.category}
      subtitle={parent ? parent.title + " · Skill" : "Skill"}
    >
      {#if selected?.name === focus.name}{@render power(selected)}{/if}
    </ShelfItemHeading>
  {/if}
  {#if !focus.name}
    {#if !onOpenItem || error}<div class="actions">
        {#if !onOpenItem}<button
            class="primary"
            disabled={busy || ownerBusy}
            on:click={() => (adding = !adding)}>Add skill</button
          >{/if}
        <button disabled={busy} on:click={() => refresh()}
          >Refresh library</button
        >
      </div>{/if}
  {:else if error}
    <button disabled={busy} on:click={() => open(focus.name)}
      >Refresh library</button
    >
  {/if}
  {#if !adding}
    {#if error}<p role="alert">{error}</p>{/if}
    {#if notice}<p role="status">{notice}</p>{/if}
    {#if busy}<p role="status" class="hint">
        Working… An import can take up to 35 seconds.
      </p>{/if}
  {/if}
  {#if adding}
    <ShelfDialog
      title="Add skill"
      closeLabel="Close add skill"
      {busy}
      onClose={() => (adding = false)}
    >
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
            data-first
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
            data-first
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
      {#if error}<p role="alert" class="error">{error}</p>{/if}
      {#if busy}<p role="status" class="hint">
          Working… An import can take up to 35 seconds.
        </p>{/if}
      <svelte:fragment slot="footer">
        <button disabled={busy} on:click={() => (adding = false)}>Cancel</button
        >
        <button
          class="primary"
          disabled={busy || ownerBusy || !saved || !source.trim()}
          on:click={install}>Import skill</button
        >
      </svelte:fragment>
    </ShelfDialog>
  {/if}
  {#if saved && !focus.name}
    {#if !onOpenItem && saved.items.length > 5}<label class="filter"
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
    {#if onOpenItem}
      <div class="cp-cards">
        {#each visible as item (item.name)}
          {@const owner = owners.get(item.name)?.[0]}
          <ShelfCard
            title={partTitle("skill", item.name, owner)}
            state={item.error ? "Needs attention" : item.enabled ? "On" : "Off"}
            summary={owners
              .get(item.name)
              ?.map((owner) => owner.title)
              .join(" · ") || "Skill"}
            icon={partIcon("skill", item.name, owner)}
            category={owner?.category}
            part={"skill:" + item.name}
            disabled={busy}
            onOpen={() => onOpenItem?.(item.name)}
          />
        {/each}
      </div>
    {:else}<div class="skill-list">
        {#each visible as item (item.name)}
          {@const owner = owners.get(item.name)?.[0]}
          <button
            class="skill-row"
            data-part={"skill:" + item.name}
            class:selected={selected?.name === item.name}
            disabled={busy}
            on:click={() => (onOpenItem ? onOpenItem(item.name) : choose(item))}
          >
            <span class="row-title"
              ><span class="skill-name"
                ><PackageIcon
                  compact
                  name={partIcon("skill", item.name, owner)}
                  category={owner?.category}
                /><strong>{item.name}</strong></span
              ><span class="status-tag"
                >{item.error
                  ? "Needs attention"
                  : item.enabled
                    ? "Enabled"
                    : "Disabled"}</span
              ></span
            >
            <span class="hint">{item.error || item.description}</span>
            {#if owners.has(item.name)}<small
                >{owners
                  .get(item.name)
                  ?.map((owner) => owner.title)
                  .join(" · ")}</small
              >{/if}
          </button>
        {/each}
      </div>{/if}
    {#if filter.trim() && !visible.length}<p class="cp-empty">
        No matching skills.
      </p>{/if}
  {/if}
  {#if selected}
    <ShelfItemLayout
      inline={!focus.name}
      label="Skill details"
      facts={[
        {
          label: "State",
          value: selected.error
            ? "Needs attention"
            : selected.enabled
              ? "On"
              : "Off",
        },
        { label: "Name", value: selected.name },
        { label: "Files", value: String(selected.files) },
        ...packageFacts(parent),
      ]}
    >
      <div class={focus.name ? "cp-section" : "settings-card detail"}>
        {#if !focus.name}<h3>{selected.name}</h3>{/if}
        <p>{selected.description}</p>
        {#if selected.compatibility}<p class="hint">
            Compatibility: {selected.compatibility}
          </p>{/if}
        <div class="actions">
          <button disabled={busy || !!selected.error} on:click={() => read()}
            >Read instructions</button
          >
          {#if !focus.name}{@render power(selected)}{/if}
          <button
            aria-expanded={advanced}
            disabled={busy}
            on:click={() => (advanced = !advanced)}>Advanced</button
          >
        </div>
        <p class="hint">
          A skill gives guidance. It does not grant tool, account or file
          access. Installing it is not proof that its procedure works.
        </p>
        {#if advanced}
          <div class="advanced-content">
            <p class="hint">
              {selected.files} installed files. Exact revision:
              <code>{selected.revision}</code>
            </p>
            <button
              disabled={busy || !!selected.error}
              on:click={() => read("")}>Inspect files and origin</button
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
                on:click={() => read(path, page!.next_offset!)}
                >Next page</button
              >{/if}
          </div>
        {/if}
      </div>
    </ShelfItemLayout>
  {/if}
</section>

<style>
  .cp-section > :first-child {
    margin-top: 0;
  }
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
    align-items: center;
    gap: 1rem;
  }
  .skill-name {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 8px;
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
  .error {
    color: var(--ke-danger);
  }
  .empty-state {
    padding: 1.5rem 0;
    color: var(--ke-text-secondary);
  }
</style>
