<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import CopyButton from "./CopyButton.svelte";
  import type { StudyReference, StudyRequest } from "./study-requests";
  export let request: StudyRequest;
  export let disabled = false;
  export let onOpen:
    ((reference: StudyReference) => Promise<void>) | undefined = undefined;
  export let onRemove: (() => void) | undefined = undefined;
  export let onEdit: (() => void) | undefined = undefined;
  /** Where in the source, instead of the page (a Markdown passage has no
   *  page); "" shows none. */
  export let place: string | undefined = undefined;
  /** "Full request" with the raw text; a mark not yet sent has none. */
  export let details = true;
  let opening = false,
    error = "";
  $: file = request.reference.path.split(/[\\/]/).at(-1) || "Source material";
  $: where =
    place ??
    (request.reference.page ? `Page ${request.reference.page}` : "Text source");
  async function open() {
    if (!onOpen || disabled || opening) return;
    opening = true;
    error = "";
    try {
      await onOpen(request.reference);
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      opening = false;
    }
  }
</script>

<div
  class="study-request"
  role="group"
  aria-label={`${request.label}: ${file}`}
>
  <div class="request-heading">
    <Icon name="file" size={16} />
    <div class="request-labels">
      <span class="request-intent">{request.label}</span>
      {#if onOpen}<button
          type="button"
          class="source-link"
          disabled={disabled || opening}
          aria-label={`Open ${file}${request.reference.page ? `, page ${request.reference.page}` : ""}`}
          title={request.reference.path}
          on:click={open}
        >
          {opening ? "Opening source…" : file}{#if where}<span class="page"
              >{where}</span
            >{/if}<Icon name="arrow" size={13} />
        </button>
      {:else}<span class="source-label"
          >{file}{#if where}<span class="page">{where}</span>{/if}</span
        >{/if}
    </div>
    {#if onRemove}<button
        type="button"
        class="remove"
        aria-label={`Remove ${request.label} request`}
        title="Remove request"
        {disabled}
        on:click={onRemove}><Icon name="close" size={13} /></button
      >{/if}
  </div>
  {#if request.passage}<blockquote class="passage">
      {request.passage}
    </blockquote>{/if}
  {#if details}<details>
      <summary>Full request</summary>
      <div class="request-details">
        <div class="details-actions">
          {#if onEdit}<button type="button" {disabled} on:click={onEdit}
              >Edit full request</button
            >{/if}
          <CopyButton text={request.raw} label="Copy full request" />
        </div>
        <pre>{request.raw}</pre>
      </div>
    </details>{/if}
  {#if error}<p class="source-error" role="alert">{error}</p>{/if}
</div>

<style>
  .study-request {
    display: block;
    max-width: 540px;
    margin: 9px 0;
    padding: 12px 14px;
    border-radius: var(--ke-radius-sm);
    background: var(--ke-accent-soft);
    color: var(--ke-text-primary);
    white-space: normal;
    font: 12px/1.6 var(--ke-font-ui);
  }
  .passage {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    overflow: hidden;
    margin: 6px 0 0 26px;
    padding-left: 9px;
    border-left: 3px solid var(--ke-marker-blue);
    color: var(--ke-text-secondary);
    overflow-wrap: anywhere;
  }
  .request-heading {
    display: flex;
    align-items: start;
    gap: 10px;
  }
  .request-heading > :global(svg) {
    margin-top: 3px;
    color: var(--ke-text-secondary);
  }
  .request-labels {
    flex: 1;
    min-width: 0;
  }
  .request-intent {
    display: block;
    font-weight: 500;
    margin-bottom: 2px;
  }
  .source-link,
  .source-label {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 10px;
    overflow-wrap: anywhere;
    color: var(--ke-text-secondary);
  }
  button {
    font: inherit;
    border: 0;
    padding: 0;
    background: none;
    color: inherit;
    cursor: pointer;
    text-align: left;
  }
  .source-link:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .page {
    font-size: var(--ke-text-2xs);
    color: var(--ke-text-tertiary);
    white-space: nowrap;
  }
  .remove {
    padding: 2px;
    color: var(--ke-text-secondary);
  }
  details {
    margin: 5px 0 0 26px;
  }
  summary {
    cursor: pointer;
    font-size: var(--ke-text-3xs);
    color: var(--ke-text-tertiary);
  }
  .details-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 14px;
    font-size: var(--ke-text-2xs);
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 230px;
    overflow: auto;
    font: 11px/1.7 var(--ke-font-mono);
    margin: 6px 0 0;
  }
  .source-error {
    color: var(--ke-danger);
    font-size: var(--ke-text-2xs);
    margin: 8px 0 0;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
</style>
