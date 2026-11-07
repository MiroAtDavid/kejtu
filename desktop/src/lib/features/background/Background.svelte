<script lang="ts">
  import Care from "./Care.svelte";
  import Reflection from "./Reflection.svelte";
  import Jobs from "./Jobs.svelte";
  import type { Profile } from "$lib/api/wire.generated";
  export let stateId: string;
  export let conversationId = "";
  export let profile: Profile;
  export let busy = false;
  export let navigationLocked = false;
  let selected = "tidy";
  let visited = new Set([selected]);
  let careLock = false,
    reflectionLock = false,
    jobsLock = false;
  $: locked = careLock || reflectionLock || jobsLock;
  $: navigationLocked = locked;
  function choose(value: string) {
    if (locked) return;
    selected = value;
    visited = new Set([...visited, value]);
  }
</script>

<section aria-label="Background work" class="background settings-page">
  <!-- Keep the shared heading and tabs in place for every activity. -->
  <header class="settings-page">
    <h2 class="settings-title">Background work</h2>
    <p class="intro">
      Quiet work while Kejtu is open and idle. Each activity has its own
      settings and results.
    </p>
    <nav class="settings-tabs" aria-label="Background activities">
      <button
        class:selected={selected === "tidy"}
        aria-pressed={selected === "tidy"}
        disabled={locked}
        on:click={() => choose("tidy")}>Notebook tidy</button
      >
      <button
        class:selected={selected === "reflection"}
        aria-pressed={selected === "reflection"}
        disabled={locked}
        on:click={() => choose("reflection")}>Reflection</button
      >
      <button
        class:selected={selected === "advanced"}
        aria-pressed={selected === "advanced"}
        disabled={locked}
        on:click={() => choose("advanced")}>Advanced</button
      >
    </nav>
  </header>
  <p class="hint">
    Close Settings and leave the message box empty to allow background work.
    Chat always takes priority.
  </p>
  <!-- Retain mounted controllers: hiding a tab cannot discard an uncertain save. -->
  {#if visited.has("tidy")}<div hidden={selected !== "tidy"}>
      <Care {stateId} {profile} {busy} bind:navigationLocked={careLock} />
    </div>{/if}
  {#if visited.has("reflection")}<div hidden={selected !== "reflection"}>
      <Reflection
        {stateId}
        {profile}
        {busy}
        bind:navigationLocked={reflectionLock}
      />
    </div>{/if}
  {#if visited.has("advanced")}<div hidden={selected !== "advanced"}>
      <Jobs
        {stateId}
        {conversationId}
        {profile}
        {busy}
        bind:navigationLocked={jobsLock}
      />
    </div>{/if}
</section>

<style>
  .intro,
  .hint {
    color: var(--ke-text-secondary);
  }
  .hint {
    font-size: var(--ke-text-xs);
    max-width: 68ch;
    line-height: 1.65;
    margin: 1.5rem 0;
  }
  [hidden] {
    display: none;
  }
</style>
