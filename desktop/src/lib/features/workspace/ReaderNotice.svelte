<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  export let label = "Dismiss message";
  export let error = false;
  export let onDismiss: () => void;
</script>

<div class="reader-message" class:error role={error ? "alert" : "status"}>
  <span><slot /></span>
  <button type="button" aria-label={label} title={label} on:click={onDismiss}>
    <Icon name="close" size={13} />
  </button>
</div>

<style>
  .reader-message {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 2px 0 8px;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
    line-height: 1.5;
  }
  .reader-message.error {
    color: var(--ke-danger);
  }
  span {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  button {
    display: grid;
    place-items: center;
    flex: none;
    width: 26px;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-tertiary);
  }
  button:hover {
    background: var(--ke-surface-inset);
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
</style>
