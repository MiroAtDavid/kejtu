<script lang="ts">
  import { tick } from "svelte";
  export let id: string;
  export let label: string;
  export let value = "";
  export let options: {
    value: string;
    label: string;
    shortLabel?: string;
    detail?: string;
    disabled?: boolean;
  }[] = [];
  export let placeholder = "Choose an option";
  export let disabled = false;
  export let required = false;
  export let compact = false;
  export let hideLabel = false;
  export let singleLine = false;
  export let labelOnly = false;
  export let describedBy = "";
  export let invalid = false;
  export let searchable = false;
  export let onChange: (value: string) => void = () => {};
  let root: HTMLDivElement;
  let trigger: HTMLButtonElement;
  let open = false;
  let active = 0;
  let query = "";
  let typedAt = 0;
  let filter = "";
  let searchInput: HTMLInputElement;
  $: visible = options
    .map((option, index) => ({ option, index }))
    .filter(
      ({ option }) =>
        !searchable ||
        `${option.label} ${option.detail || ""}`
          .toLocaleLowerCase()
          .includes(filter.trim().toLocaleLowerCase()),
    );
  $: enabled = visible
    .map(({ option, index }) => (option.disabled ? -1 : index))
    .filter((index) => index >= 0);
  $: selected = options.find((option) => option.value === value);
  $: unavailable = disabled || !options.some((option) => !option.disabled);
  $: if (unavailable) open = false;
  $: if (!enabled.includes(active)) active = enabled[0] ?? 0;

  function reveal() {
    if (unavailable) return;
    filter = "";
    active = Math.max(
      0,
      options.findIndex((option) => option.value === value),
    );
    if (options[active]?.disabled) active = enabled[0];
    query = "";
    open = true;
    showActive();
    if (searchable)
      void tick().then(() => searchInput?.focus({ preventScroll: true }));
  }
  function showActive() {
    void tick().then(() =>
      root
        ?.querySelector<HTMLElement>(`#${id}-option-${active}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  function choose(index: number) {
    if (disabled || !options[index] || options[index].disabled) return;
    value = options[index].value;
    onChange(value);
    open = false;
    trigger?.focus({ preventScroll: true });
  }
  function key(event: KeyboardEvent) {
    if (unavailable || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Escape" && open) {
      // The open picker consumes Escape before the containing editor's drawer.
      event.preventDefault();
      event.stopPropagation();
      open = false;
      trigger?.focus({ preventScroll: true });
    } else if (event.key === "Tab") {
      open = false;
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) {
        if (enabled.length) choose(active);
      } else reveal();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open) reveal();
      else if (event.key === "ArrowDown" && enabled.length)
        active = enabled[(enabled.indexOf(active) + 1) % enabled.length];
      else if (event.key === "ArrowUp" && enabled.length)
        active =
          enabled[
            (enabled.indexOf(active) - 1 + enabled.length) % enabled.length
          ];
      if (event.key === "Home" && enabled.length) active = enabled[0];
      if (event.key === "End" && enabled.length)
        active = enabled[enabled.length - 1];
      showActive();
    } else if (event.key.length === 1) {
      event.preventDefault();
      if (!open) reveal();
      const now = Date.now();
      query =
        (now - typedAt < 700 ? query : "") + event.key.toLocaleLowerCase();
      typedAt = now;
      const found = options.findIndex(
        (option) =>
          !option.disabled &&
          option.label.toLocaleLowerCase().startsWith(query),
      );
      if (found >= 0) active = found;
      showActive();
    }
  }
  function blur(event: FocusEvent) {
    if (
      !(event.relatedTarget instanceof Node) ||
      !root.contains(event.relatedTarget)
    )
      open = false;
  }
  function searchKey(event: KeyboardEvent) {
    if (["ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"].includes(event.key))
      key(event);
  }
  function outside(event: PointerEvent) {
    if (open && event.target instanceof Node && !root.contains(event.target))
      open = false;
  }
</script>

<svelte:window on:pointerdown={outside} />
<div class="choice-picker" class:compact bind:this={root}>
  <span
    class="choice-label"
    class:visually-hidden={compact || hideLabel}
    id={`${id}-label`}>{label}</span
  >
  <button
    class="choice-trigger"
    type="button"
    bind:this={trigger}
    role="combobox"
    aria-labelledby={`${id}-label${labelOnly ? "" : ` ${id}-value`}`}
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-controls={`${id}-options`}
    aria-activedescendant={open && enabled.length
      ? `${id}-option-${active}`
      : undefined}
    aria-required={required}
    aria-describedby={describedBy || undefined}
    aria-invalid={invalid || undefined}
    disabled={unavailable}
    on:click={() => (open ? (open = false) : reveal())}
    on:keydown={key}
    on:blur={blur}
  >
    <span
      class="choice-value"
      class:single-line={singleLine}
      title={singleLine ? selected?.label : undefined}
    >
      {#if selected?.shortLabel}
        <span aria-hidden="true">{selected.shortLabel}</span>
        <span class="visually-hidden" id={`${id}-value`}>{selected.label}</span>
      {:else}
        <span id={`${id}-value`}>{selected?.label || placeholder}</span>
      {/if}
    </span>
    <svg
      width="14"
      height="14"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      stroke-width="1.3"
      aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg
    >
  </button>
  {#if open}<div class="choice-popup">
      {#if searchable}<input
          class="choice-search"
          type="search"
          bind:this={searchInput}
          bind:value={filter}
          aria-label={`Search ${label.toLocaleLowerCase()}`}
          placeholder="Search…"
          role="combobox"
          aria-expanded="true"
          aria-controls={`${id}-options`}
          aria-autocomplete="list"
          aria-activedescendant={enabled.length
            ? `${id}-option-${active}`
            : undefined}
          on:keydown={searchKey}
          on:blur={blur}
        />{/if}
      <div
        class="choice-options"
        id={`${id}-options`}
        role="listbox"
        aria-labelledby={`${id}-label`}
      >
        {#each visible as { option, index } (option.value)}
          <button
            type="button"
            class="choice-option"
            class:highlighted={index === active}
            id={`${id}-option-${index}`}
            role="option"
            aria-selected={value === option.value}
            tabindex="-1"
            disabled={option.disabled}
            on:pointerdown|preventDefault
            on:click={() => choose(index)}
          >
            <span class="option-copy"
              ><span>{option.label}</span>{#if option.detail}<small
                  >{option.detail}</small
                >{/if}</span
            >
            {#if value === option.value}<svg
                width="14"
                height="14"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                stroke-width="1.3"
                aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg
              >{/if}
          </button>
        {/each}
      </div>
      {#if !visible.length}<p class="empty" role="status">
          No matching options.
        </p>{/if}
    </div>{/if}
</div>

<style>
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
  .compact .choice-trigger {
    min-height: 32px;
    padding: 5px 8px;
    font-size: 11px;
    background: transparent;
  }
  .compact {
    min-width: 104px;
  }

  .choice-picker {
    position: relative;
    min-width: 0;
  }
  .choice-label {
    display: block;
    margin-bottom: 7px;
    color: var(--ke-text-secondary);
    font-size: 11px;
  }
  .choice-trigger {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    width: 100%;
    min-height: 38px;
    padding: 8px 12px;
    border: 1px solid transparent;
    border-radius: var(--ke-radius-sm);
    background: color-mix(in srgb, var(--ke-marker-sage) 10%, transparent);
    color: var(--ke-text-primary);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .choice-trigger span {
    overflow-wrap: anywhere;
  }
  .choice-value.single-line {
    position: relative;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  svg {
    flex: 0 0 auto;
  }
  .choice-picker .choice-trigger:hover:not(:disabled),
  .choice-trigger[aria-expanded="true"] {
    background: color-mix(in srgb, var(--ke-marker-sage) 17%, transparent);
  }
  .choice-trigger:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .choice-trigger:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  .choice-popup {
    box-sizing: border-box;
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    width: 100%;
    z-index: 10;
    padding: 5px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-shadow-search);
  }
  .choice-options {
    max-height: min(320px, 40vh);
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .choice-popup .choice-search {
    width: 100%;
    margin: 0 0 6px;
  }
  .empty {
    margin: 10px;
    color: var(--ke-text-secondary);
  }
  .choice-option {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: 36px;
    padding: 9px 10px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .option-copy {
    display: grid;
    gap: 3px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .option-copy small {
    font-size: 11px;
    color: var(--ke-text-tertiary);
  }
  .choice-picker .choice-option:hover:not(:disabled),
  .choice-picker .choice-option.highlighted {
    background: color-mix(in srgb, var(--ke-marker-sage) 12%, transparent);
  }
  .choice-option:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .choice-option svg {
    color: var(--ke-text-secondary);
  }
</style>
