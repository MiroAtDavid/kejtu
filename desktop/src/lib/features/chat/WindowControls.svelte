<script lang="ts">
  import { onMount } from "svelte";
  import { confirmReaderClose } from "../workspace/reader-drafts";
  import { isTauri } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { windowResizeDirection } from "./window-resize";

  const native = isTauri();
  const appWindow = native ? getCurrentWindow() : undefined;
  let maximized = $state(false);
  let fullscreen = false;
  let error = $state("");
  let disposed = false;
  let stateRefresh: ReturnType<typeof setTimeout> | undefined;

  async function readWindowState() {
    if (!appWindow) return;
    const [isMaximized, isFullscreen] = await Promise.all([
      appWindow.isMaximized(),
      appWindow.isFullscreen(),
    ]);
    if (disposed) return;
    maximized = isMaximized;
    fullscreen = isFullscreen;
    document.documentElement.dataset.windowMaximized = String(
      maximized || fullscreen,
    );
  }

  function refreshWindowState() {
    void readWindowState().catch(() => undefined);
    clearTimeout(stateRefresh);
    // The compositor can acknowledge a resize before its maximized flag changes.
    // Read again after it settles, including changes made through system shortcuts.
    stateRefresh = setTimeout(() => {
      void readWindowState().catch(() => undefined);
    }, 250);
  }

  onMount(() => {
    if (!appWindow) return;
    document.documentElement.dataset.nativeWindow = "true";
    let unlisten: (() => void) | undefined;
    let unclose: (() => void) | undefined;
    let closing = false;
    void appWindow
      .onCloseRequested(async (event) => {
        if (closing) {
          event.preventDefault();
          return;
        }
        closing = true;
        try {
          if (!(await confirmReaderClose())) event.preventDefault();
        } catch {
          event.preventDefault();
          error =
            "The document could not be checked. Save a copy before closing.";
        } finally {
          closing = false;
        }
      })
      .then((off) => {
        if (disposed) off();
        else unclose = off;
      })
      .catch(() => {
        error = "Document close protection could not connect.";
      });
    refreshWindowState();
    void appWindow
      .onResized(refreshWindowState)
      .then((off) => {
        if (disposed) off();
        else unlisten = off;
      })
      .catch(() => undefined);
    return () => {
      disposed = true;
      clearTimeout(stateRefresh);
      unlisten?.();
      unclose?.();
      delete document.documentElement.dataset.nativeWindow;
      delete document.documentElement.dataset.windowMaximized;
    };
  });

  async function control(action: "minimize" | "toggleMaximize" | "close") {
    if (!appWindow) return;
    error = "";
    try {
      await appWindow[action]();
      if (action === "toggleMaximize") refreshWindowState();
    } catch {
      error =
        "Window control unavailable. You can use your system window shortcuts.";
    }
  }

  function resize(event: PointerEvent) {
    if (!appWindow || maximized || fullscreen || event.button !== 0) return;
    const direction = windowResizeDirection(
      event.clientX,
      event.clientY,
      innerWidth,
      innerHeight,
    );
    if (!direction) return;
    event.preventDefault();
    void appWindow.startResizeDragging(direction).catch(() => {
      error =
        "Window resizing is unavailable. You can still maximize the window.";
    });
  }
</script>

<svelte:body onpointerdown={resize} />

{#if native}
  <div class="window-controls" role="group" aria-label="Window controls">
    <button
      type="button"
      aria-label="Minimize window"
      title="Minimize"
      onclick={() => control("minimize")}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5h9" /></svg>
    </button>
    <button
      type="button"
      aria-label={maximized ? "Restore window" : "Maximize window"}
      title={maximized ? "Restore" : "Maximize"}
      onclick={() => control("toggleMaximize")}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        {#if maximized}<path d="M5.5 5.5v-2h7v7h-2" /><rect
            x="3.5"
            y="5.5"
            width="7"
            height="7"
            rx="0.5"
          />
        {:else}<rect x="3.5" y="3.5" width="9" height="9" rx="0.5" />{/if}
      </svg>
    </button>
    <button
      type="button"
      class="close"
      aria-label="Close window"
      title="Close"
      onclick={() => control("close")}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true"
        ><path d="m4.5 4.5 7 7m0-7-7 7" /></svg
      >
    </button>
  </div>
  {#if error}<div class="window-error" role="alert">
      {error}<button type="button" onclick={() => (error = "")}>Dismiss</button>
    </div>{/if}
{/if}

<style>
  .window-controls {
    display: flex;
    align-self: stretch;
    align-items: center;
    gap: 2px;
    padding-inline: 3px;
  }
  .window-controls button {
    display: grid;
    place-items: center;
    width: 32px;
    height: 30px;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    cursor: default;
  }
  .window-controls button:hover {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .window-controls .close:hover {
    background: #b93636;
    color: #fff;
  }
  svg {
    width: 15px;
    height: 15px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .window-error {
    position: fixed;
    z-index: 100;
    top: 45px;
    right: 12px;
    max-width: 22rem;
    padding: 12px;
    border: 1px solid var(--ke-border-strong);
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-raised);
    color: var(--ke-text-primary);
    font-size: var(--ke-text-sm);
    box-shadow: var(--ke-shadow-panel);
  }
  .window-error button {
    margin-left: 8px;
    color: inherit;
    background: none;
    border: 0;
    text-decoration: underline;
    cursor: pointer;
  }
</style>
