<script lang="ts">
  import DocumentEditCard from "./DocumentEditCard.svelte";
  import { replySummary } from "$lib/features/workspace/document-assist";
  import StudyRequestText from "./StudyRequestText.svelte";
  import RichText from "./RichText.svelte";
  import CopyButton from "./CopyButton.svelte";
  import { imageParts } from "./images";
  import ImageGallery from "./ImageGallery.svelte";
  export let role: "user" | "answer" | "partial";
  /** Journal presentation: the answer sits under its question with no role label. */
  export let hideRole = false;
  export let hideCopy = false;
  export let state: "ready" | "partial";
  export let content = "";
  export let complete = true;
  export let end = 0;
  export let total = 0;
  export let loading = false;
  export let streaming = false;
  export let working = false;
  export let turnId = "";
  export let onMore: () => void = () => {};

  $: label =
    role === "user"
      ? "You"
      : role === "partial" || state === "partial"
        ? "Kejtu — partial answer"
        : "Kejtu";
</script>

<article class="body" class:user={role === "user"}>
  {#if !hideRole}<p class="role">{label}</p>{/if}
  {#if role !== "user" && (!hideCopy || working)}<div class="answer-tools">
      <slot />
      <div class="answer-copy">
        {#if !hideCopy && !working && !streaming && content}
          <CopyButton
            text={content}
            label={!complete ? "Copy visible answer" : "Copy answer"}
          />
        {/if}
      </div>
    </div>{/if}
  {#if role === "user" && !imageParts(content, complete).some((p) => p.kind !== "text")}<div
      class="text"
    >
      <StudyRequestText text={content} {complete} />
    </div>{:else if role === "user"}<div class="text">
      {#each imageParts(content, complete) as part}{#if part.kind === "pending"}<span
            class="image-pending"
            >Image preview needs the remaining message data.</span
          >{:else if part.kind === "text"}<StudyRequestText
            text={part.value}
            {complete}
          />{/if}{/each}
      <ImageGallery
        images={imageParts(content, complete)
          .filter((p) => p.kind === "image")
          .map((part, index) => ({ id: `inline-${index}`, src: part.value }))}
      />
    </div>{:else}
    <div class="text formatted">
      {#if complete && !working && !streaming && state === "ready" && role === "answer" && replySummary(content)}<DocumentEditCard
          text={content}
          answer
        />{:else}<RichText
          source={content}
          {turnId}
          complete={complete &&
            !working &&
            !streaming &&
            state === "ready" &&
            role === "answer"}
        />{/if}
    </div>
  {/if}
  {#if !complete && !streaming}
    <p class="extent">
      {content
        ? `${end} of ${total} UTF-8 bytes shown`
        : "No part of this body has been read yet."}
    </p>
    <button type="button" class="more" disabled={loading} on:click={onMore}
      >{content ? "More body text" : "Load body text"}</button
    >
  {/if}
</article>

<style>
  .image-pending {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-tertiary);
  }
  .body {
    min-width: 0;
    margin: 0;
  }
  .role {
    margin: 0 0 var(--ke-space-1);
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .text {
    /* Exact text: preserve the stored bytes' own whitespace and line breaks. */
    min-width: 0;
    color: var(--ke-text-primary);
    /* Actual old AnswerCard reply typography, including its final override. */
    font-size: var(--ke-chat-answer-size, 0.875rem);
    line-height: var(--ke-chat-line-height, 1.7);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    user-select: text;
  }
  /* Old user treatment: larger, medium weight, no frame around it. */
  .user .text {
    font-size: var(--ke-text-lg);
    font-weight: 500;
    line-height: 1.6;
  }
  .answer-tools {
    min-height: 2rem;
    display: flex;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .formatted {
    margin-inline-start: var(--ke-chat-answer-inset, 0.5rem);
    white-space: normal;
  }
  .answer-copy {
    margin-left: auto;
    flex: 0 0 auto;
  }
  .formatted :global(p) {
    margin: 0 0 0.85em;
  }
  .formatted :global(p:last-child) {
    margin-bottom: 0;
  }
  .formatted :global(h1),
  .formatted :global(h2),
  .formatted :global(h3),
  .formatted :global(h4) {
    margin: 1.2em 0 0.5em;
    line-height: 1.4;
    font-size: 1.08em;
    font-weight: 600;
  }
  .formatted :global(ul),
  .formatted :global(ol) {
    padding-left: 1.6em;
    margin: 0.6em 0;
  }
  .formatted :global(li) {
    margin: 0.2em 0;
  }
  .formatted :global(blockquote) {
    border-left: 2px solid var(--ke-border-strong);
    margin: 0.8em 0;
    padding-left: 1em;
    color: var(--ke-text-secondary);
  }
  .formatted :global(a) {
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .formatted :global(code) {
    font-family: var(--ke-font-mono);
    font-size: 0.88em;
  }
  .formatted :global(table) {
    display: block;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    margin: 0.8em 0;
  }
  .formatted :global(th),
  .formatted :global(td) {
    padding: 0.45em 0.8em;
    border: 1px solid var(--ke-border);
    text-align: left;
  }
  .formatted :global(hr) {
    border: 0;
    border-top: 1px solid var(--ke-border);
    margin: 1em 0;
  }
  .extent {
    margin: var(--ke-space-2) 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-xs);
  }
  .more {
    padding: var(--ke-space-1) var(--ke-space-3);
    border: 1px solid var(--ke-border-strong);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    color: var(--ke-text-primary);
    font-size: var(--ke-text-xs);
    cursor: pointer;
  }
  .more:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
