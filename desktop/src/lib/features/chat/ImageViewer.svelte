<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import type { GalleryImage } from "./images";
  export let images: GalleryImage[];
  export let index = 0;
  export let load: (image: GalleryImage) => Promise<string>;
  export let onClose: () => void;
  let dialog: HTMLDialogElement;
  let stage: HTMLDivElement;
  let src = "",
    error = "",
    loading = false;
  let width = 0,
    height = 0,
    scale = 1,
    fit = 1;
  let fitted = true,
    sequence = 0,
    destroyed = false;
  let dragging: { x: number; y: number; left: number; top: number } | null =
    null;
  let observer: ResizeObserver | undefined;
  function fitImage() {
    if (!stage || !width || !height) return;
    fit = Math.min(
      1,
      Math.max(1, stage.clientWidth - 32) / width,
      Math.max(1, stage.clientHeight - 32) / height,
    );
    if (fitted) scale = fit;
  }
  async function show(image: GalleryImage) {
    const token = ++sequence;
    src = "";
    error = "";
    loading = true;
    fitted = true;
    width = height = 0;
    try {
      const value = await load(image);
      if (!destroyed && token === sequence) src = value;
    } catch (caught) {
      if (!destroyed && token === sequence)
        error =
          caught instanceof Error
            ? caught.message
            : "The image could not be loaded.";
    } finally {
      if (!destroyed && token === sequence) loading = false;
    }
  }
  let shownId = "";
  $: if (images[index] && images[index].id !== shownId) {
    shownId = images[index].id;
    void show(images[index]);
  }
  function zoom(value: number) {
    fitted = false;
    scale = Math.min(8, Math.max(Math.min(fit, 0.1), value));
  }
  function navigate(delta: number) {
    if (index + delta >= 0 && index + delta < images.length) {
      index += delta;
      if (stage) stage.scrollTo?.(0, 0);
    }
  }
  function keyboard(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      event.stopPropagation();
      navigate(-1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      event.stopPropagation();
      navigate(1);
    }
  }
  function startPan(event: PointerEvent) {
    if (event.button !== 0 || scale <= fit || !stage) return;
    event.preventDefault();
    stage.setPointerCapture(event.pointerId);
    dragging = {
      x: event.clientX,
      y: event.clientY,
      left: stage.scrollLeft,
      top: stage.scrollTop,
    };
  }
  function pan(event: PointerEvent) {
    if (!dragging) return;
    stage.scrollLeft = dragging.left - event.clientX + dragging.x;
    stage.scrollTop = dragging.top - event.clientY + dragging.y;
  }
  function wheel(event: WheelEvent) {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      zoom(scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15));
    }
  }
  onMount(async () => {
    await tick();
    if (destroyed) return;
    dialog.showModal();
    observer =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(fitImage);
    observer?.observe(stage);
  });
  onDestroy(() => {
    destroyed = true;
    sequence++;
    observer?.disconnect();
    if (dialog?.open) dialog.close();
  });
</script>

<dialog
  bind:this={dialog}
  aria-label="Image viewer"
  on:cancel|preventDefault={onClose}
  on:keydown={keyboard}
>
  <div class="viewer">
    <header>
      <div class="navigation">
        <button
          type="button"
          aria-label="Previous image"
          disabled={index === 0}
          on:click={() => navigate(-1)}>←</button
        >
        <span aria-live="polite">Image {index + 1} of {images.length}</span>
        <button
          type="button"
          aria-label="Next image"
          disabled={index === images.length - 1}
          on:click={() => navigate(1)}>→</button
        >
      </div>
      <div class="zoom">
        <button
          type="button"
          disabled={!width}
          on:click={() => {
            fitted = true;
            fitImage();
          }}>Fit</button
        >
        <button
          type="button"
          aria-label="Actual image size"
          disabled={!width}
          on:click={() => zoom(1)}>100%</button
        >
        <button
          type="button"
          aria-label="Zoom out"
          disabled={!width}
          on:click={() => zoom(scale / 1.25)}>−</button
        >
        <output aria-label="Image zoom">{Math.round(scale * 100)}%</output>
        <button
          type="button"
          aria-label="Zoom in"
          disabled={!width}
          on:click={() => zoom(scale * 1.25)}>+</button
        >
      </div>
      <button
        type="button"
        class="close"
        aria-label="Close image viewer"
        on:click={onClose}>×</button
      >
    </header>
    <!-- A scrollable viewport needs keyboard focus for native scrolling. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      bind:this={stage}
      role="region"
      aria-label="Image viewport"
      tabindex="0"
      class="stage"
      class:pannable={scale > fit}
      class:dragging={!!dragging}
      on:pointerdown={startPan}
      on:pointermove={pan}
      on:pointerup={() => (dragging = null)}
      on:pointercancel={() => (dragging = null)}
      on:lostpointercapture={() => (dragging = null)}
      on:wheel|nonpassive={wheel}
    >
      {#if loading}<p role="status">Loading image…</p>{:else if error}<div
          class="failure"
        >
          <p role="alert">{error}</p>
          <button type="button" on:click={() => show(images[index])}
            >Retry image</button
          >
        </div>
      {:else if src}<div
          class="canvas"
          style:min-width={`${width * scale + 32}px`}
          style:min-height={`${height * scale + 32}px`}
        >
          <img
            {src}
            alt={`Image ${index + 1} of ${images.length}`}
            draggable="false"
            style:visibility={width ? "visible" : "hidden"}
            style:width={width ? `${width * scale}px` : "auto"}
            style:height={height ? `${height * scale}px` : "auto"}
            on:load={(event) => {
              width = (event.currentTarget as HTMLImageElement).naturalWidth;
              height = (event.currentTarget as HTMLImageElement).naturalHeight;
              fitImage();
            }}
            on:error={() => (error = "The image could not be displayed.")}
          />
        </div>{/if}
    </div>
    <footer>
      {#if width}{width} × {height} ·
      {/if}Scroll or drag to pan when zoomed · Esc to close
    </footer>
  </div>
</dialog>

<style>
  dialog {
    position: fixed;
    inset: 0;
    width: min(96vw, 1600px);
    height: 92vh;
    max-width: 96vw;
    max-height: 92vh;
    margin: auto;
    padding: 0;
    color: var(--ke-text-primary);
    background: var(--ke-surface);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
  }
  dialog::backdrop {
    background: rgb(0 0 0 / 75%);
  }
  .viewer {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.6rem;
    border-bottom: 1px solid var(--ke-border);
  }
  .navigation,
  .zoom {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--ke-text-xs);
  }
  .zoom {
    margin-left: auto;
  }
  button {
    color: inherit;
    background: var(--ke-surface-raised);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    padding: 0.25rem 0.5rem;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .close {
    font-size: 1.2rem;
  }
  output {
    min-width: 3rem;
    text-align: center;
  }
  .stage {
    flex: 1;
    min-height: 0;
    overflow: auto;
    overscroll-behavior: contain;
    background: var(--ke-surface-raised);
  }
  .stage.pannable {
    cursor: grab;
    touch-action: none;
  }
  .stage.dragging {
    cursor: grabbing;
  }
  .canvas {
    display: grid;
    place-items: center;
    width: 100%;
    height: 100%;
    padding: 16px;
    box-sizing: border-box;
  }
  img {
    display: block;
    max-width: none;
    user-select: none;
  }
  .stage > p,
  .failure {
    padding: 1rem;
    font-size: var(--ke-text-sm);
  }
  footer {
    padding: 0.4rem 0.7rem;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  @media (max-width: 650px) {
    .zoom {
      order: 3;
      width: 100%;
      justify-content: center;
    }
    .close {
      margin-left: auto;
    }
  }
</style>
