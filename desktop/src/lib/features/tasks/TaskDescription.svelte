<script lang="ts">
  import { tick } from "svelte";
  import { openWebLink } from "$lib/api/web-links";
  import {
    DESCRIPTION_BYTES,
    taskDescription,
    toggleTaskCheck,
  } from "./task-description";
  export let value = "";
  export let disabled = false;
  export let onChange: (value: string) => void = () => {};
  let preview = false;
  let field: HTMLTextAreaElement;
  let error = "";
  $: rendered = preview ? taskDescription(value) : { html: "", checks: [] };
  $: tooLong = new TextEncoder().encode(value).length > DESCRIPTION_BYTES;
  function change(next: string) {
    value = next;
    onChange(next);
  }
  async function format(prefix: string, suffix = "", multiline = false) {
    const start = field.selectionStart,
      end = field.selectionEnd;
    const selected = value.slice(start, end) || "Text";
    const replacement = multiline
      ? selected
          .split("\n")
          .map((line) => prefix + line)
          .join("\n")
      : prefix + selected + suffix;
    change(value.slice(0, start) + replacement + value.slice(end));
    await tick();
    field.focus();
    field.setSelectionRange(
      start + prefix.length,
      start + replacement.length - suffix.length,
    );
  }
  async function click(event: MouseEvent) {
    const element = event.target as HTMLElement;
    const link = element.closest("a");
    if (link) {
      event.preventDefault();
      try {
        await openWebLink(link.href);
      } catch (e) {
        error = String(e);
      }
    }
    if (
      element instanceof HTMLInputElement &&
      element.dataset.taskCheck !== undefined &&
      !disabled
    )
      change(
        toggleTaskCheck(
          value,
          rendered.checks[Number(element.dataset.taskCheck)] ?? -1,
          element.checked,
        ),
      );
  }
</script>

<div class="description">
  <div class="description-heading">
    <label for="task-description">Description</label>
    <div class="modes" aria-label="Description view">
      <button
        type="button"
        aria-pressed={!preview}
        on:click={() => (preview = false)}>Write</button
      >
      <button
        type="button"
        aria-pressed={preview}
        on:click={() => (preview = true)}>Preview</button
      >
    </div>
  </div>
  {#if preview}
    <!-- Links use the native browser boundary; checkboxes edit only this draft. -->
    <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
    <div
      class="preview"
      class:disabled
      on:click={click}
      aria-label="Task description preview"
    >
      {#if value}{@html disabled
          ? rendered.html.replace(/<input /g, "<input disabled ")
          : rendered.html}{:else}<p class="placeholder">
          Add the details and steps you need.
        </p>{/if}
    </div>
  {:else}
    <div class="toolbar" aria-label="Description formatting">
      <button
        type="button"
        {disabled}
        title="Bold"
        on:click={() => format("**", "**")}><strong>B</strong></button
      >
      <button
        type="button"
        {disabled}
        title="Italic"
        on:click={() => format("*", "*")}><em>I</em></button
      >
      <button type="button" {disabled} on:click={() => format("### ", "", true)}
        >Heading</button
      >
      <button type="button" {disabled} on:click={() => format("- ", "", true)}
        >Bullets</button
      >
      <button type="button" {disabled} on:click={() => format("1. ", "", true)}
        >Numbered list</button
      >
      <button
        type="button"
        {disabled}
        on:click={() => format("- [ ] ", "", true)}>Checklist</button
      >
      <button
        type="button"
        {disabled}
        on:click={() => format("[", "](https://)")}>Link</button
      >
    </div>
    <textarea
      id="task-description"
      bind:this={field}
      {value}
      {disabled}
      on:input={(event) => change(event.currentTarget.value)}
      rows="8"
      placeholder="What needs doing? Add context, steps or a checklist…"
      aria-describedby="task-description-hint"
    ></textarea>
    <p class="hint" id="task-description-hint">
      Use the formatting buttons or Markdown. Preview lets you tick checklist
      items.
    </p>
  {/if}
  {#if tooLong}<p role="alert">
      This description exceeds 32 KiB. Shorten it before saving; your draft is
      retained.
    </p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
</div>

<style>
  .description {
    grid-column: 1 / -1;
    min-width: 0;
  }
  .description-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }
  label {
    color: var(--ke-text-secondary);
    font-size: 12px;
  }
  .modes {
    display: flex;
    gap: 3px;
  }
  .modes button,
  .toolbar button {
    margin: 0;
    padding: 5px 9px;
    background: transparent;
    color: var(--ke-text-secondary);
    border: 0;
    border-radius: var(--ke-radius-sm);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .modes button[aria-pressed="true"],
  .toolbar button:hover:not(:disabled) {
    background: var(--ke-accent-soft);
    color: var(--ke-text-primary);
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    border: 1px solid var(--ke-border);
    border-bottom: 0;
    border-radius: var(--ke-radius-sm) var(--ke-radius-sm) 0 0;
    padding: 5px;
    background: var(--ke-surface-raised);
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    min-height: 200px;
    resize: vertical;
    border: 1px solid var(--ke-border);
    border-radius: 0 0 var(--ke-radius-sm) var(--ke-radius-sm);
    padding: 14px;
    font: inherit;
    line-height: 1.65;
    color: var(--ke-text-primary);
    background: var(--ke-surface-raised);
  }
  .hint,
  .placeholder {
    color: var(--ke-text-tertiary);
    font-size: 12px;
    margin: 6px 0 0;
  }
  .preview {
    min-height: 200px;
    padding: 14px 18px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    line-height: 1.7;
    overflow-wrap: anywhere;
  }
  .preview :global(p) {
    margin: 0 0 12px;
  }
  .preview :global(ul),
  .preview :global(ol) {
    padding-left: 24px;
  }
  .preview :global(input[type="checkbox"]) {
    width: 15px;
    height: 15px;
    margin-right: 7px;
    accent-color: var(--ke-accent);
  }
  .preview :global(li:has(> input[type="checkbox"])),
  .preview :global(li:has(> p > input[type="checkbox"])) {
    list-style: none;
    margin-left: -21px;
    padding: 2px 0;
  }
  .preview :global(a) {
    color: var(--ke-accent);
    text-decoration: underline;
  }
  .preview :global(pre) {
    overflow: auto;
    white-space: pre;
  }
  .preview :global(h1),
  .preview :global(h2),
  .preview :global(h3),
  .preview :global(h4) {
    font-size: 15px;
    margin: 15px 0 8px;
  }
  .preview.disabled :global(input) {
    pointer-events: none;
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
</style>
