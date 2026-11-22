<script lang="ts">
  import "./shell.css";
  import { NATIVE_READINESS_MARKER } from "$lib/api/client";
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import { pageFade } from "$lib/design/page-fade";
  import { navIndicator } from "$lib/design/nav-indicator";
  import Icon from "$lib/design/Icon.svelte";
  import { tick, onDestroy } from "svelte";
  import type { View } from "../workspace/workspace";
  import WindowControls from "./WindowControls.svelte";

  import {
    documentDockWidth,
    type DocumentDockBounds,
  } from "../workspace/document-dock";
  export let documentOpen = false;
  export let documentTitle = "";
  export let onCloseDocument: () => void = () => {};
  export let onDocumentBounds: (
    bounds: DocumentDockBounds | null,
  ) => void = () => {};
  let grid: HTMLDivElement;
  let dockBody: HTMLDivElement;
  let documentWidth = 360,
    documentSpace = 720,
    documentFraction = 0.45;
  let resizingDocument = false,
    resizeX = 0,
    resizeWidth = 0;
  function reportDocumentBounds() {
    if (destroyed) return;
    if (!documentOpen || !dockBody) {
      onDocumentBounds(null);
      return;
    }
    const { left, top, width, height } = dockBody.getBoundingClientRect();
    onDocumentBounds({ left, top, width, height });
  }
  function measureDocument() {
    if (!grid || !documentOpen) {
      onDocumentBounds(null);
      return;
    }
    const style = getComputedStyle(grid),
      gap = parseFloat(style.columnGap) || 0;
    const navigation =
      compact || quiet ? 0 : sidebar.getBoundingClientRect().width + gap;
    documentSpace = Math.max(
      0,
      grid.clientWidth -
        parseFloat(style.paddingLeft) -
        parseFloat(style.paddingRight) -
        navigation -
        gap,
    );
    documentWidth = documentDockWidth(documentSpace, documentFraction);
    void tick().then(() => requestAnimationFrame(reportDocumentBounds));
  }
  function observeDocumentBounds(node: HTMLElement) {
    // The grid can change its tracks without changing its own outer size.
    // Observe this actual slot so the retained editor follows the new bounds.
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(reportDocumentBounds);
    observer?.observe(node);
    return {
      destroy() {
        observer?.disconnect();
      },
    };
  }
  function observeDocument(node: HTMLElement) {
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measureDocument);
    observer?.observe(node);
    window.addEventListener("resize", measureDocument);
    return {
      destroy() {
        observer?.disconnect();
        window.removeEventListener("resize", measureDocument);
      },
    };
  }
  $: if (grid) {
    documentOpen;
    compact;
    quiet;
    void tick().then(measureDocument);
  }
  function changeDocumentWidth(value: number) {
    documentWidth = documentDockWidth(
      documentSpace,
      value / (documentSpace || 1),
    );
    documentFraction = documentWidth / (documentSpace || 1);
    void tick().then(() => requestAnimationFrame(reportDocumentBounds));
  }
  function beginDocumentResize(event: PointerEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    resizeX = event.clientX;
    resizeWidth = documentWidth;
    resizingDocument = true;
  }
  function moveDocumentResize(event: PointerEvent) {
    if (resizingDocument)
      changeDocumentWidth(resizeWidth + resizeX - event.clientX);
  }
  function documentResizeKeys(event: KeyboardEvent) {
    const changes: Record<string, number> = {
      ArrowLeft: documentWidth + 20,
      ArrowRight: documentWidth - 20,
      Home: 0,
      End: documentSpace,
    };
    if (!(event.key in changes)) return;
    event.preventDefault();
    changeDocumentWidth(changes[event.key]);
  }
  export let quiet = false;
  export let expanded = false;
  /** The current module's resource tree accompanies navigation. */
  export let files = false;
  export let compact = false;
  export let view: View = "journal";
  export let indexOpen = false;
  export let settingsOpen = false;
  export let settingsLocked = false;
  export let scrollElement: HTMLElement | undefined = undefined;
  export let onToggleIndex: () => void = () => {};
  export let onToggleSettings: () => void = () => {};
  /** New conversation, reachable from every page and with the sidebar closed. */
  export let canCreate = false;
  export let onCreate: () => void = () => {};
  export let onReadingFocus: (element: HTMLElement) => void = () => {};
  let sidebar: HTMLElement;
  let navTrigger: HTMLButtonElement;
  let settingsTrigger: HTMLButtonElement;
  let collapsedSettingsTrigger: HTMLButtonElement;
  let settingsDialog: HTMLDialogElement;
  let settingsHeading: HTMLHeadingElement;
  let settingsReturnFocus: HTMLElement | null = null;
  let destroyed = false;
  onDestroy(() => {
    destroyed = true;
    settingsDialog?.close();
  });

  // Keep the settings controllers mounted. Native modal inertness and focus
  // containment do not hide or resize the underlying workspace's scroll owner.
  $: if (settingsDialog) syncSettings(settingsOpen);
  function syncSettings(open: boolean) {
    if (open && !settingsDialog.open) {
      settingsReturnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      settingsDialog.showModal();
      // Enter the dialog without presenting Close as an already selected action.
      // Tab still reaches Close first and uses its normal keyboard focus ring.
      settingsHeading?.focus({ preventScroll: true });
    } else if (!open && settingsDialog.open) {
      settingsDialog.close();
      void tick().then(() => {
        if (destroyed || settingsDialog.open) return;
        const target =
          settingsReturnFocus !== document.body
            ? settingsReturnFocus
            : settingsTrigger;
        if (target?.isConnected && target.getClientRects().length)
          target.focus({ preventScroll: true });
        else
          (navigationOpen ? settingsTrigger : collapsedSettingsTrigger)?.focus({
            preventScroll: true,
          });
      });
    }
  }
  function closeSettings() {
    // Escape and the visible close control share the existing permission and
    // pending-operation lock. Never bypass recovery by dismissing the dialog.
    if (!settingsLocked) onToggleSettings();
  }
  // A panel already visible at startup must not steal the composer’s focus.
  let previouslyIndexOpen = false;
  $: navigationOpen = compact ? indexOpen : !quiet;
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
    if (compact && indexOpen) {
      event.preventDefault();
      onToggleIndex();
      navTrigger?.focus();
    }
  }
</script>

<svelte:window on:keydown={panelKey} />
<div class="shell" data-native-readiness={NATIVE_READINESS_MARKER}>
  <header
    class="topbar"
    data-tauri-drag-region
    inert={settingsOpen}
    aria-hidden={settingsOpen ? "true" : undefined}
  >
    <div class="topbar-left" data-tauri-drag-region>
      <button
        bind:this={navTrigger}
        class="icon-action index-toggle"
        type="button"
        aria-label="Toggle sidebar"
        title="Toggle sidebar"
        aria-expanded={navigationOpen}
        aria-controls="journal-index"
        on:click={onToggleIndex}><Icon name="sidebar" /></button
      >
      <button
        class="icon-action new-conversation"
        type="button"
        aria-label="Start a new conversation"
        title="New conversation · Ctrl+N"
        aria-keyshortcuts="Control+N"
        disabled={!canCreate}
        on:click={onCreate}><Icon name="plus" /></button
      >
    </div>
    <div class="topbar-actions">
      <WindowControls />
    </div>
  </header>

  <!-- Preserve the transcript's existing scroll owner and mounted controllers.
       Panels only change visibility; opening Settings never recreates a draft. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <main
    class="workspace"
    bind:this={scrollElement}
    use:quietScrollbar
    on:scroll={reportDocumentBounds}
    inert={settingsOpen}
    aria-hidden={settingsOpen ? "true" : undefined}
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
    <!-- The grid's empty right track keeps every page where the former widget
         column left it; nothing is rendered there. -->
    <div
      class="journal-grid"
      bind:this={grid}
      use:observeDocument
      data-document-open={documentOpen}
      style:--document-width={`${documentWidth}px`}
      data-quiet={quiet}
      data-index-open={indexOpen}
      data-expanded={expanded}
    >
      {#if compact && indexOpen}<button
          class="nav-scrim"
          aria-label="Close sidebar"
          on:click={onToggleIndex}
        ></button>{/if}
      <aside
        bind:this={sidebar}
        id="journal-index"
        class="column column-left"
        class:has-files={files && !!$$slots.files}
        aria-label="Workspace navigation"
      >
        {#if compact}<button
            class="icon-action nav-close"
            aria-label="Close sidebar"
            on:click={onToggleIndex}><Icon name="close" /></button
          >{/if}
        <div
          class="sidebar-content"
          use:quietScrollbar
          use:navIndicator={".sidebar-menu-row.current"}
        >
          <slot name="left" />
        </div>
        <div class="sidebar-files" hidden={!files || !$$slots.files}>
          <slot name="files" />
        </div>
        <div class="sidebar-footer">
          <button
            bind:this={settingsTrigger}
            type="button"
            class="sidebar-menu-row"
            aria-haspopup="dialog"
            aria-controls="workspace-settings"
            disabled={settingsLocked}
            on:click={onToggleSettings}
            ><Icon name="settings" size={15} />Settings</button
          >
        </div>
      </aside>
      <div
        class="column column-middle"
        use:pageFade={settingsOpen || documentOpen ? null : view}
      >
        <slot />
      </div>
      <aside
        class="column column-document"
        hidden={!documentOpen}
        aria-label="Document workspace"
      >
        <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
        <div
          class="document-resizer"
          role="separator"
          aria-label="Resize conversation and document"
          aria-orientation="vertical"
          aria-valuemin={Math.round(Math.min(280, documentSpace / 2))}
          aria-valuemax={Math.round(
            documentSpace - Math.min(280, documentSpace / 2),
          )}
          aria-valuenow={Math.round(documentWidth)}
          tabindex="0"
          on:pointerdown={beginDocumentResize}
          on:pointermove={moveDocumentResize}
          on:pointerup={() => (resizingDocument = false)}
          on:lostpointercapture={() => (resizingDocument = false)}
          on:keydown={documentResizeKeys}
        ></div>
        <button
          class="icon-action document-pane-close"
          aria-label="Close document pane"
          title={documentTitle
            ? `Close ${documentTitle} documents`
            : "Close document pane"}
          on:click={onCloseDocument}><Icon name="close" size={16} /></button
        >
        <div
          class="document-pane-space"
          bind:this={dockBody}
          use:observeDocumentBounds
        ></div>
      </aside>
      {#if resizingDocument}<div
          class="document-resize-shield"
          aria-hidden="true"
        ></div>{/if}
      <!-- Share the reading column's grid cell, so the floating control follows
           its centre when navigation is opened or collapsed. -->
      <div class="column column-middle shell-footer">
        <slot name="footer" />
      </div>
    </div>
  </main>
  {#if !navigationOpen}
    <button
      bind:this={collapsedSettingsTrigger}
      class="icon-action settings-collapsed"
      type="button"
      aria-label="Settings"
      title="Settings"
      aria-haspopup="dialog"
      aria-controls="workspace-settings"
      disabled={settingsLocked}
      inert={settingsOpen}
      aria-hidden={settingsOpen ? "true" : undefined}
      on:click={onToggleSettings}><Icon name="settings" size={15} /></button
    >
  {/if}
  <dialog
    bind:this={settingsDialog}
    id="workspace-settings"
    class="settings-dialog"
    aria-labelledby="settings-dialog-title"
    on:cancel|preventDefault={closeSettings}
  >
    <header class="settings-dialog-heading">
      <h1 bind:this={settingsHeading} id="settings-dialog-title" tabindex="-1">
        Settings
      </h1>
      <button
        class="settings-close"
        type="button"
        aria-label="Close settings"
        disabled={settingsLocked}
        on:click={closeSettings}
        ><span>Close</span><Icon name="close" size={16} /></button
      >
    </header>
    <div class="surface"><slot name="surface" /></div>
  </dialog>
</div>
