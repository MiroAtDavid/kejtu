<script lang="ts">
  import { tick } from "svelte";
  import type { ReminderAttention } from "$lib/api/wire.generated";
  import {
    arrange,
    detail,
    laterLabel,
    laterUntil,
    type Entry,
  } from "./home-attention";
  export let items: ReminderAttention[] = [];
  export let disabled = false;
  export let more = false;
  export let error = "";
  export let pending = false;
  export let absent = false;
  /** Home's own timezone; a reminder's zone is mentioned only if different. */
  export let zone = "";
  export let onAct: (
    item: ReminderAttention,
    action: "acknowledge" | "later",
    until?: string,
  ) => void;
  export let onMore: () => void;
  export let onRecover: () => void;
  export let onKeep: () => void;
  export let onTask: (id: string) => void;

  let open: string | null = null;
  let expanded = false;
  let laterAt = "";
  let section: HTMLElement;
  const triggers: Record<string, HTMLButtonElement> = {};
  // Re-read the clock whenever the list changes, so day words stay current.
  const stamp = (_: ReminderAttention[]) => new Date();
  $: now = stamp(items);
  $: line = arrange(items, now, more);
  // A reminder handled elsewhere simply has no detail to show any more.
  $: openItem = items.find((item) => item.id === open) ?? null;
  $: if (!line.rest.length && !more) expanded = false;

  function toggle(item: ReminderAttention) {
    if (open === item.id) {
      open = null;
      return;
    }
    open = item.id;
    laterAt = laterUntil(new Date(), item.timezone);
  }
  async function close() {
    const id = open;
    open = null;
    await tick();
    if (id) triggers[id]?.focus();
  }
  function acknowledge(item: ReminderAttention) {
    open = null;
    onAct(item, "acknowledge");
  }
  function later(item: ReminderAttention) {
    // Send exactly the time that is shown. If that time has (nearly) passed
    // while the detail stayed open, show a fresh one and wait for another click.
    if (!laterAt || Date.parse(laterAt) - Date.now() < 2 * 60000) {
      laterAt = laterUntil(new Date(), item.timezone);
      return;
    }
    open = null;
    onAct(item, "later", laterAt);
  }
  const separator = (index: number, count: number, tail: boolean) =>
    index === 0 ? "" : index === count - 1 && !tail ? " and " : ", ";
</script>

{#snippet title(entry: Entry)}<button
    type="button"
    class="title"
    bind:this={triggers[entry.item.id]}
    aria-expanded={open === entry.item.id}
    aria-controls={open === entry.item.id ? "reminder-detail" : undefined}
    title={entry.item.title}
    on:click={() => toggle(entry.item)}>{entry.item.title}</button
  >{#if entry.note}<span class="note">{" " + entry.note}</span>{/if}{/snippet}

{#snippet panel(item: ReminderAttention)}
  <div class="detail" id="reminder-detail" role="group" aria-label={item.title}>
    {#if item.text.trim() !== item.title.trim()}<p class="text">
        {item.text}
      </p>{/if}
    <p class="when">{detail(item, now, zone)}</p>
    <div class="actions">
      <button
        type="button"
        disabled={disabled || pending}
        on:click={() => acknowledge(item)}>Got it</button
      >
      <span class="later"
        ><button
          type="button"
          disabled={disabled || pending}
          aria-describedby="reminder-later-until"
          on:click={() => later(item)}>Later</button
        ><span class="until" id="reminder-later-until"
          >{laterLabel(laterAt, new Date(), item.timezone)}</span
        ></span
      >
      {#if item.task_ref}<button
          type="button"
          {disabled}
          on:click={() => onTask(item.task_ref!)}>Open task</button
        >{/if}
    </div>
  </div>
{/snippet}

<svelte:window
  on:keydown={(event) => {
    if (
      event.key === "Escape" &&
      open &&
      event.target instanceof Node &&
      section?.contains(event.target)
    )
      void close();
  }}
/>

{#if items.length || error || pending}
  <section class="reminders" aria-label="Reminders" bind:this={section}>
    {#if items.length}
      <p class="line">
        <span class="dot" aria-hidden="true"></span>To remember:{" "}
        {#each line.named as entry, index (entry.item.id)}{separator(
            index,
            line.named.length,
            !!line.restLabel,
          )}{@render title(entry)}{/each}{#if line.restLabel}{" and "}<button
            type="button"
            class="rest"
            aria-expanded={expanded}
            on:click={() => (expanded = !expanded)}>{line.restLabel}</button
          >{/if}.
      </p>
      {#if openItem && !line.rest.some((entry) => entry.item.id === openItem.id)}
        {@render panel(openItem)}
      {/if}
      {#if expanded}
        <ul class="rest-list">
          {#each line.rest as entry (entry.item.id)}<li>
              {@render title(entry)}
              {#if openItem?.id === entry.item.id}{@render panel(openItem)}{/if}
            </li>{/each}
          {#if more}<li>
              <button type="button" class="rest" {disabled} on:click={onMore}
                >Show more reminders</button
              >
            </li>{/if}
        </ul>
      {/if}
    {/if}
    {#if error || pending}
      <p class="status" role="status">
        {error || "A reminder change needs to be checked."}
        <button type="button" {disabled} on:click={onRecover}
          >Check status</button
        >
        {#if absent}<button type="button" {disabled} on:click={onKeep}
            >Keep unchanged</button
          >{/if}
      </p>
    {/if}
  </section>
{/if}

<style>
  .reminders {
    margin-top: 12px;
    max-width: 62ch;
  }
  p {
    margin: 0;
  }
  .line {
    color: var(--ke-text-secondary);
    font: 400 15px/1.6 var(--ke-font-heading);
    overflow-wrap: anywhere;
  }
  .dot {
    display: inline-block;
    width: 5px;
    height: 5px;
    margin: 0 9px 0 1px;
    border-radius: 50%;
    vertical-align: middle;
    background: var(--ke-accent);
  }
  button {
    border: 0;
    padding: 0;
    background: none;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  button:disabled {
    cursor: default;
    opacity: 0.45;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
    border-radius: 2px;
  }
  .title {
    display: inline-block;
    max-width: 26ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    vertical-align: bottom;
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-decoration-color: var(--ke-border-strong);
    text-decoration-thickness: 1px;
    text-underline-offset: 4px;
  }
  .title:hover,
  .title[aria-expanded="true"] {
    text-decoration-color: currentColor;
  }
  .note {
    color: var(--ke-text-tertiary);
  }
  .rest {
    color: var(--ke-text-secondary);
    text-decoration: underline dotted;
    text-decoration-color: var(--ke-border-strong);
    text-underline-offset: 4px;
  }
  .rest:hover:not(:disabled) {
    color: var(--ke-text-primary);
  }
  .rest-list {
    list-style: none;
    margin: 6px 0 0;
    padding: 0 0 0 15px;
    font: 400 15px/1.7 var(--ke-font-heading);
    color: var(--ke-text-secondary);
  }
  .detail {
    margin: 10px 0 0 15px;
    padding-left: 12px;
    border-left: 1px solid var(--ke-border);
  }
  .rest-list .detail {
    margin: 4px 0 8px;
  }
  .text {
    color: var(--ke-text-primary);
    font: 400 14px/1.6 var(--ke-font-heading);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .when {
    margin-top: 2px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    line-height: 1.7;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 18px;
    margin-top: 4px;
  }
  .until {
    margin-left: 6px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
  }
  .actions button,
  .status button {
    padding: 4px 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .actions button:hover:not(:disabled),
  .status button:hover:not(:disabled) {
    color: var(--ke-text-primary);
    text-decoration: underline;
    text-underline-offset: 4px;
  }
  .status {
    margin-top: 8px;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    line-height: 1.8;
  }
  .status button {
    margin-left: 12px;
  }
</style>
