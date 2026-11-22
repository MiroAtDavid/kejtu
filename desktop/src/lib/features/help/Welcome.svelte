<script lang="ts">
  import { tick } from "svelte";
  import { chooseFolder, request } from "$lib/api/client";
  import type {
    FilesStatus,
    KeyStatus,
    McpStatus,
    ModelSettings,
  } from "$lib/api/wire.generated";
  import Icon from "$lib/design/Icon.svelte";
  import { saveFiles, workingFolderSetup } from "../settings/files";
  import {
    knownConnectionName,
    setUpIncludedTools,
    type IncludedTool,
  } from "../settings/connections";

  /** Shown while getting-started is offered and Settings is closed. Opening
   *  Settings from a step hides it; it returns on the same step. */
  export let open = false;
  /** Another change is running or the getting-started state is uncertain. */
  export let busy = false;
  /** The getting-started state could not be changed or read. */
  export let error = "";
  /** The guided conversation can start (no unsent message). */
  export let canTour = false;
  export let onModels: () => void = () => {};
  export let onHelp: () => void = () => {};
  export let onTour: () => void = () => {};
  export let onDone: () => void = () => {};
  export let onSkip: () => void = () => {};
  export let onRefresh: () => void = () => {};

  const STEPS = 5;
  const STEP_DOTS = [0, 1, 2, 3, 4];
  let step = 0;
  let dialog: HTMLDialogElement | undefined;
  let heading: HTMLElement | undefined;

  let models: ModelSettings | null = null;
  let modelsError = "";
  let files: FilesStatus | null = null;
  let filesError = "";
  let workspace = "";
  let folderBusy = false;
  let folderNote = "";
  let folderUnknown = false;
  let createdHere = false;

  // The tools that come with Kejtu, offered once on their own step. Quick
  // Terminal (Linux) is offered unticked; Terminal Use needs system Node.js and
  // stays in the Shelf.
  const linux =
    typeof navigator !== "undefined" && /Linux/i.test(navigator.userAgent);
  const windows =
    typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent);
  const TOOLS: IncludedTool[] = [
    "playwright",
    "pdfcraft",
    "gridcraft",
    "wordcraft",
    ...(linux ? (["quick-terminal"] as const) : []),
  ];
  let tools: McpStatus | null = null;
  let toolsError = "";
  let toolsBusy = false;
  let toolsTried = false;
  let toolsFailures: string[] = [];
  let ticked: Record<string, boolean> = {
    playwright: true,
    pdfcraft: true,
    gridcraft: true,
    wordcraft: true,
    "quick-terminal": false,
  };

  const message = (caught: unknown) =>
    caught instanceof Error ? caught.message : String(caught);

  $: if (dialog) void sync(open);
  async function sync(show: boolean) {
    if (!dialog) return;
    if (show && !dialog.open) {
      dialog.showModal();
      void refresh();
      await tick();
      heading?.focus({ preventScroll: true });
    } else if (!show && dialog.open) dialog.close();
  }

  // Read again each time the window returns, so a key saved or a folder set up
  // in Settings shows here at once.
  async function refresh() {
    const [modelRead, filesRead, toolsRead] = await Promise.allSettled([
      request("models.read", {}),
      request("files.status", {}),
      request("mcp.status", {}),
    ]);
    if (toolsRead.status === "fulfilled") {
      if (!toolsBusy) tools = toolsRead.value;
      toolsError = "";
    } else toolsError = message(toolsRead.reason);
    if (modelRead.status === "fulfilled") {
      models = modelRead.value;
      modelsError = "";
    } else modelsError = message(modelRead.reason);
    if (filesRead.status === "fulfilled") {
      files = filesRead.value;
      filesError = "";
      if (!workspace) workspace = files.suggested_workspace;
    } else filesError = message(filesRead.reason);
  }

  async function go(next: number) {
    step = next;
    await tick();
    heading?.focus({ preventScroll: true });
  }

  const KEYS: Record<KeyStatus, string> = {
    saved: "Ready",
    session: "Ready",
    not_required: "No key needed",
    missing: "No key yet",
    loading: "Checking…",
    unavailable: "Key can’t be checked",
  };
  $: provider = models
    ? (models.providers.find((item) => item.id === models?.active)?.label ??
      models.active)
    : "";
  $: modelName = models ? models.profiles[models.active].model : "";
  $: keyStatus = models ? models.key_statuses[models.active] : null;
  $: modelReady =
    keyStatus === "saved" ||
    keyStatus === "session" ||
    keyStatus === "not_required";

  $: folderSet = !!files?.configured && !!files.workspace;
  $: canCreate =
    !!files && !files.configured && files.runtime_available && !folderUnknown;
  function shown(path: string) {
    const home = files?.home.replace(/\/$/, "");
    return home && path.startsWith(`${home}/`)
      ? `~${path.slice(home.length)}`
      : path;
  }
  $: folderLine = !files
    ? filesError || "Checking…"
    : folderSet
      ? createdHere
        ? "Created. Change it any time in Settings → Files."
        : "Already set up. Change it any time in Settings → Files."
      : !files.runtime_available
        ? "The Files component is missing. Repair Kejtu to set this up."
        : files.configured
          ? "No working folder chosen. Set one in Settings → Files."
          : folderUnknown
            ? "Open Settings → Files to check this folder."
            : shown(workspace).startsWith("~/")
              ? "A new folder in your home folder"
              : "A new folder in the place you chose";

  async function choosePlace() {
    if (!canCreate || folderBusy || busy) return;
    try {
      const parent = await chooseFolder(workspace);
      if (parent) workspace = `${parent.replace(/\/$/, "")}/Kejtu`;
    } catch (caught) {
      folderNote = message(caught);
    }
  }
  async function createFolder() {
    if (!files || !canCreate || folderBusy || busy) return;
    folderBusy = true;
    folderNote = "";
    try {
      files = await saveFiles(workingFolderSetup(files, workspace), (read) => {
        // The save may have landed with its answer lost: show what is saved,
        // and offer nothing more when that cannot be read.
        if (read) files = read;
        else folderUnknown = true;
      });
      createdHere = !!files.workspace;
    } catch (caught) {
      folderNote = message(caught);
      createdHere = !!files?.configured && !!files.workspace;
    } finally {
      folderBusy = false;
    }
    // The pressed button is gone now; keep keyboard focus in the window.
    await tick();
    dialog?.querySelector<HTMLButtonElement>(".footer .primary")?.focus();
  }

  $: where = folderSet ? "your Kejtu folder" : "folders you share in Files";
  $: toolText = {
    playwright: "Opens and reads web pages in a private window",
    pdfcraft: `Reads and edits PDFs in ${where}`,
    gridcraft: `Reads and edits spreadsheets in ${where}`,
    wordcraft: `Reads and edits Word files in ${where}`,
    "quick-terminal": "Runs commands on this computer",
  } as Record<string, string>;
  $: toolRows = TOOLS.map((name) => ({
    name,
    title: knownConnectionName(name) ?? name,
    text: toolText[name],
    row: tools?.servers.find((item) => item.id === name),
  }));
  // Tools not set up yet can be ticked until Turn on has run once. Anything
  // already set up keeps the choice made in the Shelf.
  $: choosing = !!tools && !toolsTried && toolRows.some((item) => !item.row);
  $: chosen = choosing
    ? toolRows
        .filter((item) => !item.row && ticked[item.name])
        .map((item) => item.name)
    : [];
  async function turnOn() {
    if (!tools || !chosen.length || toolsBusy || busy) return;
    toolsBusy = true;
    toolsFailures = [];
    let unknown = false;
    try {
      const failures = await setUpIncludedTools({
        names: chosen,
        saved: tools,
        sharedFolders: !windows && !!files?.enabled,
        status: (value) => {
          if (value) tools = value;
          else unknown = true;
        },
      });
      toolsFailures = failures.map(
        (failure) =>
          `${knownConnectionName(failure.name) ?? failure.name}: ${failure.message}`,
      );
    } catch (caught) {
      toolsFailures = [message(caught)];
    }
    // An unconfirmed save is read back once, so the rows show what is saved.
    if (unknown) {
      try {
        tools = await request("mcp.status", {});
      } catch (caught) {
        tools = null;
        toolsError = message(caught);
      }
    }
    toolsTried = true;
    toolsBusy = false;
    // The pressed button is gone now; keep keyboard focus in the window.
    await tick();
    dialog?.querySelector<HTMLButtonElement>(".footer .primary")?.focus();
  }

  $: last = step === STEPS - 1;
  function closed() {
    // Browsers may close a modal after repeated Escape presses even when the
    // cancel is refused. While it should be shown, show it again.
    void tick().then(() => {
      if (open && dialog && !dialog.open) dialog.showModal();
    });
  }
  function cancel(event: Event) {
    // Escape matches ×: skip on earlier steps, finish on the last one.
    event.preventDefault();
    if (busy || folderBusy || toolsBusy) return;
    if (last) onDone();
    else onSkip();
  }
  $: summaryModel = !models
    ? "Not checked"
    : modelReady
      ? `${provider}, ${keyStatus === "not_required" ? "no key needed" : "ready"}`
      : "Not yet — add one in Settings → Models";
  $: summaryFolder =
    folderSet && files?.workspace
      ? shown(files.workspace)
      : "Not now — Settings → Files";
  $: toolsOn = toolRows
    .filter((item) => item.row?.enabled)
    .map((item) => item.title);
  $: summaryTools = !tools
    ? "Not checked"
    : toolsOn.length
      ? toolsOn.join(", ")
      : "Not now — Shelf → Installed";
</script>

<dialog
  bind:this={dialog}
  class="welcome"
  aria-labelledby="welcome-title"
  on:cancel={cancel}
  on:close={closed}
>
  <div class="top">
    <h2 id="welcome-title" bind:this={heading} tabindex="-1">
      {step === 0
        ? "Welcome to Kejtu"
        : step === 1
          ? "Choose a model"
          : step === 2
            ? "A folder for Kejtu?"
            : step === 3
              ? "Tools for Kejtu?"
              : "Help is always close"}
    </h2>
    <button
      type="button"
      class="close"
      aria-label="Close"
      disabled={busy || folderBusy || toolsBusy}
      on:click={last ? onDone : onSkip}><Icon name="close" size={16} /></button
    >
  </div>

  {#if step === 0}
    <p class="text">
      A calm place for your conversations, notes and plans. Three small choices,
      and Kejtu is ready for you.
    </p>
  {:else if step === 1}
    <p class="text">
      Kejtu needs a model to write its replies. Choose one in Settings, and add
      its key there — never in a chat.
    </p>
    <div class="row">
      {#if modelReady}<span class="ok"><Icon name="check" size={18} /></span
        >{:else}<span class="pending" aria-hidden="true"></span>{/if}
      <div class="row-text">
        {#if models}
          <strong>{provider}</strong>
          <span>{modelName} · for new conversations</span>
        {:else}
          <span>{modelsError || "Checking…"}</span>
        {/if}
      </div>
      {#if keyStatus}<span class="state">{KEYS[keyStatus]}</span>{/if}
    </div>
  {:else if step === 2}
    <p class="text">
      Files Kejtu makes for you go here, so the parts that need a folder later
      just work. Your other folders stay private until you share them.
    </p>
    <div class="row">
      {#if folderSet}<span class="ok"><Icon name="check" size={18} /></span
        >{:else}<span class="folder"><Icon name="folder" size={20} /></span
        >{/if}
      <div class="row-text">
        {#if files}<strong
            >{shown(
              folderSet && files.workspace ? files.workspace : workspace,
            )}</strong
          >{/if}
        <span>{folderLine}</span>
      </div>
      {#if canCreate || folderSet}<span class="state">Read &amp; write</span
        >{/if}
    </div>
    {#if canCreate}<button
        type="button"
        class="link"
        disabled={busy || folderBusy}
        on:click={choosePlace}>Choose another place…</button
      >{/if}
    {#if folderNote}<p class="error" role="alert">{folderNote}</p>{/if}
  {:else if step === 3}
    <p class="text">
      Kejtu uses these only when a conversation needs them. Each keeps its own
      switch in Shelf → Installed.
    </p>
    {#if tools}
      <div class="tools">
        {#each toolRows as item (item.name)}
          {#if choosing && !item.row}
            <label class="tool choice">
              <input
                type="checkbox"
                bind:checked={ticked[item.name]}
                disabled={busy || toolsBusy}
              />
              <span class="row-text">
                <strong>{item.title}</strong>
                <span>{item.text}</span>
              </span>
            </label>
          {:else}
            <div class="tool">
              {#if item.row?.enabled}<span class="ok"
                  ><Icon name="check" size={18} /></span
                >{:else}<span class="pending" aria-hidden="true"></span>{/if}
              <span class="row-text">
                <strong>{item.title}</strong>
                <span>{item.text}</span>
              </span>
              <span class="state">{item.row?.enabled ? "On" : "Off"}</span>
            </div>
          {/if}
        {/each}
      </div>
    {:else}
      <div class="row">
        <span class="pending" aria-hidden="true"></span>
        <div class="row-text"><span>{toolsError || "Checking…"}</span></div>
      </div>
    {/if}
    {#each toolsFailures as failure}<p class="error" role="alert">
        {failure}
      </p>{/each}
  {:else}
    <p class="text">
      Settings → Help explains each part of Kejtu, whenever you need it. That’s
      all — enjoy.
    </p>
    <dl class="summary">
      <dt>Model</dt>
      <dd>{summaryModel}</dd>
      <dt>Folder</dt>
      <dd>{summaryFolder}</dd>
      <dt>Tools</dt>
      <dd>{summaryTools}</dd>
    </dl>
    {#if modelReady && canTour}<button
        type="button"
        class="link"
        disabled={busy}
        on:click={onTour}>Or let Kejtu show you around in a conversation</button
      >{/if}
  {/if}

  {#if error}<p class="error" role="alert">
      Getting started: {error}
      <button type="button" class="link" on:click={onRefresh}>Refresh</button>
    </p>{/if}

  <div class="footer">
    <span class="dots" role="img" aria-label={`Step ${step + 1} of ${STEPS}`}>
      {#each STEP_DOTS as index}<span class:current={index === step}
        ></span>{/each}
    </span>
    <div class="actions">
      {#if step === 0}
        <button type="button" class="quiet" disabled={busy} on:click={onSkip}
          >Skip setup</button
        >
        <button
          type="button"
          class="primary"
          disabled={busy}
          on:click={() => go(1)}>Let’s begin</button
        >
      {:else if step === 1}
        {#if modelReady}
          <button
            type="button"
            class="quiet"
            disabled={busy}
            on:click={onModels}>Change</button
          >
          <button
            type="button"
            class="primary"
            disabled={busy}
            on:click={() => go(2)}>Continue</button
          >
        {:else}
          <button
            type="button"
            class="quiet"
            disabled={busy}
            on:click={() => go(2)}>Later</button
          >
          <button
            type="button"
            class="primary"
            disabled={busy}
            on:click={onModels}>Open Models</button
          >
        {/if}
      {:else if step === 2}
        {#if canCreate}
          <button
            type="button"
            class="quiet"
            disabled={busy || folderBusy}
            on:click={() => go(3)}>Not now</button
          >
          <button
            type="button"
            class="primary"
            disabled={busy || folderBusy}
            on:click={createFolder}
            >{folderBusy ? "Creating…" : "Create folder"}</button
          >
        {:else}
          <button
            type="button"
            class="primary"
            disabled={busy || folderBusy || (!files && !filesError)}
            on:click={() => go(3)}>Continue</button
          >
        {/if}
      {:else if step === 3}
        <!-- Rows leave the choice as they are set up; the button stays until done. -->
        {#if chosen.length || toolsBusy}
          <button
            type="button"
            class="quiet"
            disabled={busy || toolsBusy}
            on:click={() => go(4)}>Not now</button
          >
          <button
            type="button"
            class="primary"
            disabled={busy || toolsBusy}
            on:click={turnOn}>{toolsBusy ? "Turning on…" : "Turn on"}</button
          >
        {:else}
          <button
            type="button"
            class="primary"
            disabled={busy || toolsBusy || (!tools && !toolsError)}
            on:click={() => go(4)}>Continue</button
          >
        {/if}
      {:else}
        <button type="button" class="quiet" disabled={busy} on:click={onHelp}
          >Open Help</button
        >
        <button type="button" class="primary" disabled={busy} on:click={onDone}
          >Done</button
        >
      {/if}
    </div>
  </div>
</dialog>

<style>
  .welcome {
    width: min(460px, calc(100vw - 40px));
    max-height: calc(100vh - 48px);
    overflow: auto;
    box-sizing: border-box;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-overlay-radius);
    padding: 26px;
    color: var(--ke-text-primary);
    background: var(--ke-surface);
    box-shadow: var(--ke-overlay-shadow);
    animation: ke-dialog-in var(--ke-motion-slow) var(--ke-motion-ease);
  }
  .welcome::backdrop {
    background: var(--ke-scrim, rgb(0 0 0 / 0.32));
    animation: ke-fade-in var(--ke-motion-slow, 260ms)
      var(--ke-motion-ease, ease-out);
  }
  .top {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 12px;
  }
  h2 {
    grid-column: 1;
    margin: 0;
    font-size: 21px;
    font-weight: 500;
    letter-spacing: -0.01em;
    outline: none;
  }
  .close {
    grid-column: 2;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border-radius: var(--ke-radius-md);
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .text {
    margin: 10px 0 0;
    font-size: 13.5px;
    line-height: 1.6;
    color: var(--ke-text-secondary);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 20px;
    padding: 12px 14px;
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-inset);
  }
  .tools {
    display: flex;
    flex-direction: column;
    margin-top: 20px;
    border-radius: var(--ke-radius-md);
    background: var(--ke-surface-inset);
  }
  .tool {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
  }
  .tool + .tool {
    border-top: 1px solid
      color-mix(in srgb, var(--ke-border-strong) 55%, transparent);
  }
  .choice {
    cursor: pointer;
  }
  .choice input {
    flex: none;
    width: 16px;
    height: 16px;
    margin: 1px;
    accent-color: var(--ke-accent);
  }
  .row-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .row-text strong {
    font-size: var(--ke-text-sm);
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  .row-text span {
    font-size: var(--ke-text-xs);
    /* Secondary grey is too faint on the inset row at this size. */
    color: var(--ke-text-primary);
  }
  .state {
    flex: none;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-primary);
  }
  .ok {
    display: inline-flex;
    flex: none;
    color: var(--ke-success);
  }
  .folder {
    display: inline-flex;
    flex: none;
    color: var(--ke-text-secondary);
  }
  .pending {
    flex: none;
    box-sizing: border-box;
    width: 16px;
    height: 16px;
    margin: 1px;
    border: 1.4px dashed var(--ke-text-secondary);
    border-radius: 50%;
  }
  .summary {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    row-gap: 8px;
    margin: 20px 0 0;
    font-size: var(--ke-text-sm);
  }
  .summary dt {
    color: var(--ke-text-secondary);
  }
  .summary dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 28px;
  }
  .dots {
    display: flex;
    gap: 6px;
  }
  .dots span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ke-border-strong);
  }
  .dots span.current {
    background: var(--ke-text-primary);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }
  button {
    font: inherit;
    font-size: var(--ke-text-sm);
    border: 0;
    cursor: pointer;
  }
  .primary,
  .quiet {
    min-height: 36px;
    padding: 0 14px;
    border-radius: var(--ke-radius-md);
  }
  .primary {
    padding: 0 16px;
    font-weight: 500;
    background: var(--ke-accent);
    color: var(--ke-on-accent);
  }
  .primary:hover:not(:disabled) {
    background: var(--ke-accent-hover);
  }
  .quiet {
    background: transparent;
    color: var(--ke-text-secondary);
  }
  .quiet:hover:not(:disabled),
  .close:hover:not(:disabled) {
    color: var(--ke-text-primary);
  }
  .link {
    display: block;
    margin-top: 10px;
    min-height: 28px;
    padding: 0 2px;
    background: transparent;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .error {
    margin: 14px 0 0;
    color: var(--ke-danger);
    font-size: var(--ke-text-sm);
    line-height: 1.5;
  }
  .error .link {
    display: inline;
    margin: 0 0 0 4px;
  }
  button:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
