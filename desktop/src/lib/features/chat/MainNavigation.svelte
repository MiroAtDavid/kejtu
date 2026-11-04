<script lang="ts">
  import type { View } from "../workspace/workspace";
  export let view: View = "journal";
  export let settingsOpen = false;
  export let disabled = false;
  export let onOpen: (view: View) => void = () => {};
</script>

<nav class="destination-nav" aria-label="Main navigation">
  <button
    type="button"
    aria-current={!settingsOpen && view === "home" ? "page" : undefined}
    {disabled}
    on:click={() => onOpen("home")}>Home</button
  >
</nav>
