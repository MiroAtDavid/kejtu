<script lang="ts">
  import { onDestroy } from "svelte";
  import ChatAvatar from "$lib/design/ChatAvatar.svelte";
  import {
    userAvatar,
    readUserThumbnail,
    saveUserAvatar,
  } from "$lib/design/user-avatar";

  let picker: HTMLInputElement;
  let busy = false;
  let error = "";
  let version = 0;
  onDestroy(() => version++);

  async function choose() {
    const file = picker.files?.[0];
    if (!file || busy) return;
    const selected = ++version;
    busy = true;
    error = "";
    try {
      const source = await readUserThumbnail(file);
      // An unmounted editor must not save a selection that finishes later.
      if (selected === version) saveUserAvatar(source);
    } catch (cause) {
      if (selected === version)
        error =
          cause instanceof Error
            ? cause.message
            : "This picture could not be saved.";
    } finally {
      if (selected === version) {
        busy = false;
        picker.value = "";
      }
    }
  }

  function remove() {
    error = "";
    try {
      saveUserAvatar(null);
    } catch (cause) {
      error =
        cause instanceof Error
          ? cause.message
          : "This picture could not be removed.";
    }
  }
</script>

<div class="thumbnail-controls">
  <div class="preview">
    {#if $userAvatar}<ChatAvatar role="user" />{:else}<span>No picture</span
      >{/if}
  </div>
  <input
    bind:this={picker}
    type="file"
    accept="image/png,image/jpeg,image/webp"
    aria-label="Choose your thumbnail"
    on:change={choose}
    disabled={busy}
    hidden
  />
  <button type="button" on:click={() => picker.click()} disabled={busy}
    >{busy
      ? "Opening…"
      : $userAvatar
        ? "Change picture"
        : "Upload picture"}</button
  >
  {#if $userAvatar}<button type="button" on:click={remove} disabled={busy}
      >Remove picture</button
    >{/if}
</div>
<p class="hint">
  PNG, JPEG or WebP, up to 8 MB. Saved on this computer as a small square
  thumbnail.
</p>
{#if error}<p class="error" role="alert">{error}</p>{/if}

<style>
  .thumbnail-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .preview {
    min-width: 70px;
    min-height: 36px;
    display: flex;
    align-items: center;
    color: var(--ke-text-secondary);
    font-size: 12px;
  }
  button {
    border: 1px solid var(--ke-border);
    border-radius: 6px;
    background: none;
    color: var(--ke-text-secondary);
    padding: 8px 12px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: var(--ke-surface-inset);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .hint,
  .error {
    font-size: 12px;
    line-height: 1.7;
  }
  .hint {
    color: var(--ke-text-secondary);
  }
  .error {
    color: var(--ke-danger);
  }
</style>
