<script lang="ts">
  import { onMount, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  export let x = 200;
  export let y = 200;
  export let title = "Ask Kejtu";
  export let onClose: () => void;
  let panel: HTMLDivElement;
  let width = 560,
    height = 240;
  let drag: {
    id: number;
    x: number;
    y: number;
    left: number;
    top: number;
    width: number;
    height: number;
    resize: boolean;
  } | null = null;
  const key = "kejtu-document-assist-panel-v2";
  function remember() {
    try {
      localStorage.setItem(key, JSON.stringify({ x, y, width, height }));
    } catch {
      /* Layout persistence is optional. */
    }
  }
  function clamp() {
    width = Math.min(Math.max(320, width), innerWidth - 16);
    height = Math.min(Math.max(210, height), innerHeight - 16);
    x = Math.max(8, Math.min(x, innerWidth - width - 8));
    y = Math.max(8, Math.min(y, innerHeight - height - 8));
  }
  function start(e: PointerEvent, resize = false) {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag = {
      id: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      left: x,
      top: y,
      width,
      height,
      resize,
    };
  }
  function move(e: PointerEvent) {
    if (!drag || drag.id !== e.pointerId) return;
    if (drag.resize) {
      width = drag.width + e.clientX - drag.x;
      height = drag.height + e.clientY - drag.y;
    } else {
      x = drag.left + e.clientX - drag.x;
      y = drag.top + e.clientY - drag.y;
    }
    clamp();
  }
  function end() {
    if (drag) remember();
    drag = null;
  }
  function keys(e: KeyboardEvent, resize = false) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key))
      return;
    e.preventDefault();
    const step = e.shiftKey ? 40 : 10;
    const dx =
      e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
    const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
    if (resize) {
      width += dx;
      height += dy;
    } else {
      x += dx;
      y += dy;
    }
    clamp();
    remember();
  }
  onMount(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) ?? "null");
      if (
        saved &&
        ["x", "y", "width", "height"].every((k) => Number.isFinite(saved[k]))
      ) {
        ({ x, y, width, height } = saved);
      }
    } catch {}
    clamp();
    // A nonmodal top-layer surface also sits above WebKit's composited scrollbars.
    // It retains ordinary document interaction and contains its own choice menus.
    panel.showPopover?.();
    void tick().then(() =>
      panel?.querySelector<HTMLTextAreaElement>("textarea")?.focus(),
    );
  });
</script>

<svelte:window
  on:resize={clamp}
  on:pointermove={move}
  on:pointerup={end}
  on:pointercancel={end}
/>
<div
  bind:this={panel}
  class="document-popover"
  popover="manual"
  role="dialog"
  aria-label={title}
  tabindex="-1"
  style:left={`${x}px`}
  style:top={`${y}px`}
  style:width={`${width}px`}
  style:height={`${height}px`}
  on:keydown={(e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  }}
>
  <div class="popover-heading">
    <button
      class="drag"
      aria-label="Move Ask Kejtu panel"
      title="Drag to move · arrow keys also move this panel"
      on:pointerdown={(e) => start(e)}
      on:keydown={(e) => keys(e)}
      ><span class="grip" aria-hidden="true">⠿</span>{title}</button
    >
    <button
      class="close"
      title="Close panel"
      aria-label="Close Ask Kejtu"
      on:click={onClose}><Icon name="close" size={14} /></button
    >
  </div>
  <div class="popover-body"><slot /></div>
  <button
    class="resize"
    aria-label="Resize Ask Kejtu panel"
    title="Drag to resize · arrow keys also resize this panel"
    on:pointerdown={(e) => start(e, true)}
    on:keydown={(e) => keys(e, true)}
    ><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"
      ><path d="M3 9 9 3M7 9l2-2" fill="none" stroke="currentColor" /></svg
    ></button
  >
</div>

<style>
  .document-popover {
    position: fixed;
    inset: auto;
    margin: 0;
    padding: 0;
    isolation: isolate;
    z-index: 60;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    border: 1px solid var(--ke-border);
    border-radius: 12px;
    background: var(--ke-surface-raised);
    box-shadow: 0 12px 38px rgb(0 0 0 / 0.13);
    color: var(--ke-text-primary);
    font: 12px var(--ke-font-ui);
  }
  .document-popover::backdrop {
    background: transparent;
    pointer-events: none;
  }
  .popover-heading {
    display: flex;
    align-items: center;
    padding: 6px 8px;
    flex: none;
  }
  .document-popover .popover-heading button,
  .document-popover .resize {
    background: transparent;
    border: 0;
    color: var(--ke-text-secondary);
    font: inherit;
    border-radius: 5px;
    cursor: pointer;
    min-height: 0;
  }
  .document-popover .popover-heading button:hover {
    background: var(--ke-surface-inset);
  }
  .document-popover button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: -2px;
  }
  .document-popover .popover-heading .drag {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    text-align: left;
    padding: 6px;
    cursor: grab;
    touch-action: none;
  }
  .document-popover .popover-heading .drag:active {
    cursor: grabbing;
  }
  .grip {
    font-size: 16px;
    color: var(--ke-text-tertiary);
  }
  .document-popover .popover-heading .close {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
  }
  .popover-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 2px 14px 16px;
    scrollbar-width: thin;
  }
  .document-popover .resize {
    position: absolute;
    bottom: 1px;
    right: 1px;
    display: grid;
    place-items: center;
    width: 16px;
    height: 16px;
    padding: 0;
    cursor: nwse-resize;
    touch-action: none;
    color: var(--ke-text-tertiary);
  }
</style>
