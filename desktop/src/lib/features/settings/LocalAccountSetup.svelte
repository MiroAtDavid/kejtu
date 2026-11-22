<script lang="ts">
  import { onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type { McpInput } from "$lib/api/wire.generated";

  export let server: string;
  export let revision: number;
  export let fields: McpInput[];
  export let disabled = false;
  export let busy = false;
  export let onChanged: () => Promise<void> = async () => {};
  export let onConnected: () => Promise<void> = async () => {};
  let values: Record<string, string> = {};
  let error = "";
  let mounted = true;
  $: complete = fields.every((field) => !!values[field.name]);
  onDestroy(() => {
    mounted = false;
    values = {};
  });

  async function save() {
    if (busy || disabled || !complete) return;
    busy = true;
    error = "";
    let saved = false;
    try {
      await request("mcp.update", {
        action: "credentials",
        server,
        expected_revision: revision,
        values: fields.map((field) => ({
          name: field.name,
          value: values[field.name],
        })),
      });
      saved = true;
    } catch (caught) {
      if (mounted)
        error =
          caught instanceof Error
            ? caught.message
            : "Account details could not be saved. Check the current setup before trying again.";
    } finally {
      values = {};
      busy = false;
      // Read back every outcome. Never resubmit an uncertain credential change.
      if (mounted) await onChanged();
      if (saved && mounted) await onConnected();
    }
  }
</script>

<form
  aria-label="Package account details"
  on:submit|preventDefault={save}
  autocomplete="off"
>
  <p class="hint">
    Saved in this computer’s keyring. After restoring elsewhere, enter these
    details again.
  </p>
  {#each fields as field}
    <label>
      <span>{field.label}</span>
      <input
        type={field.secret ? "password" : "text"}
        bind:value={values[field.name]}
        disabled={busy || disabled}
        required
        maxlength="8192"
        autocomplete="off"
        spellcheck="false"
      />
    </label>
  {/each}
  {#if error}<p role="alert">{error}</p>{/if}
  <button type="submit" disabled={busy || disabled || !complete}
    >{busy ? "Saving…" : "Save & enable"}</button
  >
</form>

<style>
  form {
    display: grid;
    gap: 1rem;
    max-width: 34rem;
  }
  label {
    display: grid;
    gap: 0.5rem;
  }
  .hint {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
  input,
  button {
    font: inherit;
    color: inherit;
    background: var(--ke-surface);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    padding: 8px 12px;
  }
  input {
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
  }
  button {
    justify-self: start;
    cursor: pointer;
  }
  button:disabled,
  input:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
