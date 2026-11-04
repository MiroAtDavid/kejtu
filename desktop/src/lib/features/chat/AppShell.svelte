<script lang="ts">
  import "./shell.css";
  import { NATIVE_READINESS_MARKER } from "$lib/api/client";
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import { pageFade } from "$lib/design/page-fade";
  import Icon from "$lib/design/Icon.svelte";
  import { tick } from "svelte";
  import MainNavigation from "./MainNavigation.svelte";
  import type { View } from "../workspace/workspace";
  import WindowControls from "./WindowControls.svelte";

  export let quiet = false;
  export let expanded = false;
  export let reading = false;
  export let wideReading = false;
  let wasReading = false;
  $: if (reading && !wasReading && scrollElement) {
    scrollElement.scrollTop = 0;
    wasReading = true;
  }
  $: if (!reading) wasReading = false;
  export let courseScreen = false;
  export let compact = false;
  export let view: View = "journal";
  export let navigationLocked = false;
  export let onOpen: (view: View) => void = () => {};
  export let marginOpen = false;
  export let onToggleWidgets: () => void = () => {};
  export let indexOpen = false;
  export let settingsOpen = false;
  export let settingsLocked = false;
  export let scrollElement: HTMLElement | undefined = undefined;
  export let onToggleIndex: () => void = () => {};
  export let onToggleSettings: () => void = () => {};
  export let onReadingFocus: (element: HTMLElement) => void = () => {};
  let widgets: HTMLElement;
  let sidebar: HTMLElement;
  let widgetTrigger: HTMLButtonElement;
  let navTrigger: HTMLButtonElement;
  // A panel already visible at startup must not steal the composer’s focus.
  let previouslyOpen = marginOpen;
  let previouslyIndexOpen = false;
  $: navigationOpen = !wideReading && (compact ? indexOpen : !quiet);
  $: if (marginOpen !== previouslyOpen) {
    previouslyOpen = marginOpen;
    if (marginOpen)
      void tick().then(() =>
        widgets?.querySelector<HTMLButtonElement>(".panel-close")?.focus(),
      );
    else if (widgets?.contains(document.activeElement)) widgetTrigger?.focus();
  }
  $: if (indexOpen !== previouslyIndexOpen) {
    previouslyIndexOpen = indexOpen;
    if (indexOpen && compact)
      void tick().then(() =>
        sidebar?.querySelector<HTMLButtonElement>(".nav-close")?.focus(),
      );
    else if (compact && sidebar?.contains(document.activeElement))
      navTrigger?.focus();
  }
  function panelKey(event: KeyboardEvent) {
    // Editors, source popovers and other detail drawers own Escape first.
    if (
      event.key !== "Escape" ||
      event.defaultPrevented ||
      document.querySelector('dialog[open], [role="dialog"]')
    )
      return;
    if (marginOpen && !wideReading) {
      event.preventDefault();
      onToggleWidgets();
      widgetTrigger?.focus();
    } else if (compact && indexOpen && !wideReading) {
      event.preventDefault();
      onToggleIndex();
      navTrigger?.focus();
    }
  }
</script>

<svelte:window on:keydown={panelKey} />
<div class="shell" data-native-readiness={NATIVE_READINESS_MARKER}>
  <header class="topbar" data-tauri-drag-region>
    <div class="topbar-left" data-tauri-drag-region>
      {#if !settingsOpen}<button
          bind:this={navTrigger}
          class="icon-action index-toggle"
          type="button"
          aria-label="Toggle sidebar"
          title={wideReading
            ? "Exit wide view to show the sidebar"
            : "Toggle sidebar"}
          disabled={wideReading}
          aria-expanded={navigationOpen}
          aria-controls="journal-index"
          on:click={onToggleIndex}><Icon name="sidebar" /></button
        >{/if}
      <MainNavigation
        {view}
        {settingsOpen}
        disabled={navigationLocked}
        {onOpen}
      />
    </div>
    <div class="topbar-actions">
      <div class="journal-actions" role="group" aria-label="Workspace actions">
        {#if !settingsOpen}<button
            bind:this={widgetTrigger}
            class="journal-mode widgets-toggle"
            type="button"
            aria-expanded={marginOpen && !wideReading}
            disabled={wideReading}
            aria-controls="journal-widgets"
            on:click={onToggleWidgets}
            title={wideReading ? "Exit wide view to show widgets" : "Widgets"}
            ><Icon name="widgets" size={14} /><span>Widgets</span></button
          >{/if}
        <button
          type="button"
          class="journal-mode"
          title={settingsOpen ? "Close settings" : "Settings"}
          aria-pressed={settingsOpen}
          disabled={settingsLocked}
          on:click={onToggleSettings}>Settings</button
        >
      </div>
      <WindowControls />
    </div>
  </header>

  <!-- Preserve the transcript's existing scroll owner and mounted controllers.
       Panels only change visibility; opening Settings never recreates a draft. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <main
    class="workspace"
    data-reading={reading}
    bind:this={scrollElement}
    use:quietScrollbar
    hidden={settingsOpen}
    role="region"
    aria-label={view === "home"
      ? "Home reading area"
      : expanded
        ? "Personal workspace"
        : "Conversation reading area"}
    tabindex="0"
    on:focusin={(event) => {
      if (event.target instanceof HTMLElement) onReadingFocus(event.target);
    }}
  >
    <div
      class="journal-grid"
      data-quiet={quiet || wideReading}
      data-wide-reading={wideReading}
      data-index-open={indexOpen && !wideReading}
      data-expanded={expanded}
      data-margin-open={marginOpen && !wideReading}
      data-reading={reading}
      data-course={courseScreen}
    >
      {#if compact && indexOpen && !wideReading}<button
          class="nav-scrim"
          aria-label="Close sidebar"
          on:click={onToggleIndex}
        ></button>{/if}
      <aside
        bind:this={sidebar}
        id="journal-index"
        class="column column-left"
        aria-label="Workspace navigation"
        use:quietScrollbar
      >
        {#if compact}<button
            class="icon-action nav-close"
            aria-label="Close sidebar"
            on:click={onToggleIndex}><Icon name="close" /></button
          >{/if}
        <slot name="left" />
      </aside>
      <div
        class="column column-middle"
        use:pageFade={settingsOpen ? null : view}
      >
        <slot />
      </div>
      <aside
        bind:this={widgets}
        id="journal-widgets"
        class="column column-right"
        aria-label="Widgets"
        hidden={!marginOpen || wideReading}
      >
        <div class="widget-panel-heading">
          <span>Your widgets</span><button
            class="icon-action panel-close"
            type="button"
            aria-label="Close widgets"
            on:click={onToggleWidgets}><Icon name="close" size={14} /></button
          >
        </div>
        <slot name="right" />
      </aside>
    </div>
  </main>
  {#if !settingsOpen}<div class="shell-footer"><slot name="footer" /></div>{/if}
  <section class="surface" hidden={!settingsOpen} aria-label="Settings">
    <slot name="surface" />
  </section>
</div>
