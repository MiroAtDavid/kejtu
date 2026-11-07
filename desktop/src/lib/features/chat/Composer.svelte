<script lang="ts">
  import StudyRequestCard from "./StudyRequestCard.svelte";
  import {
    draftStudyRequests,
    withStudyRequests,
    type StudyReference,
  } from "./study-requests";
  export let onOpenStudy:
    ((reference: StudyReference) => Promise<void>) | undefined = undefined;
  let showRequestText = false;
  $: if (!draft) showRequestText = false;
  import SkillChooser from "./SkillChooser.svelte";
  import type { SkillSelection } from "$lib/api/wire.generated";
  export let selectedSkill: SkillSelection | null = null;
  import { afterUpdate, onDestroy, onMount, tick } from "svelte";
  import { CompositionGuard, composerAction } from "./keys";
  import {
    draftImages,
    withImages,
    readImage,
    imageFileLength,
    checkImageCapacity,
    imageAllowance,
    imageBytes,
    byteSize,
    validateDraftImage,
  } from "./images";
  import ImageGallery from "./ImageGallery.svelte";
  import {
    clipboardImage,
    droppedImage,
    listenImageDrops,
    nativeImageInput,
    type ImageDrop,
  } from "$lib/api/image-input";

  /** Draft text. Owned by the coordinator so it survives view switches. */
  export let draft = "";
  export let conversationId = "";
  /** No usable conversation is loaded: the field cannot accept input at all. */
  export let disabled = false;
  /** The draft cannot be submitted right now (locked, empty, or work running). */
  export let readOnly = false;
  export let sending = false;
  export let empty = false;
  export let autoFocus = false;
  export let onSend: () => void = () => {};
  export let onImageViewer: (open: boolean) => void = () => {};

  const guard = new CompositionGuard();
  let field: HTMLTextAreaElement | undefined;
  let form: HTMLFormElement | undefined;
  let unlistenImages: (() => void) | undefined;
  let sizeObserver: ResizeObserver | undefined;
  let focused = false;
  let focusPending = false;
  export let maximumMessageBytes: number | null = null;
  let skills: SkillChooser;
  let activeOptionId: string | undefined;
  let skillQuery: string | null = null;
  let slashStart = 0,
    slashEnd = 0,
    dismissedSlash = "";
  let imageProgress = "";
  function syncSkillQuery() {
    if (!field || guard.composing) return;
    const before = field.value.slice(0, field.selectionStart);
    const start = before.lastIndexOf("\n") + 1;
    const line = before.slice(start);
    if (
      !line.startsWith("/") ||
      line === dismissedSlash ||
      field.selectionStart !== field.selectionEnd
    ) {
      skillQuery = null;
      return;
    }
    slashStart = start;
    slashEnd = field.selectionStart;
    skillQuery = line
      .slice(1)
      .replace(/^\s*skill(?:\s+|$)/i, "")
      .trim();
  }
  function chooseSkill() {
    if (!field) return;
    const text = field.value.slice(0, slashStart) + field.value.slice(slashEnd);
    draft = withImages(
      showRequestText ? text : withStudyRequests(text, studyDraft.requests),
      visibleDraft.images,
    );
    skillQuery = null;
    dismissedSlash = "";
    void tick().then(() => {
      field?.focus();
      field?.setSelectionRange(slashStart, slashStart);
    });
  }
  $: messageTooLarge =
    maximumMessageBytes !== null &&
    new TextEncoder().encode(draft).length > maximumMessageBytes;

  let readingImage = false;
  let imageError = "";
  let destroyed = false;
  let dragDepth = 0;
  const isFileDrag = (event: DragEvent) =>
    Array.from(event.dataTransfer?.types ?? []).some((type) =>
      ["Files", "text/uri-list", "x-special/gnome-copied-files"].includes(type),
    );
  function transferredFiles(data: DataTransfer | null | undefined): File[] {
    const files = Array.from(data?.files ?? []);
    if (files.length) return files;
    return Array.from(data?.items ?? [])
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
  }
  function dragOver(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer)
      event.dataTransfer.dropEffect =
        disabled || readOnly || sending || readingImage ? "none" : "copy";
  }
  function drop(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = 0;
    if (disabled || readOnly || sending || readingImage) {
      imageError =
        "Images cannot be attached right now. Try again when the message box is ready.";
      return;
    }
    const files = transferredFiles(event.dataTransfer);
    if (!files.length) {
      imageError = "Drop a local PNG or JPEG image file on the message box.";
      return;
    }
    void attach(files);
  }
  function preventFileNavigation(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
    if (event.type === "drop") {
      dragDepth = 0;
      imageError = "Drop images on the message box to attach them.";
    }
  }
  $: visibleDraft = draftImages(draft);
  $: studyDraft = draftStudyRequests(visibleDraft.text);
  async function attach(files: File[]) {
    try {
      // File metadata gives us the whole batch size before FileReader or image decoding.
      checkImageCapacity(
        visibleDraft.text,
        [
          ...visibleDraft.images.map((i) => i.length),
          ...files.map(imageFileLength),
        ],
        maximumMessageBytes,
      );
    } catch (error) {
      imageError = (error as Error).message;
      return;
    }
    return attachImages(files.length, (index) => readImage(files[index]));
  }
  async function attachImages(
    count: number,
    read: (index: number) => Promise<string | null>,
  ) {
    if (disabled || readOnly || sending || readingImage || !count) return;
    const before = draft,
      identity = conversationId;
    readingImage = true;
    imageError = "";
    try {
      if (visibleDraft.images.length + count > 32)
        throw Error("Use up to 32 images per message.");
      const images = [...visibleDraft.images];
      // Sequential reads bound memory and stop as soon as a batch cannot fit.
      // Commit only the complete batch; rejection never discards the existing draft.
      for (let index = 0; index < count; index++) {
        imageProgress = `Reading image ${index + 1} of ${count}…`;
        const image = await read(index);
        if (!image) return;
        if (destroyed || identity !== conversationId) return;
        if (before !== draft || disabled || readOnly || sending)
          throw Error(
            "The draft changed while reading. Please attach the image again.",
          );
        validateDraftImage(image);
        checkImageCapacity(
          visibleDraft.text,
          [...images.map((i) => i.length), image.length],
          maximumMessageBytes,
        );
        images.push(image);
      }
      draft = withImages(visibleDraft.text, images);
    } catch (error) {
      if (!destroyed && identity === conversationId)
        imageError =
          error instanceof Error
            ? error.message
            : typeof error === "string"
              ? error
              : "This image could not be read.";
    } finally {
      readingImage = false;
      imageProgress = "";
    }
  }
  function paste(event: ClipboardEvent) {
    const files = transferredFiles(event.clipboardData);
    if (files.length) {
      event.preventDefault();
      void attach(files);
      return;
    }
    const types = Array.from(event.clipboardData?.types ?? []);
    // WebKitGTK delivers a trusted screenshot paste with an empty DataTransfer.
    // Never inspect the system clipboard on startup, synthetic events or text paste.
    if (
      event.isTrusted &&
      nativeImageInput() &&
      (!types.length || types.some((type) => type.startsWith("image/")))
    ) {
      event.preventDefault();
      void attachImages(1, clipboardImage);
    }
  }

  function nativeDrop(drop: ImageDrop) {
    if (destroyed) return;
    if (drop.kind === "leave") {
      dragDepth = 0;
      return;
    }
    const target = document.elementFromPoint(drop.x, drop.y);
    const inside = target !== null && form?.contains(target);
    if (drop.kind === "over") {
      dragDepth = inside ? 1 : 0;
      return;
    }
    dragDepth = 0;
    if (!inside) {
      imageError = "Drop images on the message box to attach them.";
      return;
    }
    if (disabled || readOnly || sending || readingImage) {
      imageError =
        "Images cannot be attached right now. Try again when the message box is ready.";
      return;
    }
    if (drop.error) {
      imageError = drop.error;
      return;
    }
    if (
      !drop.token ||
      !Number.isInteger(drop.count) ||
      drop.count < 1 ||
      drop.count > 32
    ) {
      imageError = "Drop between 1 and 32 PNG or JPEG images.";
      return;
    }
    void attachImages(drop.count, (index) => droppedImage(drop.token!, index));
  }

  onMount(() => {
    void listenImageDrops(nativeDrop)
      .then((off) => {
        if (destroyed) off();
        else unlistenImages = off;
      })
      .catch(() => {
        if (!destroyed && nativeImageInput())
          imageError =
            "Image drops are unavailable. Try pasting an image instead.";
      });
  });
  $: if (autoFocus) focusPending = true;

  export function focus() {
    if (field && !disabled) field.focus({ preventScroll: true });
  }

  /**
   * What the last measurement was taken against: the text *and* the field width.
   *
   * Both matter. Text alone misses reflow — the same draft wraps onto more lines
   * in a narrower window, so the field must grow without another keystroke. Width
   * alone misses an external clear. Keying on the pair, and skipping a write when
   * the key is unchanged, is also what stops the observer from reacting to its own
   * height writes: writing the height does not change `clientWidth`.
   */
  let measured = "\u0000";

  /**
   * Grow the field with its content, capped at the old 10rem ceiling, then let it
   * scroll. Height is reset to `auto` first because re-measuring from the current
   * height can only grow a field, so a shortened or cleared draft would never
   * shrink it.
   *
   * `scrollHeight` is trusted as the measurement. An environment with no layout
   * reports 0, which leaves the field's natural single-line height instead of
   * writing an inline `0px` that would hide it.
   */
  function measure() {
    if (!field) return;
    const key = `${field.clientWidth}|${field.value}`;
    if (measured === key) return;
    measured = key;
    field.style.height = "auto";
    const natural = field.scrollHeight;
    if (natural > 0) field.style.height = `${Math.min(natural, 160)}px`;
  }

  /** Covers the reader's own typing. */
  function oninput(event: Event) {
    draft = withImages(
      showRequestText
        ? (event.target as HTMLTextAreaElement).value
        : withStudyRequests(
            (event.target as HTMLTextAreaElement).value,
            studyDraft.requests,
          ),
      visibleDraft.images,
    );
    measure();
    dismissedSlash = "";
    syncSkillQuery();
  }

  /**
   * Covers text changed from outside this component — above all the
   * coordinator's acknowledged-send clear, which writes the `draft` prop rather
   * than typing. `afterUpdate` runs after Svelte has flushed the new value into
   * the DOM, so it sees the cleared text; a reactive statement would still be
   * looking at the previous value.
   */
  afterUpdate(() => {
    measure();
    if (focusPending && autoFocus && !disabled && !readOnly && !sending) {
      focusPending = false;
      focus();
    }
  });

  $: if (field) {
    // Establish the measurement once the element is bound.
    void draft;
    measure();
  }

  $: if (field) {
    // Reflow: a width change with unchanged text still changes how many lines the
    // draft occupies. The field's width follows the reading column, so observing
    // the field is enough. `measure()` skips its own height writes (the key
    // includes only width and text), so this cannot feed back into itself.
    sizeObserver?.disconnect();
    sizeObserver =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => measure())
        : undefined;
    sizeObserver?.observe(field);
  }

  function keydown(event: KeyboardEvent) {
    if (!guard.composing && skills?.keydown(event)) return;
    const action = composerAction(event, guard.composing);
    if (action !== "submit") return;
    // Bare Enter submits; Shift+Enter keeps the textarea's native newline.
    event.preventDefault();
    submit();
  }

  function submit() {
    if (
      disabled ||
      readOnly ||
      sending ||
      readingImage ||
      messageTooLarge ||
      skillQuery !== null
    )
      return;
    if (!draft.trim()) return;
    onSend();
  }

  onDestroy(() => {
    destroyed = true;
    unlistenImages?.();
    sizeObserver?.disconnect();
    guard.destroy();
  });
</script>

<svelte:window
  on:dragover={preventFileNavigation}
  on:drop={preventFileNavigation}
  on:blur={() => (dragDepth = 0)}
/>

<form
  bind:this={form}
  class:dragging={dragDepth > 0}
  on:dragenter={(event) => {
    if (isFileDrag(event)) {
      event.preventDefault();
      dragDepth++;
    }
  }}
  on:dragleave={(event) => {
    if (isFileDrag(event)) dragDepth = Math.max(0, dragDepth - 1);
  }}
  on:dragover={dragOver}
  on:drop={drop}
  class:empty
  on:focusin={() => (focused = true)}
  on:focusout={(event) => {
    // Keep the hint in place while clicking a composer control. Collapsing it
    // between pointer-down and click would move that control out from under the pointer.
    focused =
      event.relatedTarget instanceof Node &&
      !!form?.contains(event.relatedTarget);
  }}
  on:submit|preventDefault={submit}
  aria-label="Message composer"
>
  {#if !showRequestText}
    {#each studyDraft.requests as study, index}
      <StudyRequestCard
        request={study}
        onOpen={onOpenStudy}
        disabled={disabled || readOnly || sending}
        onEdit={() => {
          showRequestText = true;
          focus();
        }}
        onRemove={() =>
          (draft = withImages(
            withStudyRequests(
              studyDraft.text,
              studyDraft.requests.filter((_, at) => at !== index),
            ),
            visibleDraft.images,
          ))}
      />
    {/each}
  {:else if studyDraft.requests.length}
    <button
      type="button"
      class="request-view"
      on:click={() => (showRequestText = false)}>Show source card</button
    >
  {/if}
  <div class="search-field" class:blank={!draft} class:focused>
    {#if focused && !draft && !disabled && !readOnly && !sending}
      <span class="terminal-caret" aria-hidden="true"></span>
    {/if}
    <textarea
      aria-label="Message"
      aria-describedby="composer-image-limits"
      aria-autocomplete="list"
      aria-activedescendant={activeOptionId}
      aria-controls={skillQuery !== null ? "composer-skills" : undefined}
      rows="1"
      {disabled}
      aria-disabled={disabled ? "true" : "false"}
      bind:this={field}
      value={showRequestText ? visibleDraft.text : studyDraft.text}
      on:input={oninput}
      on:paste={paste}
      on:keydown={keydown}
      on:keyup={(event) => {
        if (
          !["Escape", "Enter", "Tab", "ArrowUp", "ArrowDown"].includes(
            event.key,
          )
        )
          syncSkillQuery();
      }}
      on:click={syncSkillQuery}
      on:compositionstart={() => guard.start()}
      on:compositionend={() => guard.end()}
    ></textarea>
  </div>
  <ImageGallery
    onViewerChange={onImageViewer}
    compact
    images={visibleDraft.images.map((src, index) => ({
      id: `draft-${index}`,
      src,
    }))}
    disabled={disabled || readOnly || sending || readingImage}
    onRemove={(index) =>
      (draft = withImages(
        visibleDraft.text,
        visibleDraft.images.filter((_, i) => i !== index),
      ))}
  />
  {#if dragDepth > 0}<p class="drop-hint">
      Drop PNG or JPEG images to attach
    </p>{/if}
  <p
    id="composer-image-limits"
    class="image-limits"
    hidden={!visibleDraft.images.length && !dragDepth}
  >
    {#if maximumMessageBytes !== null}
      {visibleDraft.images.length
        ? `${visibleDraft.images.length} image${visibleDraft.images.length === 1 ? "" : "s"} · ${byteSize(visibleDraft.images.reduce((total, image) => total + imageBytes(image), 0))} total. `
        : "PNG/JPEG · "}
      About {byteSize(
        imageAllowance(
          visibleDraft.text,
          Math.max(1, visibleDraft.images.length),
          maximumMessageBytes,
        ),
      )} total with this text · 1.4 MB each · 4096 × 4096 px.
    {:else}Image limits are loading…{/if}
  </p>
  {#if readingImage}<p class="image-limits" role="status">
      {imageProgress}
    </p>{/if}
  {#if imageError}<p class="image-error" role="alert">{imageError}</p>{/if}
  {#if messageTooLarge}<p class="image-error" role="alert">
      This message is too large. Remove an image or shorten the text before
      sending.
    </p>{/if}
  <div class="composer-tools">
    <SkillChooser
      bind:this={skills}
      bind:activeOptionId
      bind:selectedSkill
      anchor={field}
      query={skillQuery}
      disabled={disabled || readOnly || sending}
      onChoose={chooseSkill}
      onDismiss={() => {
        dismissedSlash = field?.value.slice(slashStart, slashEnd) ?? "";
        skillQuery = null;
      }}
    />
    <button
      class="send"
      type="submit"
      aria-label="Send message"
      title="Send message · Enter"
      disabled={disabled ||
        readOnly ||
        sending ||
        readingImage ||
        messageTooLarge ||
        skillQuery !== null ||
        !draft.trim()}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        aria-hidden="true"><path d="m5 12 7-7 7 7M12 5v14" /></svg
      >
    </button>
  </div>
</form>

<style>
  .request-view {
    border: 0;
    background: none;
    color: var(--ke-text-secondary);
    font: 11px/1.6 var(--ke-font-ui);
    text-align: left;
    cursor: pointer;
  }

  .image-limits {
    margin: 0;
    color: var(--ke-text-tertiary);
    font-size: var(--ke-text-2xs);
    line-height: 1.5;
  }
  .image-limits[hidden] {
    display: none;
  }
  form.dragging {
    outline: 1px dashed var(--ke-text-secondary);
    outline-offset: 6px;
  }
  .drop-hint {
    margin: 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .image-error {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
    margin: 0;
  }
  .composer-tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 28px;
  }
  .composer-tools button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-tertiary);
    cursor: pointer;
  }
  .composer-tools .send {
    margin-left: auto;
  }
  .composer-tools button:hover {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .composer-tools button:disabled {
    opacity: 0.3;
    cursor: default;
  }
  .composer-tools button:focus-visible {
    outline: 2px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  /* The journal's `.search-form`: a full-measure column under a hairline, with
     1.15rem of air above the border. No bottom padding and no background — it is
     the column's last child, not a pinned footer, so the reading column's own
     bottom padding is the only gap below it. */
  form {
    position: relative;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 1.15rem 0 0;
    border-top: 1px solid var(--ke-border);
  }
  form.empty {
    border-top: 0;
    padding-top: 0;
  }
  .search-field {
    position: relative;
    display: flex;
    font: var(--ke-chat-user-font);
  }
  .search-field textarea {
    width: 100%;
  }
  .search-field.blank.focused:has(.terminal-caret) textarea {
    caret-color: transparent;
  }
  /* Original QuestionField caret, including its reduced-motion behaviour. */
  .terminal-caret {
    position: absolute;
    left: 0;
    top: 0.4rem;
    width: 1.5px;
    height: 1.15em;
    background: var(--ke-text-primary);
    pointer-events: none;
    animation: caret-blink 2.2s step-end infinite;
  }
  @keyframes caret-blink {
    0%,
    55%,
    100% {
      opacity: 1;
    }
    56%,
    99% {
      opacity: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .terminal-caret {
      animation: none;
    }
  }
  /* Old composer idiom: no frame, no fill, no resize grip — the text is the
     control. The wrapper supplies the single hairline. */
  textarea {
    min-width: 0;
    max-height: 10rem;
    padding: var(--ke-space-1) 0;
    border: none;
    border-radius: 0;
    resize: none;
    overflow-y: auto;
    background: transparent;
    color: var(--ke-text-primary);
    font: inherit;
    caret-color: var(--ke-text-primary);
  }
  textarea:focus {
    outline: none;
  }
  textarea:disabled {
    opacity: 0.6;
  }
</style>
