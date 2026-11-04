<script lang="ts">
  import { marked } from "marked";
  import DOMPurify from "dompurify";
  import {
    topics,
    helpPrompt,
    topicNotes,
    type HelpDestination,
  } from "./guide";
  import Icon from "$lib/design/Icon.svelte";

  export let onAsk: (text: string) => void = () => {};
  export let onSettings: (destination: HelpDestination) => void = () => {};
  export let canAsk = false;
  export let disabled = false;
  let selected = "";
  $: topic = topics.find((item) => item.id === selected);
  $: sections = topic?.body.split(/^## /m) ?? [];
  function html(body: string) {
    return DOMPurify.sanitize(marked.parse(body, { async: false }), {
      FORBID_TAGS: ["img", "iframe", "form"],
    });
  }
</script>

<section aria-label="Kejtu help" class="help">
  <h2 class="settings-title">Help</h2>
  <p class="intro">
    A little guidance when you need it. Available offline, for this version of
    Kejtu.
  </p>
  {#if !topic}
    <div class="start-here">
      <span class="start-icon"><Icon name="comment" size={24} /></span>
      <div>
        <h3>Start with something useful</h3>
        <p>
          Connect a model, then ask Kejtu to help with one small task. You can
          explore the rest as you go.
        </p>
        <div class="actions">
          <button type="button" {disabled} on:click={() => onSettings("models")}
            >Connect a model <span aria-hidden="true">→</span></button
          >
          <button type="button" on:click={() => (selected = topics[0].id)}
            >Getting started</button
          >
        </div>
      </div>
    </div>
    <h3 class="browse-title">Explore Kejtu</h3>
    <nav class="topics" aria-label="Help topics">
      {#each topics as item}
        <button type="button" on:click={() => (selected = item.id)}>
          <span class="topic-icon"
            ><Icon name={topicNotes[item.id].icon} size={19} /></span
          >
          <span
            ><strong>{item.title}</strong><small
              >{topicNotes[item.id].summary}</small
            ></span
          >
          <span class="chevron" aria-hidden="true">›</span>
        </button>
      {/each}
    </nav>
  {:else}
    <button type="button" class="back" on:click={() => (selected = "")}
      >← All help topics</button
    >
    <article aria-label={topic.title}>
      <h3 class="topic-title">{topic.title}</h3>
      <div class="prose">{@html html(sections[0].replace(/^# .*\n/, ""))}</div>
      {#if topicNotes[topic.id].destination}
        <button
          type="button"
          class="destination"
          {disabled}
          on:click={() => onSettings(topicNotes[topic!.id].destination!)}
        >
          Open {topicNotes[topic.id].label} <span aria-hidden="true">→</span>
        </button>
      {/if}
      <div class="sections">
        {#each sections.slice(1) as section (section)}
          <details>
            <summary>{section.split("\n")[0]}</summary>
            <div class="prose">
              {@html html(section.slice(section.indexOf("\n") + 1))}
            </div>
          </details>
        {/each}
      </div>
      <div class="ask">
        <div>
          <strong>Talk it through</strong>
          <p>
            {canAsk
              ? "Prepare a question about this topic. Review it in your message box before sending."
              : "Open a conversation to ask Kejtu about this topic. The guide is always available here."}
          </p>
        </div>
        <button
          type="button"
          disabled={!canAsk || disabled}
          on:click={() => onAsk(helpPrompt(topic!.id))}>Ask Kejtu</button
        >
      </div>
    </article>
  {/if}
</section>

<style>
  .help {
    line-height: 1.6;
  }
  .intro {
    color: var(--ke-text-secondary);
    margin: 0 0 2rem;
  }
  .start-here {
    display: flex;
    gap: 1.15rem;
    padding: 1.5rem;
    border-radius: 14px;
    background: var(--ke-surface-inset);
  }
  .start-icon,
  .topic-icon {
    color: var(--ke-text-secondary);
    display: inline-flex;
    flex-shrink: 0;
  }
  .start-icon {
    padding-top: 0.25rem;
  }
  .help .start-here h3 {
    margin: 0 0 0.4rem;
    font-size: 17px;
    font-weight: 500;
  }
  .start-here p {
    margin: 0;
    max-width: 34rem;
    color: var(--ke-text-secondary);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 1rem;
  }
  .help .actions button {
    background: var(--ke-surface-raised);
    padding: 0.5rem 0.75rem;
  }
  .help .browse-title {
    margin: 2rem 0 0.75rem;
    font-size: 14px;
    font-weight: 500;
  }
  .topics {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.6rem;
  }
  .help .topics button {
    display: flex;
    align-items: flex-start;
    gap: 0.8rem;
    padding: 1rem;
    text-align: left;
    background: transparent;
    border-radius: 10px;
  }
  .topics button > span:nth-child(2) {
    min-width: 0;
  }
  .topics strong {
    display: block;
    font-size: 14px;
    font-weight: 500;
  }
  .topics small {
    display: block;
    margin-top: 0.3rem;
    color: var(--ke-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }
  .chevron {
    margin-left: auto;
  }
  .help .topics button:hover,
  .help .topics button:focus-visible {
    background: var(--ke-surface-inset);
  }
  .help .topics button:focus-visible {
    outline: none;
    box-shadow: inset 3px 0 var(--ke-focus-ring);
  }
  .help .back {
    background: transparent;
    padding-left: 0;
    margin-bottom: 1rem;
    color: var(--ke-text-secondary);
  }
  .help .topic-title {
    margin: 0 0 1rem;
    font-family: var(--ke-font-display);
    font-size: 24px;
    line-height: 1.3;
    font-weight: 400;
  }
  .help .destination {
    margin: 0.25rem 0 1.25rem;
  }
  .sections {
    margin-top: 1rem;
  }
  details {
    border-bottom: 1px solid var(--ke-border);
  }
  summary {
    padding: 1rem 0;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
  }
  details[open] summary {
    padding-bottom: 0.5rem;
  }
  .prose {
    overflow-wrap: anywhere;
  }
  .prose :global(p) {
    margin: 0 0 1rem;
  }
  .prose :global(h3) {
    font-size: 14px;
    margin: 1.25rem 0 0.6rem;
    font-weight: 600;
  }
  .prose :global(table) {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .prose :global(th),
  .prose :global(td) {
    text-align: left;
    vertical-align: top;
    padding: 0.6rem;
    border-bottom: 1px solid var(--ke-border);
  }
  .prose :global(blockquote) {
    margin: 1rem 0;
    padding: 0.75rem 1rem;
    background: var(--ke-surface-inset);
    border-radius: 8px;
  }
  .prose :global(blockquote p:last-child) {
    margin: 0;
  }
  .prose :global(li) {
    margin: 0.5rem 0;
  }
  .ask {
    display: flex;
    align-items: center;
    gap: 1.5rem;
    justify-content: space-between;
    padding: 1.25rem;
    margin-top: 2rem;
    background: var(--ke-surface-inset);
    border-radius: 12px;
  }
  .ask strong {
    font-size: 14px;
    font-weight: 500;
  }
  .ask p {
    margin: 0.25rem 0 0;
    font-size: 12px;
    color: var(--ke-text-secondary);
  }
  .ask button {
    flex-shrink: 0;
  }
  @media (max-width: 1000px) {
    .topics {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 600px) {
    .start-here {
      padding: 1rem;
    }
    .start-icon {
      display: none;
    }
    .ask {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.75rem;
    }
    .prose :global(thead) {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
    }
    .prose :global(tbody),
    .prose :global(tr),
    .prose :global(td) {
      display: block;
    }
    .prose :global(tr) {
      padding: 0.5rem 0;
      border-bottom: 1px solid var(--ke-border);
    }
    .prose :global(td) {
      border: 0;
      padding: 0.25rem 0;
    }
    .prose :global(td:first-child) {
      font-weight: 600;
    }
  }
</style>
