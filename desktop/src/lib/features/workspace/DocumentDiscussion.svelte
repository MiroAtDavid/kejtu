<script lang="ts">
  import type { Readable } from "svelte/store";
  import type {
    DocumentDiscussionState,
    DocumentDiscussionActions,
  } from "./document-discussion";
  import Icon from "$lib/design/Icon.svelte";
  import RichText from "$lib/features/chat/RichText.svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import ReaderNotice from "./ReaderNotice.svelte";
  import { proposalText } from "./document-assist";
  import { outcomeLabel } from "./document-conversation";
  export let model: Readable<DocumentDiscussionState | null>;
  export let actions: DocumentDiscussionActions;
</script>

{#if $model}
  <div class="assistant-bar">
    <div class="assist-context">
      <span
        class="focus-hint"
        title="Optional focus hint. Kejtu can locate passages from your instruction."
        >{$model.focus}</span
      >
      {#if $model.hasFocus}<button
          class="clear-focus"
          aria-label="Clear document focus"
          title="Use the document without a focus hint"
          disabled={$model.asking}
          on:click={actions.wholeDocument}
          ><Icon name="close" size={12} /></button
        >{/if}
      {#if $model.sourceName}<ChoicePicker
          id={`document-edit-source-${$model.sessionId}`}
          label="Reference source"
          hideLabel
          labelOnly
          value={$model.usePdf ? "pdf" : "none"}
          disabled={$model.asking}
          options={[
            { value: "pdf", label: $model.sourceName },
            { value: "none", label: "No reference source" },
          ]}
          onChange={(value) => actions.source(value === "pdf")}
        />{/if}
      {#if $model.visibleExchanges.length || $model.exchanges.length || $model.canEarlier}<button
          class="edit-details"
          title="Document conversation"
          aria-label="Document conversation"
          aria-expanded={$model.expanded}
          on:click={actions.toggleHistory}>···</button
        >{/if}
    </div>
    {#if $model.expanded}<div
        class="edit-conversation"
        aria-label="Document editing conversation"
      >
        <div class="history-heading">In this Journal conversation</div>
        {#if $model.canEarlier}<button
            disabled={$model.loading}
            on:click={() => actions.moreHistory()}>Earlier exchanges</button
          >{/if}
        {#if $model.hasHistory}
          {#each $model.visibleExchanges as exchange (exchange.turnId)}
            <div class="document-exchange">
              <p class="instruction">{exchange.request.instruction}</p>
              <div class="exchange-reply">
                <RichText
                  source={exchange.reply?.explanation || exchange.detail}
                  complete
                />
              </div>
              <small>{outcomeLabel(exchange)}</small>
              {#if !exchange.complete && exchange.status === "completed"}<button
                  on:click={() => actions.moreHistory(exchange.turnId)}
                  >Load answer</button
                >{/if}
              {#if exchange.reply?.action === "edit"}<details>
                  <summary>Proposed change</summary>
                  <pre>{proposalText(exchange.reply)}</pre>
                </details>{/if}
            </div>
          {/each}
          {#if !$model.visibleExchanges.length}<p>
              No loaded exchanges for this document.
            </p>{/if}
        {:else}
          {#each $model.exchanges as exchange}<p class="instruction">
              {exchange.instruction}
            </p>
            <p>{exchange.reply}</p>{/each}
        {/if}
      </div>{/if}
    {#if $model.proposal}<details class="proposal" open>
        <summary>Proposed Markdown</summary>
        <pre>{proposalText($model.proposal)}</pre>
        <p>{$model.proposal.explanation}</p>
        <button on:click={actions.dismissProposal}>Dismiss proposal</button>
      </details>{/if}
    {#if $model.assistError}<ReaderNotice
        error
        label="Dismiss editing message"
        onDismiss={actions.dismissError}>{$model.assistError}</ReaderNotice
      >{/if}
    {#if ($model.answer || $model.liveAnswer) && !$model.expanded}<div
        class="document-answer"
        role="status"
      >
        <RichText
          source={$model.answer || $model.liveAnswer}
          complete={!$model.asking}
        />
      </div>{/if}
    {#if $model.result}<div class="edit-result" role="status">
        <span>{$model.result}</span
        >{#if $model.result.startsWith("Applied")}<button
            disabled={!$model.canUndo}
            title={$model.undoBlocked
              ? "Use the document toolbar to undo later changes first"
              : "Undo this edit"}
            on:click={actions.undo}>Undo</button
          >{/if}<button
          aria-label="Dismiss edit result"
          on:click={actions.dismissResult}
          ><Icon name="close" size={12} /></button
        >
      </div>{/if}
    <div class="instruction-row">
      <textarea
        aria-label="Ask Kejtu"
        placeholder="Ask a question or describe a change…"
        value={$model.instruction}
        on:input={(event) => actions.input(event.currentTarget.value)}
        rows="4"
        on:keydown={actions.instructionKeys}
        disabled={!$model.ready}
      ></textarea>
      {#if $model.asking}<button
          class="submit"
          aria-label="Stop document edit"
          disabled={$model.stopped}
          on:click={actions.stop}><Icon name="stop" size={15} /></button
        >
      {:else}<button
          class="submit"
          aria-label="Send document instruction"
          title="Send · Ctrl/⌘ Enter"
          disabled={!$model.ready ||
            !$model.available ||
            !$model.instruction.trim()}
          on:click={actions.ask}><Icon name="arrow-up" size={15} /></button
        >{/if}
    </div>
    <div class="assist-foot">
      <span>{$model.phase}</span>
      {#if $model.pending && !$model.asking}<button on:click={actions.resolve}
          >Check pending request</button
        >{/if}
    </div>
  </div>
{/if}

<style>
  .document-answer {
    overflow: auto;
    max-height: min(180px, 30%);
    min-height: 24px;
    padding: 4px 0 8px;
    flex-shrink: 1;
    font-size: 13px;
    line-height: 1.55;
  }
  .document-answer :global(p) {
    margin: 0;
  }
  .document-exchange {
    border-bottom: 1px solid var(--ke-border);
    padding: 8px 0;
  }
  .document-exchange small,
  .history-heading {
    color: var(--ke-text-tertiary);
    font-size: 10px;
  }
  .document-exchange pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 200px;
    overflow: auto;
  }
  .exchange-reply :global(p) {
    margin: 6px 0;
  }

  .assistant-bar {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    padding: 0;
    color: var(--ke-text-secondary);
    font-size: 11px;
  }
  .assist-context,
  .instruction-row,
  .assist-foot,
  .edit-result {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .assist-context {
    justify-content: flex-start;
    flex-wrap: nowrap;
    padding-bottom: 10px;
  }
  .assistant-bar button {
    border: 0;
    background: transparent;
    color: var(--ke-text-secondary);
    padding: 4px 6px;
    border-radius: var(--ke-radius-sm);
    font: inherit;
    cursor: pointer;
  }
  .assistant-bar button:hover {
    background: var(--ke-surface-inset);
  }
  .assistant-bar button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .assistant-bar button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 1px;
  }
  .instruction-row {
    flex: 1;
    min-height: 64px;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-raised);
    padding: 5px 7px;
  }
  .instruction-row:focus-within {
    border-color: var(--ke-border-strong);
    box-shadow: 0 0 0 1px var(--ke-border);
  }
  .instruction-row textarea:focus {
    outline: none;
  }
  .instruction-row textarea {
    flex: 1;
    width: 100%;
    min-width: 0;
    min-height: 48px;
    height: 100%;
    box-sizing: border-box;
    resize: none;
    padding: 4px;
    border: 0;
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    font-size: 12px;
    line-height: 1.5;
  }
  .instruction-row .submit {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    flex: 0 0 28px;
    align-self: flex-end;
    padding: 0;
  }
  .assist-foot {
    justify-content: space-between;
    padding-top: 5px;
    color: var(--ke-text-tertiary);
    font-size: 10px;
  }
  .edit-conversation,
  .proposal {
    max-height: 180px;
    overflow: auto;
    scrollbar-width: thin;
    padding: 5px 0;
    overflow-wrap: anywhere;
  }
  .edit-conversation p {
    margin: 5px 0;
    white-space: pre-wrap;
  }
  .edit-conversation .instruction {
    color: var(--ke-text-primary);
    font-weight: 500;
  }
  .proposal pre {
    white-space: pre-wrap;
    user-select: text;
  }
  .edit-result {
    padding-bottom: 5px;
  }
  .focus-hint {
    flex: none;
    color: var(--ke-text-secondary);
  }
  .edit-details {
    margin-left: auto;
    flex: none;
  }
  .assist-context :global(.choice-field) {
    margin: 0;
    min-width: 0;
  }
  .assist-context :global(.choice-trigger) {
    font-size: 11px;
    min-height: 28px;
  }
</style>
