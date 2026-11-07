<script lang="ts">
  export let text = "";
  export let complete = true;
  export let onInspect: (() => void) | undefined = undefined;
  export let onDetails:
    ((section: "activity" | "context") => void) | undefined = undefined;
  export let onToggleAnswer: (() => void) | undefined = undefined;
  export let collapsed = false;
  export let answerText = "";
  export let answerComplete = true;
  export let onAction:
    | ((
        action: "save_memory" | "exclude" | "delete",
        text: string,
      ) => Promise<void>)
    | undefined = undefined;
  export let disabled = false;
  export let recallExcluded = false;
  export let partialRecallExcluded = false;
  let editing = false;
  let memoryText = "";
  let saving = false;
  $: memoryBytes = new TextEncoder().encode(memoryText).length;
  async function act(action: "save_memory" | "exclude" | "delete", value = "") {
    if (disabled || saving || !onAction) return;
    saving = true;
    close();
    try {
      await onAction(action, value);
      editing = false;
    } catch (caught) {
      message =
        caught instanceof Error
          ? caught.message
          : "The change could not be confirmed.";
    } finally {
      saving = false;
    }
  }
  let open = false;
  let above = false;
  let menuHeight = 400;
  let message = "";
  let root: HTMLDivElement;
  let trigger: HTMLButtonElement;
  function placeMenu() {
    const bounds = trigger?.getBoundingClientRect();
    if (!bounds) return;
    const below = window.innerHeight - bounds.bottom - 12;
    const before = bounds.top - 52;
    above = below < 360 && before > below;
    menuHeight = Math.max(80, above ? before : below);
  }
  function close(restoreFocus = false) {
    open = false;
    message = "";
    if (restoreFocus) trigger?.focus();
  }
  function outside(event: PointerEvent) {
    if (open && !event.composedPath().includes(root)) close();
  }
  function keyboard(event: KeyboardEvent) {
    if (open && event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  }
  async function copy(value = text) {
    try {
      await navigator.clipboard.writeText(value);
      message = "Copied";
    } catch {
      message = "Could not copy. Select the text to copy it.";
    }
  }
</script>

<svelte:window
  on:pointerdown={outside}
  on:keydown|capture={keyboard}
  on:resize={placeMenu}
/>
<div class="message-actions" bind:this={root}>
  <button
    class="trigger"
    type="button"
    bind:this={trigger}
    aria-label="Message actions"
    title="Message actions"
    aria-expanded={open}
    on:click={() => {
      if (open) close();
      else {
        placeMenu();
        open = true;
      }
    }}
  >
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <circle cx="4" cy="10" r="1.3" /><circle cx="10" cy="10" r="1.3" /><circle
        cx="16"
        cy="10"
        r="1.3"
      />
    </svg>
  </button>
  {#if open}
    <div
      class="actions"
      class:above
      style:max-height={`${menuHeight}px`}
      role="group"
      aria-label="Actions for this message"
    >
      <button type="button" disabled={!text} on:click={() => copy()}>
        {complete ? "Copy question" : "Copy visible question"}
      </button>
      {#if answerText}
        <button type="button" on:click={() => copy(answerText)}
          >{answerComplete ? "Copy answer" : "Copy visible answer"}</button
        >
      {/if}
      {#if onDetails}
        <button
          type="button"
          on:click={() => {
            close(true);
            onDetails?.("activity");
          }}>Tool activity</button
        >
        <button
          type="button"
          on:click={() => {
            close(true);
            onDetails?.("context");
          }}>Context details</button
        >
      {/if}
      {#if onToggleAnswer}
        <button
          type="button"
          on:click={() => {
            close(true);
            onToggleAnswer?.();
          }}>{collapsed ? "Expand answer" : "Collapse answer"}</button
        >
      {/if}
      {#if onInspect}
        <button
          type="button"
          on:click={() => {
            close();
            onInspect?.();
          }}>Inspect response</button
        >
      {/if}
      {#if partialRecallExcluded}<p>
          Original message exclusions are preserved.
        </p>{/if}
      {#if onAction}
        <button
          type="button"
          disabled={disabled ||
            saving ||
            recallExcluded ||
            partialRecallExcluded}
          on:click={() => {
            close();
            memoryText =
              complete && new TextEncoder().encode(text).length <= 8192
                ? text
                : "";
            editing = true;
          }}>Save a memory…</button
        >
        <button
          type="button"
          disabled={disabled || saving || recallExcluded}
          on:click={() => act("exclude")}
          >{recallExcluded
            ? "Excluded from recall"
            : "Exclude from recall"}</button
        >
        <button
          type="button"
          disabled={disabled || saving}
          title="Delete this question and reply. Later replies, notes and other copies stay."
          on:click={() => act("delete")}>Delete exchange</button
        >
        <p>
          Deletes this question and reply. Later replies, notes and other copies
          stay.
        </p>
      {/if}
      {#if message}<p role="status">{message}</p>{/if}
    </div>
  {/if}
  {#if message && !open && !editing}<p class="action-error" role="alert">
      {message}
    </p>{/if}
  {#if editing}
    <form
      class="memory-editor"
      aria-label="Save a memory"
      on:submit|preventDefault={() => act("save_memory", memoryText)}
    >
      <label for="memory-draft">Memory</label>
      <textarea
        id="memory-draft"
        bind:value={memoryText}
        rows="5"
        disabled={disabled || saving}
      ></textarea>
      <p>Saved as your words, linked to this question.</p>
      {#if message}<p role="alert">{message}</p>{/if}
      {#if memoryBytes > 8192}<p role="alert">
          This memory is too long. Shorten it before saving.
        </p>{/if}
      <div class="editor-buttons">
        <button
          type="button"
          disabled={saving}
          on:click={() => (editing = false)}>Cancel</button
        >
        <button
          type="submit"
          disabled={disabled ||
            saving ||
            !memoryText.trim() ||
            memoryBytes > 8192}>Save memory</button
        >
      </div>
    </form>
  {/if}
</div>

<style>
  .message-actions {
    position: relative;
  }
  .action-error {
    position: absolute;
    right: 0;
    width: min(25rem, 70vw);
    padding: 0.75rem;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-primary);
    z-index: 12;
  }
  button {
    font: inherit;
    cursor: pointer;
    border: 0;
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
    background: transparent;
  }
  .trigger {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
  }
  .trigger svg {
    fill: currentColor;
  }
  button:hover,
  .trigger[aria-expanded="true"] {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  button:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .actions {
    box-sizing: border-box;
    overflow-y: auto;
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 10;
    width: min(12rem, 70vw);
    padding: 0.35rem;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-overlay-shadow);
    font-size: var(--ke-text-sm);
    transform-origin: top right;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .actions.above {
    top: auto;
    bottom: calc(100% + 4px);
  }
  .actions button {
    display: block;
    width: 100%;
    padding: 0.5rem 0.6rem;
    text-align: left;
  }
  .actions p {
    margin: 0;
    padding: 0.5rem 0.6rem;
    font-size: var(--ke-text-sm);
  }
  .memory-editor {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 11;
    width: min(25rem, 70vw);
    padding: 1rem;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    box-shadow: var(--ke-overlay-shadow);
    font-size: var(--ke-text-base);
    transform-origin: top right;
    animation: ke-menu-in var(--ke-motion-base) var(--ke-motion-ease);
  }
  .memory-editor textarea {
    box-sizing: border-box;
    width: 100%;
    margin-top: 0.5rem;
    padding: 0.6rem;
    resize: vertical;
    color: var(--ke-text-primary);
    background: var(--ke-surface-inset);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    font: inherit;
  }
  .memory-editor p {
    color: var(--ke-text-secondary);
  }
  .editor-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 0.7rem;
  }
  .editor-buttons button {
    padding: 0.4rem 0.6rem;
  }
</style>
