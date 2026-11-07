<script lang="ts">
  import Help from "../help/Help.svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { choiceSelect } from "$lib/design/choice-select";
  import { pageFade } from "$lib/design/page-fade";
  import { navIndicator } from "$lib/design/nav-indicator";
  import Files from "../settings/Files.svelte";
  import Skills from "../settings/Skills.svelte";
  import Connections from "../settings/Connections.svelte";
  import "$lib/features/settings/settings.css";
  import ContextAccounting from "$lib/features/development/ContextAccounting.svelte";
  import Background from "$lib/features/background/Background.svelte";
  import PrivacyAccess from "$lib/features/settings/PrivacyAccess.svelte";
  import WebSettings from "$lib/features/web/WebSettings.svelte";
  import Models from "$lib/features/settings/Models.svelte";
  import Appearance from "$lib/features/settings/Appearance.svelte";
  import Execution from "$lib/features/settings/Execution.svelte";
  import type { Inspection, Profile } from "$lib/api/wire.generated";

  /**
   * Secondary view: operational controls and diagnostics.
   *
   * The frame is adapted from the old settings surface
   * (`features/settings/SettingsPage.svelte` markup ~172-190 and
   * `settings.css` 1-60): a left navigation column and a scrolling content
   * column. Only the chrome is reused — none of the old settings feature panels.
   *
   * Each panel owns its settings and recovery. This component decides where
   * they live; it grants nothing by rendering them. Privacy & access is the
   * single ordinary editor for People, Reminders and task permissions.
   *
   * Two rules are load-bearing:
   *
   * 1. A panel's controller is mounted only once the reader has actually opened
   *    that panel, so entering Operations does not by itself read task, job or
   *    reminder state.
   * 2. Once mounted, a panel stays mounted (hidden, never destroyed) and the
   *    navigation refuses to switch while either permission or note-removal lock is set. ReminderAccess
   *    clears its own lock in `onDestroy`, so unmounting it mid-change would
   *    silently unlock sending and take the recovery controls off screen.
   */
  export let stateId = "";
  export let active = true;
  export let personalTaskLock = false;
  export let showWorkspace = 0;
  export let workspaceLocked = false;
  export let researchLock = false;
  export let backgroundLock = false;
  export let showAccess = 0;
  export let showHelp = 0;
  export let canAskHelp = false;
  export let onAskHelp: (text: string) => void = () => {};
  export let selected = "";
  export let launchProfile: Profile | null = null;
  export let identityLine = "";
  export let capabilities = "";
  export let ownerBusy = false;
  export let reminderLock = false;
  export let peopleLock = false;
  export let accessLock = false;
  export let accessBusy = false;
  export let recoverAccess = false;
  export let webLock = false;
  export let webBusy = false;
  export let recoverWeb = false;
  export let modelsBusy = false;
  export let onModelsChanged: () => Promise<void> = async () => {};
  export let notebookLock = false;
  export let showNotes = false;
  export let inspectTurnId = "";
  export let inspection: {
    items: Inspection[];
    next_cursor: string | null;
  } | null = null;
  export let onMoreInspect: () => void = () => {};
  /** Maintenance actions the journal keeps out of its topbar. */
  export let canRefresh = false;
  export let canEarlier = false;
  export let canInspect = false;
  export let onInspectLatest: () => void = () => {};
  export let earlierLoading = false;
  export let onRefresh: () => void = () => {};
  export let onEarlier: () => void = () => {};

  type Panel =
    | "overview"
    | "help"
    | "workspace"
    | "skills"
    | "appearance"
    | "notes"
    | "conversation"
    | "models"
    | "execution"
    | "files"
    | "connections"
    | "web"
    | "diagnostics"
    | "access"
    | "inspection"
    | "background";

  let panel: Panel = recoverWeb ? "web" : recoverAccess ? "access" : "overview";
  /** Panels the reader has opened at least once; these stay mounted. */
  let visited = new Set<Panel>([panel]);
  let expandedGroup = "";
  let revealedPanel: Panel | undefined;

  // Explicit diagnostic inspection opens its panel, away from the chat.
  $: if (inspectTurnId) select("inspection");
  $: if (recoverAccess && !webLock && !visited.has("access")) select("access");
  $: if (showHelp) select("help");
  $: if (showNotes) select("notes");
  $: if (showAccess) select("access");
  $: if (showWorkspace) select("workspace");
  $: locked =
    reminderLock ||
    notebookLock ||
    accessLock ||
    peopleLock ||
    webLock ||
    modelsBusy ||
    researchLock ||
    backgroundLock ||
    personalTaskLock ||
    workspaceLocked;

  function select(next: Panel) {
    // The guard lives in the handler as well as on `disabled`: a disabled control
    // still delivers a dispatched click to its listener, and this must hold for
    // real input and for programme-driven clicks alike.
    if (
      (reminderLock ||
        notebookLock ||
        accessLock ||
        peopleLock ||
        webLock ||
        modelsBusy ||
        researchLock ||
        backgroundLock ||
        personalTaskLock ||
        workspaceLocked) &&
      next !== panel
    )
      return;
    visited.add(next);
    visited = visited;
    panel = next;
  }

  $: groups = [
    {
      title: "Everyday",
      icon: "home",
      items: [
        {
          id: "appearance" as const,
          label: "Appearance",
          hint: "Theme and reading comfort",
        },
        {
          id: "models" as const,
          label: "Models",
          hint: "Choose and connect an AI model",
        },
        {
          id: "web" as const,
          label: "Web",
          hint: "Search providers and web access",
        },
      ],
    },
    {
      title: "Skills & connections",
      icon: "book",
      items: [
        {
          id: "execution" as const,
          label: "Tools and time",
          hint: "How much work a message can do",
        },
        {
          id: "skills" as const,
          label: "Skills",
          hint: "Reusable instructions and resources",
        },
        {
          id: "connections" as const,
          label: "Connections",
          hint: "Browser and other connected tools",
        },
      ],
    },
    {
      title: "Planning",
      icon: "calendar",
      items: [
        {
          id: "workspace" as const,
          label: "Workspace",
          hint: "Projects, calendar and widgets",
        },
        ...(launchProfile
          ? [
              {
                id: "background" as const,
                label: "Background work",
                hint: "Notebook tidy, reflection and advanced jobs",
              },
            ]
          : []),
      ],
    },
    ...(selected
      ? [
          {
            title: "This conversation",
            icon: "comment",
            items: [
              {
                id: "notes" as const,
                label: "Notes",
                hint: "Review and manage saved notes",
              },
              {
                id: "conversation" as const,
                label: "Conversation",
                hint: "Earlier messages and reading controls",
              },
            ],
          },
        ]
      : []),
    {
      title: "Privacy & access",
      icon: "folder",
      items: [
        {
          id: "access" as const,
          label: "Permissions & recall",
          hint: "People, reminders, tasks and preference recall",
        },
        {
          id: "files" as const,
          label: "Files",
          hint: "Working folder and shared file access",
        },
      ],
    },
    {
      title: "Help",
      icon: "book",
      items: [
        {
          id: "help" as const,
          label: "Guide",
          hint: "Start here, explore and find answers",
        },
        {
          id: "diagnostics" as const,
          label: "Diagnostics",
          hint: "App and profile information",
        },
        ...(inspection
          ? [
              {
                id: "inspection" as const,
                label: "Turn inspection",
                hint: "Inspect a saved reply’s accounting",
              },
            ]
          : []),
      ],
    },
  ];
  $: activeGroup =
    groups.find((group) => group.items.some((item) => item.id === panel))
      ?.title ?? "Preferences and tools";
  // Direct entry (for example recovery or inspection) reveals its location.
  // Folding navigation never mounts/unmounts a controller or changes its lock.
  $: if (panel !== revealedPanel) {
    revealedPanel = panel;
    expandedGroup = panel === "overview" ? "" : activeGroup;
  }
</script>

<div class="operations">
  <aside class="operations-nav" use:navIndicator={"nav button.selected"}>
    <h1>Settings</h1>
    <p class="nav-caption">Preferences and tools</p>
    <nav aria-label="Operations sections">
      <button
        type="button"
        class:selected={panel === "overview"}
        aria-current={panel === "overview" ? "page" : undefined}
        disabled={locked && panel !== "overview"}
        on:click={() => select("overview")}>Overview</button
      >
      {#each groups as group, index (group.title)}
        <div class="nav-group">
          <button
            type="button"
            class="group-toggle"
            aria-expanded={expandedGroup === group.title}
            aria-controls={`settings-group-${index}`}
            on:click={() =>
              (expandedGroup =
                expandedGroup === group.title ? "" : group.title)}
            >{group.title}<span aria-hidden="true"
              >{expandedGroup === group.title ? "⌄" : "›"}</span
            ></button
          >
          <div
            class="group-items"
            id={`settings-group-${index}`}
            hidden={expandedGroup !== group.title}
          >
            {#each group.items as item (item.id)}
              <button
                type="button"
                class:selected={panel === item.id}
                aria-current={panel === item.id ? "page" : undefined}
                disabled={locked && item.id !== panel}
                on:click={() => select(item.id)}>{item.label}</button
              >
            {/each}
          </div>
        </div>
      {/each}
    </nav>
    <label class="mobile-sections"
      >Settings section<select
        use:choiceSelect={panel}
        aria-label="Settings section"
        value={panel}
        disabled={locked}
        on:change={(event) => select(event.currentTarget.value as Panel)}
      >
        <option value="overview">Overview</option>
        {#each groups as group (group.title)}<optgroup label={group.title}
            >{#each group.items as item (item.id)}<option value={item.id}
                >{item.label}</option
              >{/each}</optgroup
          >{/each}
      </select></label
    >
  </aside>

  <div class="operations-content">
    <div class="operations-inner" use:pageFade={active ? panel : null}>
      <p class="section-context">{activeGroup}</p>
      <div
        class="settings-page settings-overview"
        hidden={panel !== "overview"}
      >
        <h2 class="settings-title">Your Kejtu</h2>
        <p class="overview-intro">
          Choose how Kejtu looks, works and uses your information.
        </p>
        <div class="overview-grid">
          {#each groups as group (group.title)}
            <section
              class="overview-group"
              aria-label={`${group.title} settings`}
            >
              <h3>
                <span class="group-icon"
                  ><Icon name={group.icon} size={18} /></span
                >{group.title}
              </h3>
              {#each group.items as item (item.id)}
                <button
                  type="button"
                  aria-label={`Open ${item.label}`}
                  disabled={locked}
                  on:click={() => select(item.id)}
                  ><span
                    ><strong>{item.label}</strong><small>{item.hint}</small
                    ></span
                  ><span aria-hidden="true">›</span></button
                >
              {/each}
            </section>
          {/each}
        </div>
      </div>
      {#if visited.has("help")}
        <div class="settings-page" hidden={panel !== "help"}>
          <Help
            canAsk={canAskHelp && !locked}
            onAsk={onAskHelp}
            onSettings={select}
            disabled={locked}
          />
        </div>
      {/if}
      {#if visited.has("workspace")}<div
          class="settings-page"
          hidden={panel !== "workspace"}
        >
          <slot name="workspace-settings" />
        </div>{/if}
      {#if visited.has("skills")}<div
          class="settings-page"
          hidden={panel !== "skills"}
        >
          <Skills {ownerBusy} bind:busy={modelsBusy} />
        </div>{/if}
      {#if visited.has("appearance")}
        <div class="settings-page" hidden={panel !== "appearance"}>
          <Appearance />
        </div>
      {/if}
      {#if visited.has("files")}
        <div class="settings-page" hidden={panel !== "files"}>
          <Files {ownerBusy} bind:busy={modelsBusy} />
        </div>
      {/if}
      {#if visited.has("connections")}
        <div class="settings-page" hidden={panel !== "connections"}>
          <Connections
            {ownerBusy}
            bind:busy={modelsBusy}
            active={active && panel === "connections"}
            onOpenFiles={() => select("files")}
          />
        </div>
      {/if}
      {#if visited.has("execution")}
        <div class="settings-page" hidden={panel !== "execution"}>
          <Execution {ownerBusy} bind:busy={modelsBusy} />
        </div>
      {/if}
      {#if reminderLock}
        <p class="lock-note" role="status">
          A reminder access change has no confirmed outcome. Resolve it in
          Reminders before leaving this panel; sending stays paused until it is
          resolved.
        </p>
      {/if}

      {#if selected && visited.has("notes")}
        <!-- A pending removal owns this mounted panel until its outcome resolves. -->
        <div class="settings-page" hidden={panel !== "notes"}>
          <slot name="notes" openAccess={() => select("access")} />
        </div>
      {/if}

      <div class="settings-page" hidden={panel !== "conversation"}>
        <h2 class="settings-title">Conversation</h2>
        <p class="hint">
          Refresh this conversation, load earlier messages, or inspect the last
          reply.
        </p>
        <div class="actions">
          <button type="button" disabled={!canRefresh} on:click={onRefresh}
            >Refresh conversation</button
          >
          <button
            type="button"
            disabled={!canEarlier || earlierLoading}
            on:click={onEarlier}>Earlier turns</button
          >
          <button
            type="button"
            disabled={!canInspect || locked}
            on:click={onInspectLatest}>Inspect latest turn</button
          >
        </div>
      </div>

      {#if visited.has("models")}
        <div class="settings-page" hidden={panel !== "models"}>
          <Models
            {ownerBusy}
            bind:busy={modelsBusy}
            onChanged={onModelsChanged}
          />
        </div>
      {/if}

      {#if visited.has("web")}
        <div class="settings-page" hidden={panel !== "web"}>
          <h2 class="settings-title">Web</h2>
          {#key stateId}<WebSettings
              {stateId}
              busy={webBusy}
              bind:locked={webLock}
            />{/key}
        </div>
      {/if}
      {#if webLock}<p role="status">
          Resolve the web setting change before leaving Settings or sending.
        </p>{/if}
      <div class="settings-page" hidden={panel !== "diagnostics"}>
        <h2 class="settings-title">Diagnostics</h2>
        <p class="mono">{identityLine}</p>
        <p class="mono">{capabilities}</p>
        <p class="hint">
          Build and profile identity for this session. Nothing here changes a
          saved profile.
        </p>
      </div>

      {#if visited.has("access")}
        <div class="settings-page" hidden={panel !== "access"}>
          {#key stateId}<PrivacyAccess
              {stateId}
              busy={accessBusy}
              bind:locked={accessLock}
              hasConversation={!!selected}
              onOpen={(next) => select(next)}
            />{/key}
        </div>
      {/if}

      {#if inspection && visited.has("inspection")}
        <div class="settings-page" hidden={panel !== "inspection"}>
          <h2 class="settings-title">Turn inspection</h2>
          <p class="hint">
            Recorded accounting for turn {inspectTurnId.slice(0, 8)}. This reads
            what was already stored; it does not run anything.
          </p>
          <ContextAccounting
            items={inspection.items}
            complete={!inspection.next_cursor}
          />
          <pre aria-label="Turn inspection">{JSON.stringify(
              inspection.items,
              null,
              2,
            )}</pre>
          {#if inspection.next_cursor}
            <button type="button" class="plain" on:click={onMoreInspect}
              >More inspection</button
            >
          {/if}
        </div>
      {/if}

      {#if launchProfile && visited.has("background")}
        <div hidden={panel !== "background"}>
          <Background
            {stateId}
            conversationId={selected}
            profile={launchProfile}
            busy={ownerBusy}
            bind:navigationLocked={backgroundLock}
          />
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  /* Old settings frame: fixed navigation column plus scrolling content. */
  .operations {
    display: grid;
    grid-template-columns: 244px minmax(0, 1fr);
    min-height: 0;
    flex: 1;
    background: var(--ke-surface);
    color: var(--ke-text-primary);
  }
  .operations-nav {
    min-width: 0;
    overflow-y: auto;
    padding: 2.25rem 1.1rem;
    border-right: 1px solid var(--ke-border);
    background: var(--ke-surface);
  }
  .operations-nav h1 {
    margin: 0 0.85rem 0.4rem;
    font: var(--ke-title-page);
    letter-spacing: var(--ke-title-page-tracking);
  }
  .operations-nav nav {
    display: grid;
    gap: 5px;
  }
  .operations-nav nav button {
    padding: 0.55rem 0.85rem;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
    text-align: left;
    cursor: pointer;
  }
  .operations-nav nav button:hover:not(:disabled) {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .operations-nav nav button.selected {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-weight: 600;
  }
  .operations-nav:global(.has-nav-indicator) nav button {
    position: relative;
    z-index: 1;
  }
  .operations-nav:global(.has-nav-indicator) nav button.selected {
    background: transparent;
  }
  .operations-nav nav button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .operations-content {
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 2.25rem clamp(1.5rem, 4vw, 4.5rem) 4rem;
  }
  .operations-inner {
    max-width: 46rem;
    margin: 0;
  }
  .lock-note {
    margin: 0 0 var(--ke-space-4);
    padding: var(--ke-space-3);
    border-radius: var(--ke-radius-sm);
    border-left: 3px solid var(--ke-border-strong);
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
    font-size: var(--ke-text-sm);
  }
  .mono {
    margin: 0 0 var(--ke-space-1);
    font-family: var(--ke-font-mono);
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
    overflow-wrap: anywhere;
  }
  .hint {
    margin: var(--ke-space-2) 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  pre {
    max-height: 18rem;
    overflow: auto;
    padding: var(--ke-space-3);
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    background: var(--ke-surface-inset);
    font-family: var(--ke-font-mono);
    font-size: var(--ke-text-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .plain {
    margin-top: var(--ke-space-2);
  }
  .nav-caption {
    margin: 0 0.85rem 1.8rem;
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .nav-group {
    margin-top: 0.35rem;
  }
  .group-toggle {
    display: flex;
    width: 100%;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .group-toggle span {
    color: var(--ke-text-tertiary);
  }
  .group-items {
    display: grid;
    gap: 0.15rem;
    margin: 0.15rem 0 0.65rem 0.75rem;
    border-left: 1px solid var(--ke-border);
    padding-left: 0.35rem;
    color: var(--ke-text-secondary);
  }
  .group-items[hidden] {
    display: none;
  }
  .section-context {
    margin: 0 0 0.85rem;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .overview-intro {
    color: var(--ke-text-secondary);
    margin-bottom: 2.1rem;
  }
  .overview-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1.25rem;
    align-items: start;
  }
  .overview-group {
    padding: 1.25rem 0.75rem 0.65rem;
    border: 0;
    border-radius: var(--ke-radius-lg);
    background: var(--ke-surface-inset);
    min-width: 0;
  }
  .settings-overview .overview-group h3 {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin: 0 0.65rem 0.7rem;
    font-size: var(--ke-text-base);
    font-weight: 600;
  }
  .group-icon {
    color: var(--ke-text-secondary);
    display: inline-flex;
  }
  .settings-overview .overview-group button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    text-align: left;
    border: 0;
    border-radius: var(--ke-radius-md);
    padding: 0.8rem 0.65rem;
    background: transparent;
    color: var(--ke-text-primary);
    cursor: pointer;
  }
  .overview-group button span:first-child {
    display: grid;
    gap: 0.3rem;
    min-width: 0;
  }
  .overview-group button strong {
    font-size: var(--ke-text-sm);
    font-weight: 500;
  }
  .overview-group button small {
    font-size: var(--ke-text-xs);
    color: var(--ke-text-secondary);
  }
  .settings-overview .overview-group button:hover:not(:disabled),
  .settings-overview .overview-group button:focus-visible {
    background: var(--ke-surface-inset);
    background: color-mix(
      in srgb,
      var(--ke-surface-inset) 94%,
      var(--ke-text-primary)
    );
  }
  .settings-overview .overview-group button:focus-visible {
    outline: none;
    box-shadow: inset 3px 0 var(--ke-focus-ring);
  }
  .overview-group button:focus-visible strong {
    text-decoration: underline;
    text-underline-offset: 0.2em;
  }
  .mobile-sections {
    display: none;
  }
  @media (max-width: 1000px) {
    .overview-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 760px) {
    .operations {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: auto minmax(0, 1fr);
    }
    .operations-nav {
      padding: 1rem 1.25rem;
      border-right: 0;
      border-bottom: 1px solid var(--ke-border);
      overflow: visible;
    }
    .operations-nav h1 {
      margin: 0 0 0.75rem;
      font-size: var(--ke-text-lg);
    }
    .operations-nav nav,
    .nav-caption {
      display: none;
    }
    .mobile-sections {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 1rem;
      color: var(--ke-text-secondary);
      font-size: var(--ke-text-xs);
    }
    .mobile-sections select {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--ke-border-strong);
      border-radius: var(--ke-radius-sm);
      background: var(--ke-surface-raised);
      color: var(--ke-text-primary);
      font: inherit;
      font-size: var(--ke-text-sm);
      padding: 0.55rem;
    }
    .operations-content {
      padding: 1.5rem 1.25rem 3rem;
    }
    .overview-group {
      padding: 1rem;
    }
  }
</style>
