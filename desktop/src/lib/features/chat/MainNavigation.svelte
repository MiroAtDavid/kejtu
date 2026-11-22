<script lang="ts">
  import Icon from "$lib/design/Icon.svelte";
  import type { View } from "../workspace/workspace";
  export let view: View = "journal";
  export let settingsOpen = false;
  export let disabled = false;
  export let reminderWaiting = false;
  export let onOpen: (view: View) => void = () => {};
</script>

<nav aria-label="Main navigation">
  <button
    type="button"
    class="sidebar-menu-row"
    class:current={!settingsOpen && view === "home"}
    aria-current={!settingsOpen && view === "home" ? "page" : undefined}
    {disabled}
    aria-label={reminderWaiting ? "Home, reminders waiting" : "Home"}
    on:click={() => onOpen("home")}
    ><Icon name="home" size={15} />Home{#if reminderWaiting}<span
        class="reminder-dot"
        aria-hidden="true"
      ></span>{/if}</button
  >
</nav>

<style>
  .reminder-dot {
    display: inline-block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    margin-left: 7px;
    vertical-align: middle;
    background: var(--ke-accent);
  }
</style>
