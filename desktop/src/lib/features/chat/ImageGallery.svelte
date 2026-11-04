<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import ImageTile from "./ImageTile.svelte";
  import ImageViewer from "./ImageViewer.svelte";
  import { queuedImage, type GalleryImage } from "./images";
  export let images: GalleryImage[] = [];
  export let compact = false;
  export let disabled = false;
  export let onRemove: ((index: number) => void) | undefined = undefined;
  export let onViewerChange: (open: boolean) => void = () => {};
  let urls: Record<string, string> = {},
    errors: Record<string, string> = {};
  const visible = new Set<string>();
  const pending = new Map<string, Promise<string>>();
  let selected = -1,
    destroyed = false;
  let restore: HTMLButtonElement | undefined;
  async function load(image: GalleryImage): Promise<string> {
    if (image.src) return image.src;
    if (urls[image.id]) return urls[image.id];
    if (pending.has(image.id)) return pending.get(image.id)!;
    const task = queuedImage(async () => {
      if (destroyed || !image.load)
        throw new Error("This image is no longer available here.");
      return image.load();
    });
    pending.set(image.id, task);
    try {
      const src = await task;
      if (!destroyed && (visible.has(image.id) || selected >= 0)) {
        urls = { ...urls, [image.id]: src };
        errors = { ...errors, [image.id]: "" };
      }
      return src;
    } catch (caught) {
      if (!destroyed)
        errors = {
          ...errors,
          [image.id]:
            caught instanceof Error ? caught.message : "Image unavailable",
        };
      throw caught;
    } finally {
      pending.delete(image.id);
    }
  }
  function visibility(image: GalleryImage, shown: boolean) {
    if (shown) {
      visible.add(image.id);
      void load(image).catch(() => {});
    } else {
      visible.delete(image.id);
      if (selected < 0) {
        const next = { ...urls };
        delete next[image.id];
        urls = next;
      }
    }
  }
  async function close() {
    selected = -1;
    onViewerChange(false);
    urls = Object.fromEntries(
      Object.entries(urls).filter(([id]) => visible.has(id)),
    );
    await tick();
    if (restore?.isConnected) restore.focus({ preventScroll: true });
  }
  onDestroy(() => {
    destroyed = true;
    if (selected >= 0) onViewerChange(false);
  });
</script>

{#if images.length}
  <div class="image-gallery" class:compact aria-label="Attached images">
    {#each images as image, index (image.id)}
      <div class="tile">
        <ImageTile
          {index}
          {compact}
          src={image.src ?? urls[image.id] ?? ""}
          error={errors[image.id] ?? ""}
          onVisible={(shown) => visibility(image, shown)}
          onOpen={(button) => {
            restore = button;
            selected = index;
            onViewerChange(true);
          }}
        />
        {#if onRemove}<button
            class="remove"
            type="button"
            aria-label={`Remove image ${index + 1}`}
            {disabled}
            on:click={() => onRemove?.(index)}>×</button
          >{/if}
      </div>
    {/each}
  </div>
{/if}
{#if selected >= 0 && images[selected]}<ImageViewer
    {images}
    index={selected}
    {load}
    onClose={close}
  />{/if}

<style>
  .image-gallery {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 0.65rem;
    max-width: 100%;
    margin: 0.6rem 0;
  }
  .image-gallery.compact {
    max-height: 192px;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 2px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0;
  }
  .tile {
    position: relative;
    min-width: 0;
    width: fit-content;
    max-width: min(220px, 100%);
  }
  .remove {
    position: absolute;
    top: 0.2rem;
    right: 0.2rem;
    width: 24px;
    height: 24px;
    padding: 0;
    border-radius: 50%;
    border: 1px solid var(--ke-border);
    color: var(--ke-text-primary);
    background: var(--ke-surface);
    cursor: pointer;
  }
  .remove:disabled {
    cursor: default;
    opacity: 0.4;
  }
</style>
