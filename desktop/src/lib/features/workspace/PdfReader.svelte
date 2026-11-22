<script lang="ts">
  import { quietScrollbar } from "$lib/design/quiet-scrollbar";
  import { onMount, onDestroy, tick } from "svelte";
  import {
    blockAt,
    passageRange,
    passageText,
    pdfLayout,
    pdfPages,
    type MaterialPage,
    type PdfLayout,
  } from "./material-reader";
  import {
    MAX_PASSAGE_CHARACTERS,
    type StudyPassage,
  } from "$lib/features/chat/study-requests";
  export let courseId: string;
  export let file: string;
  export let first: MaterialPage;
  export let active = true;
  export let zoom = "100%";
  export let onPage: (
    value: MaterialPage | null,
    number: number,
  ) => void = () => {};
  /** The chat draft's marked passage; this reader only displays it. */
  export let marked: StudyPassage | null = null;
  /** Without an owner for marks, pages stay plain images. */
  export let onMark: ((value: StudyPassage | null) => void) | undefined =
    undefined;
  export let markDisabled = false;
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
  // Paragraph boxes load on first pointer use of a page, never while only
  // scrolling. A page without usable boxes stays an ordinary image.
  let layouts = new Map<number, PdfLayout | null>();
  const loadingLayouts = new Set<number>();
  let hover: { page: number; index: number } | null = null;
  let press: { page: number; index: number; x: number; y: number } | null =
    null;
  function wantLayout(number: number) {
    const page = displayed.get(number);
    if (!onMark || !page?.image || layouts.has(number)) return;
    if (loadingLayouts.has(number)) return;
    loadingLayouts.add(number);
    const image = page.image;
    pdfLayout(courseId, file, page.source)
      .then(
        (layout) => {
          // Fail closed when the boxes do not describe the rendered page.
          const ratio = image.height / image.width;
          return Math.abs(layout.height / layout.width - ratio) / ratio < 0.02
            ? layout
            : null;
        },
        () => null,
      )
      .then((layout) => {
        loadingLayouts.delete(number);
        if (!destroyed) layouts = new Map(layouts).set(number, layout);
      });
  }
  $: if (onMark && marked)
    for (const [number, page] of displayed)
      if (page.source.artifact_id === marked.artifactId) wantLayout(number);
  function markedRange(
    number: number,
    page: MaterialPage | undefined,
    passage: StudyPassage | null,
    _layouts: Map<number, PdfLayout | null>,
  ) {
    const layout = layouts.get(number);
    return passage && layout && page?.source.artifact_id === passage.artifactId
      ? passageRange(layout, passage.text)
      : null;
  }
  function sheet(slotWidth: number, slotHeight: number, page: MaterialPage) {
    // The paper box can be taller than its image at very narrow widths.
    const height = slotHeight - 32,
      ratio = page.image!.height / page.image!.width;
    let w = slotWidth,
      h = slotWidth * ratio;
    if (h > height) {
      h = height;
      w = h / ratio;
    }
    return {
      left: `${((slotWidth - w) / 2 / slotWidth) * 100}%`,
      top: `${((height - h) / 2 / height) * 100}%`,
      width: `${(w / slotWidth) * 100}%`,
      height: `${(h / height) * 100}%`,
    };
  }
  function boxStyle(layout: PdfLayout, index: number) {
    const [x0, y0, x1, y1] = layout.blocks[index].box;
    const pct = (value: number, size: number) => (value / size) * 100;
    return `left:calc(${pct(x0, layout.width)}% - 5px);top:calc(${pct(y0, layout.height)}% - 3px);width:calc(${pct(x1 - x0, layout.width)}% + 10px);height:calc(${pct(y1 - y0, layout.height)}% + 6px)`;
  }
  function pointAt(event: PointerEvent, number: number) {
    const layout = layouts.get(number);
    const target = event.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    if (!layout || !rect.width || !rect.height) return -1;
    const scale = layout.width / rect.width;
    return blockAt(
      layout,
      (event.clientX - rect.left) * scale,
      (event.clientY - rect.top) * (layout.height / rect.height),
      4 * scale,
    );
  }
  function pointerMove(event: PointerEvent, number: number) {
    wantLayout(number);
    const index = pointAt(event, number);
    if (hover?.page !== number || hover.index !== index)
      hover = index < 0 ? null : { page: number, index };
  }
  function pointerDown(event: PointerEvent, number: number) {
    if (event.button !== 0 || markDisabled) return;
    press = {
      page: number,
      index: pointAt(event, number),
      x: event.clientX,
      y: event.clientY,
    };
  }
  function pointerUp(event: PointerEvent, number: number) {
    const start = press;
    press = null;
    const layout = layouts.get(number);
    const page = displayed.get(number);
    if (!start || start.page !== number || !layout || !page || markDisabled)
      return;
    const index = pointAt(event, number);
    if (start.index < 0 || index < 0) return;
    const current = markedRange(number, page, marked, layouts);
    const moved =
      Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5;
    // Shift-click grows the current mark; a drag spans from where it started.
    const anchor =
      !moved && event.shiftKey && current
        ? index >= current[0]
          ? current[0]
          : current[1]
        : start.index;
    if (
      !moved &&
      !event.shiftKey &&
      current &&
      index >= current[0] &&
      index <= current[1]
    ) {
      onMark?.(null);
      return;
    }
    let from = Math.min(anchor, index),
      to = Math.max(anchor, index);
    if (!moved && event.shiftKey && current) {
      from = Math.min(from, current[0]);
      to = Math.max(to, current[1]);
    }
    // A long run keeps the paragraphs nearest its anchor that fit in one
    // message card; a single paragraph is never cut.
    while (
      from < to &&
      passageText(layout, from, to).length > MAX_PASSAGE_CHARACTERS
    )
      if (index >= anchor) to--;
      else from++;
    onMark?.({
      courseId,
      artifactId: page.source.artifact_id,
      path: page.source.path,
      page: number,
      text: passageText(layout, from, to),
    });
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
  use:quietScrollbar
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
          {#if onMark}
            {@const layout = layouts.get(number)}
            {@const range = markedRange(number, value, marked, layouts)}
            {@const place = sheet(slot.width, slot.height, value)}
            <!-- Pointer-only enhancement; the mark itself is read from the chat card. -->
            <div
              class="marks"
              class:pointing={hover?.page === number && !markDisabled}
              style:left={place.left}
              style:top={place.top}
              style:width={place.width}
              style:height={place.height}
              aria-hidden="true"
              data-marks-page={number}
              on:pointermove={(event) => pointerMove(event, number)}
              on:pointerleave={() => {
                if (hover?.page === number) hover = null;
              }}
              on:pointerdown={(event) => pointerDown(event, number)}
              on:pointerup={(event) => pointerUp(event, number)}
              on:pointercancel={() => (press = null)}
            >
              {#if layout && range}
                {#each layout.blocks.slice(range[0], range[1] + 1) as _, i}
                  <div
                    class="block marked"
                    style={boxStyle(layout, range[0] + i)}
                  ></div>
                {/each}
              {/if}
              {#if layout && hover?.page === number && !markDisabled && !(range && hover.index >= range[0] && hover.index <= range[1])}
                {#key hover.index}<div
                    class="block hover"
                    style={boxStyle(layout, hover.index)}
                  ></div>{/key}
              {/if}
            </div>
          {/if}
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
    position: relative;
    background: #fff;
    display: grid;
    place-items: center;
    overflow: hidden;
  }
  /* Paper stays white in every theme: the Markdown mark's marker blue, with a
     light tint multiplied into the print so the words keep full contrast. */
  .marks {
    position: absolute;
    touch-action: pan-x pan-y;
  }
  .marks.pointing {
    cursor: pointer;
  }
  .block {
    position: absolute;
    border-radius: 3px;
    pointer-events: none;
  }
  .block.hover {
    box-shadow: inset 0 0 0 1px var(--ke-marker-blue);
    opacity: 0;
    animation: mark-hint 140ms ease 120ms forwards;
  }
  .block.marked {
    background: rgba(140, 148, 172, 0.22);
    mix-blend-mode: multiply;
    box-shadow: -3px 0 var(--ke-marker-blue);
  }
  @keyframes mark-hint {
    to {
      opacity: 0.55;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .block.hover {
      animation: none;
      opacity: 0.55;
    }
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
