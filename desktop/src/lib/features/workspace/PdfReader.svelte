<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { pdfPages, type MaterialPage } from "./material-reader";
  export let courseId: string;
  export let file: string;
  export let first: MaterialPage;
  export let active = true;
  export let zoom = "100%";
  export let onPage: (
    value: MaterialPage | null,
    number: number,
  ) => void = () => {};
  const pages = pdfPages(courseId, file, first);
  const total = first.page_count || 1;
  const ratios = new Map<number, number>();
  let errors = new Map<number, string>();
  let reader: HTMLDivElement;
  let width = 600;
  let viewportHeight = 600;
  let current = first.source.page;
  let targetedPage: number | null = null,
    targetedScroll = 0;
  let start = current,
    end = current;
  let displayed = new Map<number, MaterialPage>([[current, first]]);
  let loading = false,
    mounted = false,
    destroyed = false;
  let frame = 0,
    layoutRevision = 0;
  $: geometry = geometryFor(
    width,
    start,
    end,
    layoutRevision,
    zoom,
    viewportHeight,
  );
  function geometryFor(
    _width: number,
    from: number,
    to: number,
    _revision: number,
    _zoom: string,
    _height: number,
  ) {
    return {
      before: top(from),
      after: Math.max(0, top(total + 1) - top(to + 1)),
      slots: Array.from({ length: to - from + 1 }, (_, i) => ({
        number: from + i,
        height: height(from + i),
        width: pageWidth(ratios.get(from + i) || ratio),
      })),
    };
  }
  const ratio = first.image!.height / first.image!.width;
  function availableWidth() {
    const available = reader.clientWidth;
    return zoom === "width" || zoom === "page"
      ? available
      : Math.min(available, (760 * parseInt(zoom, 10)) / 100);
  }
  function pageWidth(aspect: number) {
    return zoom === "page"
      ? Math.min(width, Math.max(240, viewportHeight - 32) / aspect)
      : width;
  }
  function paperHeight(aspect: number) {
    return Math.max(240, pageWidth(aspect) * aspect);
  }
  function height(n: number) {
    return paperHeight(ratios.get(n) || ratio) + 32;
  }
  function top(n: number) {
    let value = (n - 1) * (paperHeight(ratio) + 32);
    for (const [page, actual] of ratios)
      if (page < n) value += paperHeight(actual) - paperHeight(ratio);
    return value;
  }
  function at(y: number) {
    let lo = 1,
      hi = total;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (top(mid) <= y) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }
  function announce() {
    onPage(displayed.get(current) || null, current);
  }
  async function fill() {
    if (loading || !active || destroyed) return;
    loading = true;
    try {
      while (active && !destroyed) {
        const wanted = [
          current,
          ...Array.from({ length: end - start + 1 }, (_, i) => start + i),
        ];
        const number = wanted.find((n) => !displayed.has(n) && !errors.has(n));
        if (number === undefined) break;
        try {
          const result = await pages.read(number);
          if (!result || destroyed) return;
          // Save dimensions before releasing distant images so scrolling back has
          // stable spacers, even in a PDF mixing portrait and landscape pages.
          ratios.set(number, result.image!.height / result.image!.width);
          layoutRevision++;
          if (number >= start && number <= end)
            displayed = new Map(displayed).set(number, result);
          announce();
        } catch (error) {
          errors.set(number, String(error));
          errors = errors;
        }
      }
    } finally {
      loading = false;
    }
  }
  function measure() {
    if (!reader || !active || !reader.clientWidth) return;
    width = availableWidth();
    viewportHeight = reader.clientHeight;
    // A short final page cannot always reach the top of the viewport. Honour an
    // explicit jump until the user scrolls, so its actions still target that page.
    if (
      targetedPage !== null &&
      Math.abs(reader.scrollTop - targetedScroll) < 1
    )
      current = targetedPage;
    else {
      targetedPage = null;
      current = at(reader.scrollTop + Math.min(120, reader.clientHeight / 4));
    }
    start = Math.max(1, at(reader.scrollTop) - 1);
    end = Math.min(total, at(reader.scrollTop + reader.clientHeight) + 1);
    displayed = new Map([...displayed].filter(([n]) => n >= start && n <= end));
    announce();
    void fill();
  }
  function scrolled() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(measure);
  }
  export async function go(number: number) {
    if (!reader || !Number.isInteger(number) || number < 1 || number > total)
      return;
    await tick();
    if (destroyed) return;
    reader.scrollTop = top(number);
    targetedPage = number;
    targetedScroll = reader.scrollTop;
    measure();
  }
  function reflow() {
    if (!active || !reader?.clientWidth) return;
    const selected = current;
    const nextWidth = availableWidth(),
      nextHeight = reader.clientHeight;
    if (
      nextWidth !== width ||
      nextHeight !== viewportHeight ||
      lastZoom !== zoom
    ) {
      width = nextWidth;
      viewportHeight = nextHeight;
      lastZoom = zoom;
      void tick().then(() => {
        if (!destroyed) void go(selected);
      });
    } else measure();
  }
  let lastZoom = zoom;
  onMount(() => {
    mounted = true;
    width = availableWidth() || 600;
    viewportHeight = reader.clientHeight;
    void go(first.source.page);
    const observer = new ResizeObserver(reflow);
    observer.observe(reader);
    return () => observer.disconnect();
  });
  $: if (mounted && active) {
    zoom;
    void tick().then(reflow);
  }
  $: if (active && mounted) {
    void tick().then(measure);
  }
  onDestroy(() => {
    destroyed = true;
    pages.dispose();
    cancelAnimationFrame(frame);
  });
</script>

<!-- Scroll regions must be keyboard focusable for native Page Up/Down and arrow scrolling. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="pdf-scroll"
  bind:this={reader}
  on:scroll={scrolled}
  tabindex="0"
  role="region"
  aria-label="PDF pages"
>
  <div style:height={`${geometry.before}px`} aria-hidden="true"></div>
  {#each geometry.slots as slot (slot.number)}
    {@const number = slot.number}
    {@const value = displayed.get(number)}
    <figure
      data-page={number}
      style:height={`${slot.height}px`}
      style:width={`${slot.width}px`}
    >
      <div class="paper" style:height={`${slot.height - 32}px`}>
        {#if value?.image}
          <img
            alt={`Page ${number} of ${total} — ${file.split("/").pop()}`}
            src={`data:${value.image.mime_type};base64,${value.image.data}`}
          />
        {:else if errors.has(number)}
          <div class="page-message">
            <p role="alert">{errors.get(number)}</p>
            <button
              on:click={() => {
                errors.delete(number);
                errors = errors;
                void fill();
              }}>Retry page {number}</button
            >
          </div>
        {:else}<p class="page-message" role="status">
            Reading page {number}…
          </p>{/if}
      </div>
      <figcaption>Page {number}</figcaption>
    </figure>
  {/each}
  <div style:height={`${geometry.after}px`} aria-hidden="true"></div>
</div>

<style>
  .pdf-scroll {
    height: var(--ke-reader-height, 64vh);
    min-height: 260px;
    background: var(--ke-reader-stage);
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-gutter: stable;
    outline-offset: 3px;
  }
  figure {
    margin: 0 auto;
    max-width: 100%;
  }
  .paper {
    background: #fff;
    display: grid;
    place-items: center;
    overflow: hidden;
  }
  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
  figcaption {
    height: 32px;
    padding: 7px 0;
    box-sizing: border-box;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-3xs);
    text-align: center;
  }
  .page-message {
    color: #626262;
    padding: 24px;
    font-size: var(--ke-text-xs);
    max-width: 48ch;
  }
  .page-message button {
    border: 0;
    background: #efefef;
    padding: 7px 12px;
    border-radius: var(--ke-radius-sm);
    color: #333;
  }
  @media (max-width: 800px) {
    .pdf-scroll {
      height: var(--ke-reader-height, 58vh);
    }
  }
</style>
