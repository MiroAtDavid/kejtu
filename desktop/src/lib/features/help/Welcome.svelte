<script lang="ts">
  export let active = false;
  export let busy = false;
  export let hasDraft = false;
  export let inConversation = true;
  export let onHelp: () => void = () => {};
  export let onStart: () => void = () => {};
  export let onContinue: () => void = () => {};
  export let onComplete: () => void = () => {};
  export let onSkip: () => void = () => {};
</script>

<aside aria-label="Getting started" class:active class="welcome">
  <div>
    <h2>{active ? "Finding your way" : "Make yourself at home."}</h2>
    <p>
      {active
        ? "A short introduction, at your pace. Finish whenever you feel ready; your conversation stays."
        : "Connect a model in Settings, then let Kejtu help with one small task. You can start with the guide, too."}
    </p>
  </div>
  <div class="actions">
    {#if active}
      {#if !inConversation}<button
          type="button"
          disabled={busy}
          on:click={onContinue}>Continue introduction</button
        >{/if}
      <button type="button" disabled={busy} on:click={onComplete}
        >I’m ready</button
      >
    {:else}
      <button type="button" disabled={busy || hasDraft} on:click={onStart}
        >Get started with Kejtu</button
      >
    {/if}
    <button type="button" disabled={busy} on:click={onHelp}
      >Read the guide</button
    >
    <button class="quiet" type="button" disabled={busy} on:click={onSkip}
      >Skip introduction</button
    >
  </div>
  {#if hasDraft && !active}<p class="draft-hint">
      Send or save your message before starting the introduction.
    </p>{/if}
</aside>

<style>
  .welcome {
    border: 0;
    border-radius: 14px;
    padding: 1.4rem 1.5rem;
    margin: 1rem auto 2rem;
    max-width: 48rem;
    background: var(--ke-surface-inset);
  }
  .welcome.active {
    padding: 1rem 1.25rem;
  }
  h2 {
    font-size: 19px;
    font-weight: 500;
    margin: 0 0 0.4rem;
  }
  .active h2 {
    font-size: 15px;
  }
  p {
    margin: 0;
    color: var(--ke-text-secondary);
    line-height: 1.6;
    font-size: 13px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.9rem;
  }
  button {
    font: inherit;
    font-size: 12px;
    color: var(--ke-text-primary);
    border: 0;
    background: var(--ke-surface-raised);
    border-radius: 7px;
    padding: 0.5rem 0.75rem;
    cursor: pointer;
  }
  button.quiet {
    background: transparent;
    color: var(--ke-text-secondary);
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .draft-hint {
    margin-top: 0.6rem;
    font-size: 12px;
  }
</style>
