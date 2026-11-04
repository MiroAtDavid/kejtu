<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import type { PreferenceSettings } from "$lib/api/wire.generated";
  import { choiceSelect } from "$lib/design/choice-select";

  export let busy = false;
  export let locked = false;
  let saved: PreferenceSettings | null = null;
  let strength: PreferenceSettings["strength"] = "subtle";
  let loading = true;
  let uncertain = false;
  let error = "";
  let notice = "";
  let destroyed = false;
  $: dirty = saved !== null && strength !== saved.strength;
  $: locked = loading || uncertain || dirty;

  async function load() {
    loading = true;
    error = "";
    try {
      const value = await request("preferences.read", {});
      if (destroyed) return;
      saved = value;
      strength = value.strength;
      if (uncertain) notice = "Saved setting checked. Nothing was retried.";
      uncertain = false;
    } catch (caught) {
      if (!destroyed) error = String(caught);
    } finally {
      if (!destroyed) loading = false;
    }
  }
  async function save() {
    if (!saved || !dirty || loading || busy || uncertain) return;
    loading = true;
    error = "";
    notice = "";
    try {
      const value = await request("preferences.save", {
        strength,
        expected_revision: saved.revision,
      });
      if (destroyed) return;
      saved = value;
      strength = value.strength;
      notice = "Preference recall updated for future replies.";
    } catch (caught) {
      if (!destroyed) {
        uncertain = true;
        error = `${String(caught)} Reload the saved setting before trying again.`;
      }
    } finally {
      if (!destroyed) loading = false;
    }
  }
  onMount(() => {
    void load();
    return () => {
      destroyed = true;
    };
  });
</script>

<section aria-label="Preference recall" class="preference-recall">
  <h3>Preference recall</h3>
  <p class="muted">
    Bring in saved preferences when they fit your request. Subtle favours
    missing uncertain matches.
  </p>
  <div class="preference-controls">
    <label>
      Recall sensitivity
      <select
        use:choiceSelect={strength}
        bind:value={strength}
        disabled={loading || busy || uncertain || !saved}
      >
        <option value="off">Off</option>
        <option value="subtle">Subtle</option>
        <option value="balanced">Balanced</option>
        <option value="broader">Broader</option>
      </select>
    </label>
    <button disabled={!dirty || loading || busy || uncertain} on:click={save}
      >Save preference recall</button
    >
    {#if dirty && !uncertain}
      <button
        disabled={loading}
        on:click={() => {
          if (saved) strength = saved.strength;
        }}>Discard change</button
      >
    {/if}
    {#if error || uncertain}<button disabled={loading || busy} on:click={load}
        >Reload saved setting</button
      >{/if}
  </div>
  <p class="muted">
    Applies across conversations to notes with an “Applies when” scope. Ordinary
    memory and explicit searches stay available, including when this is off.
    Your current request takes precedence.
  </p>
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
</section>

<style>
  .preference-recall {
    margin: 24px 0;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--ke-border);
  }
  h3 {
    font-size: 14px;
    font-weight: 500;
  }
  p {
    font-size: 12px;
    line-height: 1.6;
  }
  .muted {
    color: var(--ke-text-secondary);
  }
  .preference-controls {
    display: flex;
    gap: 12px;
    align-items: end;
    flex-wrap: wrap;
  }
  label {
    margin: 0;
    display: grid;
    gap: 8px;
    font-size: 12px;
    min-width: 190px;
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
</style>
