<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { request } from "$lib/api/client";
  import type { SkillSelection, SkillItem } from "$lib/api/wire.generated";

  export let selectedSkill: SkillSelection | null = null;
  export let disabled = false;
  export let anchor: HTMLTextAreaElement | undefined = undefined;
  export let query: string | null = null;
  export let onChoose: () => void = () => {};
  export let onDismiss: () => void = () => {};
  let root: HTMLDivElement;
  let menu: HTMLDivElement;
  let open = false,
    loading = false,
    error = "",
    placement = "";
  let items: SkillItem[] = [];
  let active = 0,
    requestId = 0;
  const key = (item: SkillSelection) => `${item.name}:${item.revision}`;
  $: selectedKey = selectedSkill ? key(selectedSkill) : "";
  $: filtered = items.filter((item) =>
    item.name.toLowerCase().includes((query ?? "").toLowerCase()),
  );
  $: choices = [null, ...filtered] as (SkillItem | null)[];
  $: if (active >= choices.length) active = 0;
  $: if (query !== null && !disabled && !open) void show();
  $: if ((query === null || disabled) && open) close();
  $: if (query !== null) {
    query;
    active = filtered.length ? 1 : 0;
  }
  export let activeOptionId: string | undefined = undefined;
  $: activeOptionId =
    open && !loading && !error ? `composer-skill-${active}` : undefined;
  $: stale =
    !loading &&
    !!selectedSkill &&
    !items.some((item) => key(item) === selectedKey);

  function close(dismiss = false) {
    open = false;
    loading = false;
    requestId++;
    if (dismiss) onDismiss();
  }
  async function show() {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(280, window.innerWidth - 24);
    const above = Math.max(0, rect.top - 20),
      below = Math.max(0, window.innerHeight - rect.bottom - 20);
    const down = below > above;
    placement = `width:${width}px;left:${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px;max-height:${Math.min(240, down ? below : above)}px;${down ? `top:${rect.bottom + 6}px` : `bottom:${window.innerHeight - rect.top + 6}px`}`;
    open = true;
    loading = true;
    error = "";
    items = [];
    active = 0;
    const ticket = ++requestId;
    try {
      let offset: number | null = 0,
        revision: number | null = null;
      const rows: SkillItem[] = [],
        offsets = new Set<number>();
      while (offset !== null) {
        if (offsets.has(offset))
          throw Error("Skills could not be loaded. Type / to try again.");
        offsets.add(offset);
        const page = await request("skills.status", {
          enabled_only: true,
          offset,
        });
        if (ticket !== requestId) return;
        if (revision !== null && page.revision !== revision)
          throw Error("Skills changed while loading. Type / to reopen.");
        revision = page.revision;
        rows.push(...page.items);
        offset = page.next_offset;
      }
      items = rows;
      active = rows.some((item) =>
        item.name.toLowerCase().includes((query ?? "").toLowerCase()),
      )
        ? 1
        : 0;
    } catch (caught) {
      if (ticket === requestId)
        error =
          caught instanceof Error
            ? caught.message
            : "Skills could not be loaded.";
    } finally {
      if (ticket === requestId) loading = false;
    }
  }
  function choose(item: SkillItem | null) {
    if (disabled || loading) return;
    selectedSkill = item ? { name: item.name, revision: item.revision } : null;
    close();
    onChoose();
    anchor?.focus({ preventScroll: true });
  }
  export function keydown(event: KeyboardEvent): boolean {
    if (!open || event.isComposing) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return true;
    }
    if (!["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(event.key))
      return false;
    // Enter always belongs to the open picker, including while it is loading.
    event.preventDefault();
    if (event.key === "Enter" || event.key === "Tab") {
      if (!error) choose(choices[active]);
    } else {
      active =
        (active + (event.key === "ArrowDown" ? 1 : -1) + choices.length) %
        choices.length;
      void tick().then(() =>
        menu
          ?.querySelector('[aria-selected="true"]')
          ?.scrollIntoView?.({ block: "nearest" }),
      );
    }
    return true;
  }
  function outside(event: Event) {
    if (
      open &&
      event.target instanceof Node &&
      event.target !== anchor &&
      !root.contains(event.target)
    )
      close(true);
  }
  onDestroy(() => requestId++);
</script>

<svelte:document on:pointerdown={outside} on:focusin={outside} />
<svelte:window on:resize={() => close(true)} />
<div class="skill-chooser" bind:this={root}>
  {#if selectedSkill}<span class="selection"
      ><span class="selected-name" title={selectedSkill.name}
        >/{selectedSkill.name}</span
      >
      <button
        type="button"
        class="clear"
        {disabled}
        aria-label="Clear selected skill"
        title="Use Automatic"
        on:click={() => {
          selectedSkill = null;
          anchor?.focus();
        }}>×</button
      >
    </span>{/if}
  {#if open}<div class="skill-menu" style={placement}>
      <div class="heading">Skills</div>
      {#if loading}<p role="status">Loading skills…</p>
      {:else if error}<p role="alert">{error}</p>
      {:else}
        {#if stale}<p role="status">
            Selected skill changed or is unavailable.
          </p>{/if}
        <div
          id="composer-skills"
          role="listbox"
          aria-label="Skills"
          bind:this={menu}
        >
          {#each choices as item, index (item?.name ?? "__automatic")}
            <button
              type="button"
              role="option"
              id={`composer-skill-${index}`}
              aria-selected={active === index}
              class:chosen={item ? selectedKey === key(item) : !selectedSkill}
              title={item?.description ?? "Let Kejtu choose a skill"}
              on:pointerdown|preventDefault={() => {}}
              on:click={() => choose(item)}
              on:keydown={keydown}
            >
              <span>{item?.name ?? "Automatic"}</span>
              {#if item ? selectedKey === key(item) : !selectedSkill}<span
                  aria-hidden="true">✓</span
                >{/if}
            </button>
          {/each}
        </div>
        {#if !filtered.length}<p>
            {items.length
              ? "No matching skills."
              : "No enabled skills. Add them in Settings → Skills."}
          </p>{/if}
      {/if}
    </div>{/if}
</div>

<style>
  .skill-chooser {
    min-width: 0;
    max-width: calc(100% - 36px);
  }
  .selection {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .selected-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  button {
    font: inherit;
    color: var(--ke-text-secondary);
    cursor: pointer;
  }
  .clear {
    border: 0;
    background: transparent;
    padding: 3px 6px;
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -2px;
  }
  .skill-menu {
    position: fixed;
    z-index: 30;
    box-sizing: border-box;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 5px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-overlay-shadow);
    transform-origin: top left;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .heading {
    padding: 4px 7px;
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
  }
  [role="option"] {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    width: 100%;
    padding: 5px 7px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    text-align: left;
    font-size: var(--ke-text-xs);
    line-height: 20px;
  }
  [role="option"] span:first-child {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  [role="option"]:hover,
  [aria-selected="true"] {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .chosen {
    font-weight: 500;
  }
  p {
    padding: 5px 7px;
    margin: 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
</style>
