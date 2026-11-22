<script lang="ts">
  import { onMount, tick } from "svelte";
  type Nested = {
    value: string;
    label: string;
    detail?: string;
    disabled?: boolean;
  };
  export let id: string;
  export let label: string;
  export let value = "";
  export let options: {
    value: string;
    label: string;
    shortLabel?: string;
    detail?: string;
    disabled?: boolean;
    /** An action item (such as "Open project"): runs, never becomes the value. */
    action?: () => void;
    /** Optional nested choices (such as a project's modules), shown beside the
     *  option while it is hovered or highlighted, after a click on its arrow,
     *  or with Right Arrow. The option itself remains a choice. A chosen nested
     *  value is shown with this option's label for context. A searchable
     *  list's filter (and its field's Left/Right Arrow) covers main options. */
    children?: Nested[];
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
  /** A small inline chip (composer tools) instead of a form field. */
  export let chip = false;
  /** The trigger's down arrow; a quiet filter row may leave it out. */
  export let arrow = true;
  /** Open the list above the trigger, for pickers near the bottom of a page.
   *  Where it does not fit above and there is more room below (a new
   *  conversation's composer sits near the top), it opens below instead. */
  export let above = false;
  export let onChange: (value: string) => void = () => {};
  /** Called when the list opens, before anything can be chosen. */
  export let onReveal: () => void = () => {};
  let root: HTMLDivElement;
  let trigger: HTMLButtonElement;
  let open = false;
  let active = 0;
  let query = "";
  let typedAt = 0;
  let filter = "";
  let searchInput: HTMLInputElement;
  // Measured each time an `above` list opens: the side it fits on, and the
  // height its options may use there (the rest of the list then scrolls).
  let openBelow = false;
  let room = "";
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
  // Nested choices are optional: without them every rule below is inert.
  // `inner` is the highlighted nested choice while the keyboard is in the
  // nested list (-1 on the main list); Escape there hides it (`collapsed`)
  // until another option is highlighted.
  let inner = -1;
  let collapsed = false;
  let hoverTimer: ReturnType<typeof setTimeout> | undefined;
  let nestedLeft = "";
  let nestedTop = "";
  let nestedRoom = "";
  $: nested = options.some((option) => !!option.children?.length);
  $: branch =
    open && !collapsed && options[active]?.children?.length ? active : -1;
  $: children = branch >= 0 ? options[branch].children! : [];
  $: nestedEnabled = children
    .map((child, index) => (child.disabled ? -1 : index))
    .filter((index) => index >= 0);
  $: parent = options.find(
    (option) =>
      !option.action &&
      option.value !== value &&
      option.children?.some((child) => child.value === value),
  );
  $: selected =
    options.find((option) => option.value === value) ?? nestedSelection(parent);
  $: unavailable = disabled || !options.some((option) => !option.disabled);
  $: if (unavailable) open = false;
  $: if (!enabled.includes(active)) active = enabled[0] ?? 0;
  $: if (inner >= 0 && !nestedEnabled.includes(inner))
    inner = nestedEnabled[0] ?? -1;
  $: if (!open) clearTimeout(hoverTimer);
  $: activeId =
    inner >= 0 ? `${id}-option-${active}-${inner}` : `${id}-option-${active}`;
  $: if (branch >= 0) void placeNested(branch, children);

  function nestedSelection(owner: (typeof options)[number] | undefined) {
    const child = owner?.children?.find((choice) => choice.value === value);
    if (!owner || !child) return undefined;
    return {
      label: `${child.label} · ${owner.label}`,
      shortLabel: owner.shortLabel
        ? `${child.label} · ${owner.shortLabel}`
        : undefined,
    };
  }

  function reveal() {
    if (unavailable) return;
    onReveal();
    filter = "";
    const index = options.findIndex((option) => option.value === value);
    active = Math.max(0, index);
    inner = -1;
    collapsed = false;
    // A chosen nested value opens beside its option, highlighted.
    if (index < 0 && parent && !parent.disabled) {
      active = options.indexOf(parent);
      inner = parent.children!.findIndex(
        (child) => child.value === value && !child.disabled,
      );
    }
    if (options[active]?.disabled) active = enabled[0];
    query = "";
    open = true;
    void place().then(showActive);
    if (searchable)
      void tick().then(() => searchInput?.focus({ preventScroll: true }));
  }
  /** Space is measured against the window and every clipping container (the
   *  scrolled page under the top bar), like the skill list and message menu. */
  async function place() {
    openBelow = false;
    room = "";
    await tick();
    const popup = root?.querySelector<HTMLElement>(".choice-popup");
    const list = popup?.querySelector<HTMLElement>(".choice-options");
    // Not laid out (closed again, or no layout engine): keep the default side.
    if (!above || !popup || !list || !popup.offsetHeight) return;
    const view = root.ownerDocument.defaultView ?? window;
    const box = trigger.getBoundingClientRect();
    let top = 0;
    let bottom = view.innerHeight;
    for (let node = root.parentElement; node; node = node.parentElement) {
      const overflow = view.getComputedStyle(node).overflowY;
      if (!/auto|scroll|hidden|clip/.test(overflow)) continue;
      const clip = node.getBoundingClientRect();
      top = Math.max(top, clip.top);
      bottom = Math.min(bottom, clip.bottom);
    }
    // The popup's 6 px offset plus a little air at the edge.
    const up = box.top - top - 14;
    const down = bottom - box.bottom - 14;
    openBelow = popup.offsetHeight > up && down > up;
    const chrome = popup.offsetHeight - list.offsetHeight;
    room = `${Math.max(72, Math.floor((openBelow ? down : up) - chrome))}px`;
  }
  let placing = 0;
  /** A nested list opens beside the main list where it fits (right, else
   *  left), level with its option, and stays inside the window and every
   *  clipping container; its own choices scroll when the height is short. */
  async function placeNested(..._changed: unknown[]) {
    const run = ++placing;
    await tick();
    const popup = root?.querySelector<HTMLElement>(".choice-popup");
    const panel = popup?.querySelector<HTMLElement>(".choice-nested");
    const list = panel?.querySelector<HTMLElement>(".choice-nested-options");
    const row = root?.querySelector<HTMLElement>(`#${id}-option-${branch}`);
    // Not laid out (closed again, or no layout engine): keep the CSS default.
    if (run !== placing || !popup || !panel || !list || !row) return;
    if (!panel.offsetWidth) return;
    const view = root.ownerDocument.defaultView ?? window;
    let left = 0;
    let top = 0;
    let right = view.innerWidth;
    let bottom = view.innerHeight;
    for (let node = root.parentElement; node; node = node.parentElement) {
      const style = view.getComputedStyle(node);
      const clipX = /auto|scroll|hidden|clip/.test(style.overflowX);
      const clipY = /auto|scroll|hidden|clip/.test(style.overflowY);
      if (!clipX && !clipY) continue;
      const clip = node.getBoundingClientRect();
      if (clipX) {
        left = Math.max(left, clip.left);
        right = Math.min(right, clip.right);
      }
      if (clipY) {
        top = Math.max(top, clip.top);
        bottom = Math.min(bottom, clip.bottom);
      }
    }
    const edge = 8;
    const gap = 4;
    const chrome = panel.offsetHeight - list.offsetHeight;
    nestedRoom = `${Math.max(72, Math.floor(bottom - top - 2 * edge - chrome))}px`;
    await tick();
    if (run !== placing) return;
    // Layout offsets, not the popup's own box: its opening animation scales
    // it, which would leave the nested list a little off once it settles.
    const base = root.getBoundingClientRect();
    const popupLeft = base.left + popup.offsetLeft;
    const popupTop = base.top + popup.offsetTop;
    const scroller = row.parentElement?.scrollTop ?? 0;
    const rowTop = popupTop + popup.clientTop + row.offsetTop - scroller;
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    let x = popupLeft + popup.offsetWidth + gap;
    if (x + width > right - edge) {
      const flipped = popupLeft - gap - width;
      // Neither side has room: overlap the main list, inside the edges.
      x =
        flipped >= left + edge
          ? flipped
          : Math.max(left + edge, right - edge - width);
    }
    const y = Math.max(
      top + edge,
      Math.min(
        rowTop - panel.clientTop - list.offsetTop,
        bottom - edge - height,
      ),
    );
    nestedLeft = `${Math.round(x - popupLeft - popup.clientLeft)}px`;
    nestedTop = `${Math.round(y - popupTop - popup.clientTop)}px`;
  }
  function showActive() {
    void tick().then(() =>
      root
        ?.querySelector<HTMLElement>(`#${activeId}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  /** Highlight an option of the main list (keyboard, typing or hover). */
  function highlight(index: number) {
    clearTimeout(hoverTimer);
    active = index;
    inner = -1;
    collapsed = false;
  }
  /** Move into an option's nested list, at its chosen value if it has one. */
  function enter(index: number) {
    const list = options[index]?.children ?? [];
    const chosen = list.findIndex(
      (child) => child.value === value && !child.disabled,
    );
    const first = chosen >= 0 ? chosen : list.findIndex((c) => !c.disabled);
    if (first < 0) return;
    clearTimeout(hoverTimer);
    collapsed = false;
    inner = first;
  }
  function step(pool: number[], current: number, key: string) {
    if (key === "Home") return pool[0];
    if (key === "End") return pool[pool.length - 1];
    const at = pool.indexOf(current) + (key === "ArrowDown" ? 1 : -1);
    return pool[(at + pool.length) % pool.length];
  }
  function hover(index: number, event: PointerEvent) {
    if (!nested || event.pointerType === "touch" || options[index]?.disabled)
      return;
    clearTimeout(hoverTimer);
    if (index === active) {
      inner = -1;
      collapsed = false;
    } else if (branch >= 0) {
      // On the way from an option to its nested list the pointer may cross
      // the next option: switch only once it rests there.
      hoverTimer = setTimeout(() => highlight(index), 160);
    } else highlight(index);
  }
  function hoverNested(index: number, event: PointerEvent) {
    if (event.pointerType === "touch") return;
    clearTimeout(hoverTimer);
    if (!children[index]?.disabled) inner = index;
  }
  function pick(index: number, event: MouseEvent) {
    // The arrow is a separate target that only shows the nested choices, so
    // they never depend on hover (touch, pen or a plain click).
    const target = event.target as Element | null;
    if (options[index]?.children?.length && target?.closest?.(".choice-more"))
      highlight(index);
    else choose(index);
  }
  function choose(index: number, nestedIndex = -1) {
    if (disabled || !options[index] || options[index].disabled) return;
    if (nestedIndex >= 0) {
      const child = options[index].children?.[nestedIndex];
      if (!child || child.disabled) return;
      value = child.value;
      onChange(value);
      open = false;
      trigger?.focus({ preventScroll: true });
      return;
    }
    const run = options[index].action;
    if (run) {
      open = false;
      trigger?.focus({ preventScroll: true });
      run();
      return;
    }
    value = options[index].value;
    onChange(value);
    open = false;
    trigger?.focus({ preventScroll: true });
  }
  function key(event: KeyboardEvent) {
    if (unavailable || event.altKey || event.ctrlKey || event.metaKey) return;
    // In a nested list: arrows, typing and Enter act on its choices.
    const within = open && inner >= 0;
    if (event.key === "Escape" && open) {
      // The open picker consumes Escape before the containing editor's drawer.
      event.preventDefault();
      event.stopPropagation();
      if (within) {
        // Leave only the nested list; a second Escape closes the picker.
        inner = -1;
        collapsed = true;
        return;
      }
      open = false;
      trigger?.focus({ preventScroll: true });
    } else if (event.key === "Tab") {
      open = false;
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) {
        if (within) choose(active, inner);
        else if (enabled.length) choose(active);
      } else reveal();
    } else if (
      open &&
      nested &&
      (event.key === "ArrowRight" || event.key === "ArrowLeft")
    ) {
      event.preventDefault();
      if (event.key === "ArrowLeft") inner = -1;
      else if (!within) enter(active);
      showActive();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open) reveal();
      else if (within) {
        if (nestedEnabled.length) inner = step(nestedEnabled, inner, event.key);
      } else if (event.key.startsWith("Arrow") && enabled.length)
        highlight(step(enabled, active, event.key));
      if (!within && !event.key.startsWith("Arrow") && enabled.length)
        highlight(step(enabled, active, event.key));
      showActive();
    } else if (event.key.length === 1) {
      event.preventDefault();
      if (!open) reveal();
      const now = Date.now();
      query =
        (now - typedAt < 700 ? query : "") + event.key.toLocaleLowerCase();
      typedAt = now;
      const pool: { label: string; disabled?: boolean }[] =
        inner >= 0 ? (options[active]?.children ?? []) : options;
      const found = pool.findIndex(
        (option) =>
          !option.disabled &&
          option.label.toLocaleLowerCase().startsWith(query),
      );
      if (found >= 0) {
        if (inner >= 0) inner = found;
        else highlight(found);
      }
      showActive();
    }
  }
  function contains(target: EventTarget | null) {
    return !!target && "nodeType" in target && root.contains(target as Node);
  }
  function blur(event: FocusEvent) {
    if (!contains(event.relatedTarget)) open = false;
  }
  function searchKey(event: KeyboardEvent) {
    if (["ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"].includes(event.key))
      key(event);
  }
  function outside(event: PointerEvent) {
    if (open && !contains(event.target)) open = false;
  }
  function reposition() {
    if (branch >= 0) void placeNested();
  }
  onMount(() => {
    // A document discussion may be mounted in a related desktop window.
    const owner = root.ownerDocument.defaultView;
    owner?.addEventListener("pointerdown", outside);
    owner?.addEventListener("resize", reposition);
    return () => {
      clearTimeout(hoverTimer);
      owner?.removeEventListener("pointerdown", outside);
      owner?.removeEventListener("resize", reposition);
    };
  });
</script>

<div
  class="choice-picker"
  class:compact
  class:chip
  class:above={above && !openBelow}
  bind:this={root}
>
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
    aria-activedescendant={open && enabled.length ? activeId : undefined}
    aria-required={required}
    aria-describedby={describedBy || undefined}
    aria-invalid={invalid || undefined}
    disabled={unavailable}
    on:click={() => (open ? (open = false) : reveal())}
    on:keydown={key}
    on:blur={blur}
  >
    <slot name="leading" />
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
    {#if arrow}<svg
        width="14"
        height="14"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.3"
        aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg
      >{/if}
  </button>
  {#if open}<div class="choice-popup" style:--choice-room={room || null}>
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
          aria-activedescendant={enabled.length ? activeId : undefined}
          on:keydown={searchKey}
          on:blur={blur}
        />{/if}
      <!-- A nested list renders beside, not inside, this scrolled list, so it
           is never clipped; aria-owns keeps its options in this listbox. -->
      <div
        class="choice-options"
        id={`${id}-options`}
        role="listbox"
        aria-labelledby={`${id}-label`}
        aria-owns={branch >= 0 ? `${id}-nested` : undefined}
        on:scroll={reposition}
      >
        {#each visible as { option, index } (option.value)}
          <button
            type="button"
            class="choice-option"
            class:choice-action={!!option.action}
            class:highlighted={index === active && inner < 0}
            class:expanded={index === branch}
            id={`${id}-option-${index}`}
            role="option"
            aria-selected={value === option.value}
            aria-describedby={option.children?.length
              ? `${id}-nested-hint`
              : undefined}
            tabindex="-1"
            disabled={option.disabled}
            on:pointerdown|preventDefault
            on:pointerenter={(event) => hover(index, event)}
            on:click={(event) => pick(index, event)}
          >
            <span class="option-copy"
              ><span>{option.label}</span>{#if option.detail}<small
                  >{option.detail}</small
                >{/if}</span
            >
            {#if option.children?.length}<span class="choice-end"
                >{#if value === option.value}<svg
                    width="14"
                    height="14"
                    viewBox="0 0 20 20"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.3"
                    aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg
                  >{/if}<span class="choice-more" aria-hidden="true"
                  ><svg
                    width="14"
                    height="14"
                    viewBox="0 0 20 20"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.3"><path d="m8 5 5 5-5 5" /></svg
                  ></span
                ></span
              >{:else if value === option.value}<svg
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
      {#if branch >= 0}<div
          class="choice-nested"
          id={`${id}-nested`}
          role="group"
          aria-labelledby={`${id}-option-${branch}`}
          style:left={nestedLeft || null}
          style:top={nestedTop || null}
          style:--choice-nested-room={nestedRoom || null}
        >
          <div class="choice-nested-options">
            {#each children as child, nestedIndex (child.value)}
              <button
                type="button"
                class="choice-option"
                class:highlighted={nestedIndex === inner}
                id={`${id}-option-${branch}-${nestedIndex}`}
                role="option"
                aria-selected={value === child.value}
                tabindex="-1"
                disabled={child.disabled}
                on:pointerdown|preventDefault
                on:pointerenter={(event) => hoverNested(nestedIndex, event)}
                on:click={() => choose(branch, nestedIndex)}
              >
                <span class="option-copy"
                  ><span>{child.label}</span>{#if child.detail}<small
                      >{child.detail}</small
                    >{/if}</span
                >
                {#if value === child.value}<svg
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
        </div>{/if}
      {#if nested}<span class="visually-hidden" id={`${id}-nested-hint`}
          >Right Arrow shows more choices.</span
        >{/if}
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
    font-size: var(--ke-text-2xs);
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
    font-size: var(--ke-text-2xs);
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
    font-size: var(--ke-text-sm);
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
    box-shadow: var(--ke-overlay-shadow);
    transform-origin: top left;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .choice-options {
    /* A measured list (see place) uses the room on its side instead of 40vh. */
    max-height: min(320px, var(--choice-room, 40vh));
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
    font-size: var(--ke-text-sm);
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
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
  }
  /* An option with nested choices: its own check, then the arrow, which is a
     separate click/touch target reaching the option's edge. */
  .choice-end {
    display: inline-flex;
    flex: none;
    align-items: center;
    align-self: stretch;
    gap: 4px;
  }
  .choice-more {
    display: inline-flex;
    align-items: center;
    align-self: stretch;
    margin: -9px -10px -9px 0;
    padding: 0 8px 0 6px;
    border-radius: 0 var(--ke-radius-sm) var(--ke-radius-sm) 0;
    color: var(--ke-text-tertiary);
  }
  .choice-option.expanded .choice-more {
    color: var(--ke-text-secondary);
  }
  .choice-picker .choice-option.expanded {
    background: color-mix(in srgb, var(--ke-marker-sage) 7%, transparent);
  }
  .choice-nested {
    box-sizing: border-box;
    position: absolute;
    top: 0;
    left: calc(100% + 4px);
    z-index: 1;
    width: max-content;
    min-width: 180px;
    max-width: min(280px, calc(100vw - 32px));
    padding: 5px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-overlay-shadow);
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .choice-nested-options {
    max-height: min(320px, var(--choice-nested-room, 40vh));
    overflow-y: auto;
    overscroll-behavior: contain;
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
  /* Chip: a small inline control whose list may be wider than itself. */
  .chip {
    display: inline-block;
    max-width: 100%;
  }
  .chip .choice-trigger {
    width: auto;
    max-width: 100%;
    min-height: 28px;
    gap: 6px;
    padding: 0 9px 0 10px;
    border-radius: var(--ke-radius-md);
    background: var(--ke-soft-fill);
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .chip .choice-value {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chip .choice-trigger:hover:not(:disabled),
  .chip .choice-trigger[aria-expanded="true"] {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .chip .choice-popup {
    width: max-content;
    min-width: 220px;
    max-width: min(320px, calc(100vw - 32px));
  }
  .above .choice-popup {
    top: auto;
    bottom: calc(100% + 6px);
    transform-origin: bottom left;
  }
  .choice-action {
    margin-top: 4px;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
</style>
