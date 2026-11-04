<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import { JobJournal, identity } from "./journal";
  import { PROVIDERS, JOB_LIMIT_BOUNDS } from "$lib/api/wire.generated";
  import type {
    Conversation,
    JobDefinition,
    JobRecord,
    JobRun,
    JobEvent,
    JobBody,
    JobReceipt,
    Profile,
    Results,
  } from "$lib/api/wire.generated";
  import {
    procedures,
    isReview,
    newJob,
    changeProcedure,
    startInput,
    localZone,
    scheduleTime,
    scheduleChoice,
    scheduleSummary,
    runLabel,
    privateResult,
    hasReviewToolMarkup,
    when,
  } from "./editor";

  export let stateId: string;
  export let conversationId: string;
  export let profile: Profile;
  export let busy = false;
  export let navigationLocked = false;
  let showArchived = false;
  $: navigationLocked = working || pending || confirmed !== null;
  $: visibleItems = items.filter(
    (item) =>
      item.kind !== "research" &&
      (showArchived ||
        (!item.cancelled &&
          item.kind !== "notebook_care" &&
          item.kind !== "notebook_maintenance")),
  );
  const displayZone = localZone();
  const draftKey = `kejtu-next.job-editor.v1.${stateId}`;
  const journal = new JobJournal(stateId, request, remember);
  let confirmed: JobReceipt | null = null;
  let working = false,
    ready = false,
    pending = false,
    absent = false;
  let error = "",
    notice = "";
  let items: JobRecord[] = [],
    cursor: string | null = null;
  let selected: JobRecord | null = null;
  let editor: JobDefinition | null = null;
  let runs: JobRun[] = [],
    runCursor: string | null = null;
  let inspected: JobRun | null = null;
  let events: JobEvent[] = [],
    eventCursor: string | null = null;
  let body: JobBody | null = null;
  let bodySequence: string | null = null;
  let invalidReviewOutput = false;
  let status: Results["background.status"] | null = null;
  let destroyed = false;
  let advanced = false;
  let timingOpen = false;
  let scheduleError = "";
  let conversations: Conversation[] = [],
    conversationCursor: string | null = null;
  let savedDefinition = "";
  $: dirty =
    editor !== null &&
    (!selected || JSON.stringify(editor) !== savedDefinition);
  $: explanation = editor ? procedures[editor.kind]?.description : "";
  $: resultText = bodySequence === null ? privateResult(body) : null;
  $: access = !editor?.permissions.read_memory
    ? "off"
    : isReview(editor.kind)
      ? editor.permissions.write_notes
        ? "write"
        : "read"
      : editor.permissions.deliver
        ? "deliver"
        : "read";
  $: sourceValid =
    !!editor?.conversation_id ||
    (editor !== null &&
      isReview(editor.kind) &&
      editor.conversation_id === null);
  $: canEnable =
    !!editor?.permissions.read_memory &&
    (isReview(editor.kind) || editor.permissions.deliver);
  const limitFields = [
    {
      name: "calls" as const,
      label: "Model requests per run",
      min: 1,
      max: JOB_LIMIT_BOUNDS.calls.max,
      hint: "Includes follow-up requests after tool use. Reminder follow-ups need at least two.",
    },
    {
      name: "operations" as const,
      label: "Tool operations per run",
      min: 0,
      max: JOB_LIMIT_BOUNDS.operations.max,
      hint: "Reads and note updates share this limit. Check-ins and reminder follow-ups use zero.",
    },
    {
      name: "seconds" as const,
      label: "Run time limit (seconds)",
      min: 1,
      max: JOB_LIMIT_BOUNDS.seconds.max,
      hint: "Shared time for the whole run, including its final result.",
    },
    {
      name: "source_items" as const,
      label: "Initial source items",
      min: 1,
      max: 32,
      hint: "One item is a complete conversation exchange or a reminder. This does not limit every later notebook read.",
    },
    {
      name: "runs_per_day" as const,
      label: "Maximum runs in 24 hours",
      min: 1,
      max: 48,
      hint: "Counts runs that started in the preceding 24 hours, including failed runs.",
    },
    {
      name: "cooldown_seconds" as const,
      label: "Minimum time between starts (seconds)",
      min: 60,
      max: 604800,
      hint: "An additional limit between runs of this job. It does not change the repeat schedule.",
    },
  ];
  const modelFields = [
    {
      name: "context_limit" as const,
      label: "Context window (tokens)",
      min: 1,
      hint: "Space shared by input and output.",
    },
    {
      name: "generation_limit" as const,
      label: "Output allowance (tokens)",
      min: 1,
      hint: "Reserved for the response and model reasoning, where applicable.",
    },
    {
      name: "timeout_seconds" as const,
      label: "Model-profile time limit (seconds)",
      min: 1,
      hint: "Fallback for operations without an explicit execution limit. This job uses its run time limit.",
    },
    {
      name: "safety_margin" as const,
      label: "Context margin (tokens)",
      min: 0,
      hint: "Extra space reserved for context accounting.",
    },
  ];

  function persist() {
    if (ready && editor)
      localStorage.setItem(
        draftKey,
        JSON.stringify({ selected, editor, confirmed, savedDefinition }),
      );
  }
  function remember(receipt: JobReceipt) {
    const raw = localStorage.getItem(draftKey);
    const saved = raw === null ? { selected, editor } : JSON.parse(raw);
    localStorage.setItem(
      draftKey,
      JSON.stringify({ ...saved, confirmed: receipt }),
    );
    confirmed = receipt;
  }
  function change(action: () => void) {
    action();
    editor = editor;
    notice = "";
    persist();
  }
  function setTime(value: string) {
    if (!editor) return;
    try {
      const timestamp = scheduleTime(
        value,
        displayZone,
        editor.schedule.timezone,
      );
      change(() => (editor!.schedule.starts_at = timestamp));
      scheduleError = "";
    } catch (caught) {
      scheduleError = caught instanceof Error ? caught.message : String(caught);
    }
  }
  function setAccess(value: string) {
    change(() => {
      editor!.permissions = {
        read_memory: value !== "off",
        write_notes: value === "write",
        deliver: value === "deliver",
      };
    });
  }
  function repeat(value: string) {
    if (value === "custom") {
      advanced = true;
      timingOpen = true;
      return;
    }
    change(() => {
      editor!.schedule.repeat = value as JobDefinition["schedule"]["repeat"];
      editor!.schedule.interval = 1;
    });
  }
  async function sources(more = false) {
    const page = await request("conversations.list", {
      cursor: more ? conversationCursor : null,
    });
    if (destroyed) return;
    conversations = more ? [...conversations, ...page.items] : page.items;
    conversationCursor = page.next_cursor;
  }
  function fresh() {
    if (pending) return;
    selected = null;
    runs = [];
    inspected = null;
    events = [];
    body = null;
    editor = newJob(profile);
    changeProcedure(editor, "idle_review", conversationId);
    savedDefinition = "";
    advanced = false;
    timingOpen = false;
    scheduleError = "";
    notice = "";
    persist();
  }
  async function list(more = false) {
    const page = await request("jobs.list", { cursor: more ? cursor : null });
    if (destroyed) return;
    items = more ? [...items, ...page.items] : page.items;
    cursor = page.next_cursor;
    status = await request("background.status", {});
  }
  async function runPage(more = false) {
    if (!selected) return;
    const page = await request("jobs.runs", {
      job_id: selected.id,
      cursor: more ? runCursor : null,
    });
    if (destroyed) return;
    runs = more ? [...runs, ...page.items] : page.items;
    runCursor = page.next_cursor;
  }
  async function choose(id: string) {
    if (pending) return;
    const read = await request("jobs.read", { job_id: id });
    if (destroyed) return;
    selected = read.job;
    editor = read.definition;
    savedDefinition = JSON.stringify(read.definition);
    scheduleError = "";
    advanced = false;
    inspected = null;
    events = [];
    body = null;
    persist();
    await runPage();
  }
  async function finish(receipt: JobReceipt | null) {
    pending = journal.pending !== null || confirmed !== null;
    absent = journal.absent;
    if (receipt) {
      const read = await request("jobs.read", { job_id: receipt.job_id });
      // Reconcile even after unmount, but do not overwrite a newer view's
      // already reconciled draft. A failed read retains the saved obligation.
      const saved = JSON.parse(localStorage.getItem(draftKey) ?? "{}");
      if (saved.confirmed?.mutation_key === receipt.mutation_key)
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            selected: read.job,
            editor: read.definition,
            confirmed: null,
            savedDefinition: JSON.stringify(read.definition),
          }),
        );
      if (destroyed) return;
      selected = read.job;
      editor = read.definition;
      confirmed = null;
      pending = false;
      savedDefinition = JSON.stringify(read.definition);
      scheduleError = "";
      inspected = null;
      events = [];
      body = null;
      notice = read.job.cancelled
        ? "Job cancelled. Saved results and notes remain available."
        : read.job.enabled
          ? "Job saved. It can start after Settings is closed and its idle time has passed."
          : "Job saved and paused.";
      await list();
      await runPage();
    } else
      notice =
        "No saved outcome found. Retry this exact choice or release it; nothing was resent.";
  }
  async function save() {
    if (
      !editor ||
      !dirty ||
      scheduleError ||
      !sourceValid ||
      (editor.enabled && !canEnable)
    )
      return;
    await finish(
      await journal.begin({
        operation: "jobs.save",
        arguments: selected
          ? {
              definition: editor,
              job_id: selected.id,
              expected_revision: selected.revision,
              mutation_key: identity(),
            }
          : { definition: editor, mutation_key: identity() },
      }),
    );
  }
  async function cancelJob() {
    if (!selected) return;
    await finish(
      await journal.begin({
        operation: "jobs.cancel",
        arguments: {
          job_id: selected.id,
          expected_revision: selected.revision,
          mutation_key: identity(),
        },
      }),
    );
  }
  async function cancelRun(id: string) {
    await finish(
      await journal.begin({
        operation: "jobs.cancel_run",
        arguments: { run_id: id, mutation_key: identity() },
      }),
    );
  }
  async function inspectRun(run: JobRun) {
    invalidReviewOutput = false;
    inspected = await request("jobs.run", { run_id: run.id });
    events = [];
    body = null;
    bodySequence = null;
    await eventPage();
    if (inspected.result_id) await readBody(null);
  }
  async function eventPage(more = false) {
    if (!inspected) return;
    const page = await request("jobs.events", {
      run_id: inspected.id,
      cursor: more ? eventCursor : null,
    });
    events = more ? [...events, ...page.items] : page.items;
    eventCursor = page.next_cursor;
  }
  async function readBody(sequence: string | null, offset = 0) {
    if (!inspected) return;
    const page =
      sequence === null
        ? await request("jobs.result", {
            run_id: inspected.id,
            offset,
            maximum_bytes: 16384,
          })
        : await request("jobs.event", {
            run_id: inspected.id,
            sequence,
            offset,
            maximum_bytes: 16384,
          });
    if (
      page.offset !== offset ||
      new TextEncoder().encode(page.content).length !==
        page.end - page.offset ||
      (offset &&
        (body?.sha256 !== page.sha256 ||
          body?.reference_id !== page.reference_id))
    )
      throw new Error("Exact run page identity or extent changed.");
    // Keep one bounded page, never accumulate every retained input/tool body.
    body = page;
    bodySequence = sequence;
    if (sequence === null && hasReviewToolMarkup(privateResult(page)))
      invalidReviewOutput = true;
  }
  async function run(action: () => Promise<void>) {
    if (working) return;
    working = true;
    error = "";
    try {
      await action();
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      working = false;
      pending =
        journal.pending !== null ||
        confirmed !== null ||
        localStorage.getItem(journal.key) !== null;
      absent = journal.absent;
    }
  }
  async function load() {
    const receipt = await journal.restore();
    pending = journal.pending !== null;
    absent = journal.absent;
    const raw = localStorage.getItem(draftKey);
    if (raw !== null) {
      const saved = JSON.parse(raw);
      // Drafts may be incomplete, but never substitute for a validated pending
      // operation. Preserve invalid data and require an explicit new draft.
      if (
        !saved ||
        !saved.editor ||
        typeof saved.editor !== "object" ||
        !saved.editor.schedule ||
        !saved.editor.profile ||
        !saved.editor.permissions ||
        !saved.editor.limits
      )
        throw new Error(
          "Saved job editor is invalid. It was retained; no action was sent.",
        );
      editor = saved.editor;
      selected = saved.selected;
      confirmed = saved.confirmed ?? null;
      savedDefinition = saved.savedDefinition ?? "";
    }
    ready = true;
    await list();
    if (receipt || confirmed) await finish(receipt ?? confirmed);
    else if (selected) {
      const read = await request("jobs.read", { job_id: selected.id });
      // A stored draft may have unsaved edits. Read the baseline without replacing it.
      savedDefinition = JSON.stringify(read.definition);
      if (JSON.stringify(editor) === savedDefinition) selected = read.job;
      if (read.job.cancelled && !pending) {
        // Keep the retained draft on disk; removed jobs belong in archived history.
        selected = null;
        editor = null;
        runs = [];
      } else await runPage();
    }
    await sources();
  }
  onMount(() => {
    void run(load);
    return () => {
      destroyed = true;
    };
  });
</script>

<section aria-label="Background jobs" class="jobs-panel">
  <header>
    <h2>Advanced jobs</h2>
    <p class="muted">
      Custom schedules and retained history. Notebook tidy and Reflection have
      their own controls.
    </p>
  </header>
  <p class="waiting-note">
    Jobs wait while Settings is open. Close Settings and leave the message box
    empty to allow idle work.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}
    <div class="recovery">
      <p>
        A job change needs confirmation. Check its saved outcome before making
        another change.
      </p>
      <button
        disabled={working || busy}
        on:click={() =>
          run(async () => finish(confirmed ?? (await journal.resolve())))}
        >Resolve job change</button
      >
      {#if absent}
        <button
          disabled={working || busy}
          on:click={() => run(async () => finish(await journal.retry()))}
          >Retry exact job change</button
        >
        <button
          disabled={working || busy}
          on:click={() =>
            run(async () => {
              journal.release();
              pending = false;
              absent = false;
              await list();
            })}>Release uncommitted job change</button
        >
      {/if}
    </div>
  {/if}
  <div class="actions">
    <button disabled={!ready || working || pending || busy} on:click={fresh}
      >New job</button
    >
    <button
      disabled={working || busy}
      on:click={() =>
        run(async () => {
          await list();
          await runPage();
        })}>Refresh jobs</button
    >
  </div>
  <label class="archive-choice"
    ><input
      type="checkbox"
      bind:checked={showArchived}
      disabled={working || pending}
    /> Show all jobs and archived history</label
  >
  <ul class="job-list">
    {#each visibleItems as item}
      <li class:selected={selected?.id === item.id}>
        <button
          class="job-choice"
          disabled={working || pending || busy}
          on:click={() => run(() => choose(item.id))}
        >
          <span>{item.title}</span>
          <small
            >{procedures[item.kind].title} · {item.cancelled
              ? "Cancelled"
              : item.enabled
                ? "Enabled"
                : "Paused"}</small
          >
        </button>
      </li>
    {/each}
  </ul>
  {#if ready && !visibleItems.length && !editor}<p class="muted">
      No other active jobs. Notebook tidy and Reflection are managed in their
      own sections.
    </p>{/if}
  {#if cursor}<button
      disabled={working || busy}
      on:click={() => run(() => list(true))}>More jobs</button
    >{/if}

  {#if editor}
    <form on:submit|preventDefault={() => run(save)}>
      <fieldset
        class="editor"
        disabled={working || pending || busy || selected?.cancelled}
      >
        <legend>{selected ? "Edit job" : "New job"}</legend>
        <div class="two-columns">
          <div class="field">
            <label for="job-procedure">Job</label>
            <select
              use:choiceSelect={editor.kind}
              id="job-procedure"
              value={editor.kind}
              on:change={(e) =>
                change(() =>
                  changeProcedure(
                    editor!,
                    e.currentTarget.value as JobDefinition["kind"],
                    conversationId,
                  ),
                )}
            >
              {#each Object.entries(procedures).filter(([kind]) => (kind !== "research" && kind !== "notebook_care" && kind !== "notebook_maintenance") || kind === editor?.kind) as [value, procedure]}<option
                  {value}>{procedure.title}</option
                >{/each}
            </select>
          </div>
          <div class="field">
            <label for="job-title">Job title</label>
            <input
              id="job-title"
              value={editor.title}
              on:input={(e) =>
                change(() => (editor!.title = e.currentTarget.value))}
              maxlength="500"
              required
            />
          </div>
        </div>
        <p class="procedure-help help">{explanation}</p>
        <div class="two-columns">
          <div class="field">
            <label for="job-repeat">Repeat</label>
            <select
              use:choiceSelect={scheduleChoice(editor)}
              id="job-repeat"
              value={scheduleChoice(editor)}
              on:change={(e) => repeat(e.currentTarget.value)}
            >
              <option value="none">Once</option><option value="daily"
                >Every day</option
              >
              <option value="weekly">Every week</option><option value="monthly"
                >Every month</option
              >
              <option value="custom">Custom interval…</option>
            </select>
          </div>
          <div class="field">
            <label for="job-start">Start date and time</label>
            <input
              id="job-start"
              type="text"
              placeholder="YYYY-MM-DD HH:mm"
              autocomplete="off"
              spellcheck={false}
              aria-describedby="job-start-help"
              value={startInput(editor.schedule.starts_at, displayZone)}
              on:input={(e) => setTime(e.currentTarget.value)}
              required
            />
            <p class="help" id="job-start-help">
              {displayZone} · YYYY-MM-DD HH:mm (24-hour). The job waits for idle time
              after this date.
            </p>
          </div>
        </div>
        {#if scheduleError}<p role="alert">{scheduleError}</p>{/if}
        <div class="two-columns">
          <div class="field">
            {#if isReview(editor.kind)}
              <label for="job-scope">Review scope</label>
              <select
                use:choiceSelect={editor.conversation_id === null
                  ? "notebook"
                  : "conversation"}
                id="job-scope"
                value={editor.conversation_id === null
                  ? "notebook"
                  : "conversation"}
                on:change={(e) =>
                  change(
                    () =>
                      (editor!.conversation_id =
                        e.currentTarget.value === "notebook"
                          ? null
                          : conversationId),
                  )}
              >
                <option value="notebook">Review the notebook</option>
                <option value="conversation">Focus on a conversation</option>
              </select>
              <p class="help">
                {editor.conversation_id === null
                  ? "Start with notebook notes. No conversation is required. Other conversation sources remain subject to their access permissions."
                  : "Use this conversation as context for the review. It is a focus, not an exclusive limit on accessible notebook notes."}
              </p>
            {/if}
            {#if editor.conversation_id !== null || !isReview(editor.kind)}
              <label for="job-source">Source conversation</label>
              <select
                use:choiceSelect={editor.conversation_id}
                id="job-source"
                value={editor.conversation_id}
                on:change={(e) =>
                  change(
                    () => (editor!.conversation_id = e.currentTarget.value),
                  )}
                required
              >
                <option value="" disabled>Choose a conversation</option>
                {#if editor.conversation_id && !conversations.some((item) => item.conversation_id === editor!.conversation_id)}
                  <option value={editor.conversation_id}
                    >{editor.conversation_id === conversationId
                      ? "Current conversation"
                      : "Saved conversation"} · {editor.conversation_id.slice(
                      0,
                      8,
                    )}</option
                  >
                {/if}
                {#each conversations as item}<option
                    value={item.conversation_id}
                    >{item.title || "Untitled conversation"}{item.imported
                      ? " (historical)"
                      : ""}</option
                  >{/each}
              </select>
              <p class="help">
                Use this conversation and the memory it can access. Existing
                conversation permissions still apply.
              </p>
              {#if conversationCursor}<button
                  type="button"
                  class="text-button"
                  on:click={() => run(() => sources(true))}
                  >Load more conversations</button
                >{/if}
            {/if}
          </div>
          <div class="field">
            <label for="job-access">What it can do</label>
            <select
              use:choiceSelect={access}
              id="job-access"
              value={access}
              on:change={(e) => setAccess(e.currentTarget.value)}
            >
              {#if isReview(editor.kind)}
                <option value="read">Review only</option><option value="write"
                  >{editor.kind === "notebook_maintenance"
                    ? "Review and update existing notes"
                    : "Reflect and write notes"}</option
                >
              {:else}
                <option value="deliver"
                  >Read sources and prepare a message</option
                >
                <option value="read">Read sources only (cannot run)</option>
              {/if}
              <option value="off">No source access (cannot run)</option>
            </select>
            <p class="help">
              {isReview(editor.kind)
                ? editor.permissions.write_notes
                  ? editor.kind === "notebook_maintenance"
                    ? "May shorten or reorganise existing notes after reading them. Does not create or delete notes. Conversation focus also requires that conversation’s write permission."
                    : editor.conversation_id === null
                      ? "May create or revise notebook notes. No outgoing messages."
                      : "May save or revise notes. The conversation must also allow note writing. No outgoing messages."
                  : "Keeps a private review result. Does not change notes or send a message."
                : "May prepare a message for delivery. Chat display is not available yet; read the private result here."}
            </p>
          </div>
        </div>
        <label class="toggle"
          ><input
            type="checkbox"
            checked={editor.enabled}
            on:change={(e) =>
              change(() => (editor!.enabled = e.currentTarget.checked))}
          /> Enable this job</label
        >
        {#if editor.enabled && !canEnable}<p role="alert">
            Choose source access and the required delivery permission before
            enabling this job.
          </p>{/if}
        <div class="summary" aria-label="Job summary">
          <p>{scheduleSummary(editor, displayZone)}</p>
          {#if editor.schedule.repeat !== "none" && editor.schedule.timezone !== displayZone}
            <p class="help">
              Repeats follow {editor.schedule.timezone}, set in Advanced. Local
              times can shift when clocks change.
            </p>
          {/if}
          <p class="help">
            After {editor.idle_seconds / 60} minutes without interaction in Kejtu.
            {editor.enabled
              ? "Enabled after you save."
              : "Paused until you enable and save it."}
          </p>
        </div>
        <button
          type="button"
          class="advanced-toggle"
          aria-expanded={advanced}
          aria-controls="job-advanced"
          on:click={() => (advanced = !advanced)}>Advanced</button
        >
        <div id="job-advanced" hidden={!advanced}>
          <p class="help">
            Fine-tune this job. Opening these sections does not change its
            settings.
          </p>
          <details bind:open={timingOpen}>
            <summary>Timing and missed runs</summary>
            <div class="advanced-content">
              <div class="field">
                <label for="job-idle">Wait for idle time (minutes)</label>
                <input
                  id="job-idle"
                  type="number"
                  min="0.5"
                  max="1440"
                  step="any"
                  value={editor.idle_seconds / 60}
                  on:input={(e) =>
                    change(
                      () =>
                        (editor!.idle_seconds = Math.round(
                          e.currentTarget.valueAsNumber * 60,
                        )),
                    )}
                />
                <p class="help">
                  Default: 30 minutes. Interaction or reopening Kejtu resets the
                  wait. Settings and an unsent draft prevent a start.
                </p>
              </div>
              <div class="two-columns">
                <div class="field">
                  <label for="job-repeat-unit">Repeat unit</label>
                  <select
                    use:choiceSelect={editor.schedule.repeat}
                    id="job-repeat-unit"
                    value={editor.schedule.repeat}
                    on:change={(e) =>
                      change(() => {
                        editor!.schedule.repeat = e.currentTarget
                          .value as JobDefinition["schedule"]["repeat"];
                        if (editor!.schedule.repeat === "none")
                          editor!.schedule.interval = 1;
                      })}
                  >
                    <option value="none">Once</option><option value="minutes"
                      >Minutes</option
                    ><option value="daily">Days</option><option value="weekly"
                      >Weeks</option
                    ><option value="monthly">Months</option>
                  </select>
                </div>
                <div class="field">
                  <label for="job-interval">Every</label>
                  <input
                    id="job-interval"
                    type="number"
                    min="1"
                    max={editor.schedule.repeat === "minutes"
                      ? 10080
                      : editor.schedule.repeat === "none"
                        ? 1
                        : 366}
                    value={editor.schedule.interval}
                    on:input={(e) =>
                      change(
                        () =>
                          (editor!.schedule.interval =
                            e.currentTarget.valueAsNumber),
                      )}
                  />
                </div>
              </div>
              <p class="help">
                For example, every 2 weeks. Daily, weekly and monthly schedules
                keep the local time. Missing dates or clock-change times are
                skipped.
              </p>
              <div class="field">
                <label for="job-zone">Time zone</label>
                <input
                  id="job-zone"
                  value={editor.schedule.timezone}
                  on:input={(e) =>
                    change(
                      () => (editor!.schedule.timezone = e.currentTarget.value),
                    )}
                  required
                />
                <p class="help">
                  Controls the repeat schedule. Start and run times in the
                  normal view use {displayZone}. The exact start below must use
                  this schedule zone’s matching UTC offset.
                </p>
              </div>
              <div class="field">
                <label for="job-exact-start">Exact start with UTC offset</label>
                <input
                  id="job-exact-start"
                  value={editor.schedule.starts_at}
                  on:input={(e) => {
                    change(
                      () =>
                        (editor!.schedule.starts_at = e.currentTarget.value),
                    );
                    scheduleError = "";
                  }}
                  required
                />
                <p class="help">
                  For example, 2026-10-12T09:00:00+02:00 in Europe/Vienna. Use
                  this field to choose an exact clock-change time.
                </p>
              </div>
              <div class="field">
                <label for="job-missed">If a scheduled time is missed</label>
                <select
                  use:choiceSelect={editor.schedule.catch_up}
                  id="job-missed"
                  value={editor.schedule.catch_up}
                  on:change={(e) =>
                    change(
                      () =>
                        (editor!.schedule.catch_up = e.currentTarget.value as
                          "coalesce" | "skip"),
                    )}
                >
                  <option value="coalesce">Catch up once</option><option
                    value="skip">Skip if too late</option
                  >
                </select>
                <p class="help">
                  Catch up once combines missed times into one run. Skip if too
                  late uses the delay below. Both still wait for idle time.
                </p>
              </div>
              <div class="field">
                <label for="job-grace">Allowed delay (seconds)</label>
                <input
                  id="job-grace"
                  type="number"
                  min="0"
                  max="3600"
                  value={editor.schedule.grace_seconds}
                  on:input={(e) =>
                    change(
                      () =>
                        (editor!.schedule.grace_seconds =
                          e.currentTarget.valueAsNumber),
                    )}
                />
                <p class="help">
                  Used only with Skip if too late. A recent scheduled time
                  within this delay can become a pending run.
                </p>
              </div>
            </div>
          </details>
          <details>
            <summary>Work limits</summary>
            <div class="advanced-content">
              <p class="help">
                These are maximums, not targets. More work does not grant access
                to more sources or tools.
              </p>
              <div class="two-columns">
                {#each limitFields.filter((field) => editor?.kind !== "notebook_care" || field.name !== "runs_per_day") as field}<div
                    class="field"
                  >
                    <label for={`job-limit-${field.name}`}>{field.label}</label>
                    <input
                      id={`job-limit-${field.name}`}
                      type="number"
                      min={field.min}
                      max={!isReview(editor.kind) && field.name === "operations"
                        ? 0
                        : field.max}
                      value={editor.limits[field.name]}
                      on:input={(e) =>
                        change(
                          () =>
                            (editor!.limits[field.name] =
                              e.currentTarget.valueAsNumber),
                        )}
                    />
                    <p class="help">{field.hint}</p>
                  </div>{/each}
              </div>
              <p class="help">
                Effective time limit: {Math.min(
                  editor.limits.seconds,
                  editor.profile.timeout_seconds,
                )} seconds per run.
              </p>
            </div>
          </details>
          <details>
            <summary>Model for this job</summary>
            <div class="advanced-content">
              <p class="help">
                A saved copy of the model profile. Changes here affect only this
                job. Later changes in Models do not update it automatically.
              </p>
              <div class="field">
                <label for="job-provider">Provider</label>
                <select
                  use:choiceSelect={editor.profile.provider}
                  id="job-provider"
                  value={editor.profile.provider}
                  on:change={(e) =>
                    change(
                      () =>
                        (editor!.profile.provider = e.currentTarget
                          .value as Profile["provider"]),
                    )}
                  >{#each PROVIDERS as provider}<option value={provider}
                      >{provider === "ollama"
                        ? "Local (Ollama)"
                        : provider === "deepseek"
                          ? "DeepSeek"
                          : "Kimi"}</option
                    >{/each}</select
                >
                <p class="help">
                  The provider must be available in this session. Remote
                  providers need their saved credentials.
                </p>
              </div>
              <div class="field">
                <label for="job-model">Model</label><input
                  id="job-model"
                  value={editor.profile.model}
                  on:input={(e) =>
                    change(
                      () => (editor!.profile.model = e.currentTarget.value),
                    )}
                  required
                />
                <p class="help">
                  The exact model name supported by the selected provider.
                </p>
              </div>
              <div class="field">
                <label for="job-endpoint">Service address</label><input
                  id="job-endpoint"
                  value={editor.profile.endpoint}
                  on:input={(e) =>
                    change(
                      () => (editor!.profile.endpoint = e.currentTarget.value),
                    )}
                  required
                />
                <p class="help">
                  Remote providers require their official address. Local Ollama
                  requires a loopback address and port.
                </p>
              </div>
              <div class="two-columns">
                {#each modelFields as field}<div class="field">
                    <label for={`job-model-${field.name}`}>{field.label}</label>
                    <input
                      id={`job-model-${field.name}`}
                      type="number"
                      min={field.min}
                      value={editor.profile[field.name]}
                      on:input={(e) =>
                        change(
                          () =>
                            (editor!.profile[field.name] =
                              e.currentTarget.valueAsNumber),
                        )}
                    />
                    <p class="help">{field.hint}</p>
                  </div>{/each}
              </div>
              <div class="field">
                <label for="job-reasoning">Reasoning effort</label>
                <select
                  use:choiceSelect={String(editor.profile.thinking_level)}
                  id="job-reasoning"
                  value={String(editor.profile.thinking_level)}
                  on:change={(e) =>
                    change(
                      () =>
                        (editor!.profile.thinking_level =
                          e.currentTarget.value === "true"
                            ? true
                            : e.currentTarget.value === "false"
                              ? false
                              : (e.currentTarget.value as
                                  "low" | "medium" | "high" | "max")),
                    )}
                >
                  <option value="false">Off</option><option value="true"
                    >On</option
                  >{#each ["low", "medium", "high", "max"] as effort}<option
                      value={effort}>{effort}</option
                    >{/each}
                </select>
                <p class="help">
                  Choose an option supported by this model. More reasoning can
                  take more time and output space.
                </p>
              </div>
              <label class="toggle"
                ><input
                  type="checkbox"
                  checked={editor.profile.keep_loaded}
                  on:change={(e) =>
                    change(
                      () =>
                        (editor!.profile.keep_loaded = e.currentTarget.checked),
                    )}
                /> Keep model loaded</label
              >
              <p class="help">
                Local Ollama only. Keeps the model in memory between requests.
                Leave off for a remote provider.
              </p>
            </div>
          </details>
          <details>
            <summary>Delivery and source details</summary>
            <div class="advanced-content">
              <div class="field">
                <label for="job-channel">Delivery channel</label>
                <select
                  use:choiceSelect={editor.delivery}
                  id="job-channel"
                  disabled={isReview(editor.kind)}
                  value={editor.delivery}
                  on:change={(e) =>
                    change(
                      () =>
                        (editor!.delivery = e.currentTarget.value as
                          "quiet" | "message"),
                    )}
                  ><option value="quiet">Quiet notice</option><option
                    value="message">Conversational message</option
                  ></select
                >
                <p class="help">
                  Requested channel for permitted check-ins and reminders. Chat
                  display is not available yet. Notebook reviews remain private.
                </p>
              </div>
              {#if editor.conversation_id !== null}
                <div class="field">
                  <label for="job-source-id">Source conversation ID</label
                  ><input
                    id="job-source-id"
                    value={editor.conversation_id}
                    on:input={(e) =>
                      change(
                        () => (editor!.conversation_id = e.currentTarget.value),
                      )}
                    required
                    pattern={"[0-9a-f]{32}"}
                  />
                  <p class="help">
                    The exact identity behind the conversation choice. Use the
                    main list unless you need a known identifier.
                  </p>
                </div>
              {/if}
              {#if selected}<p class="help">
                  Saved revision {selected.revision}. Earlier runs keep their
                  own saved settings.
                </p>{/if}
              {#if status}<p class="help">
                  Activity: {status.activity_fresh
                    ? "connected"
                    : "unknown or stale"}. Scheduler: {status.scheduler_running
                    ? "running"
                    : "stopped"}. These report the app connection, not job
                  completion.
                </p>
                {#if status.error_code}<p role="alert">
                    Last scheduler error: {status.error_code}
                  </p>{/if}
              {/if}
            </div>
          </details>
        </div>
        {#if selected && dirty && runs.some( (item) => ["pending", "running", "cancel_requested"].includes(item.status) )}
          <p class="waiting-note">
            Saving changes cancels the waiting or active run. For a one-time
            job, choose a new start time to schedule another run.
          </p>
        {/if}
        <div class="actions save-row">
          <button
            type="submit"
            disabled={!dirty ||
              !!scheduleError ||
              !sourceValid ||
              (editor.enabled && !canEnable)}>Save job</button
          >
        </div>
      </fieldset>
    </form>
    {#if selected}
      <section class="runs" aria-label="Job runs">
        <div class="section-heading">
          <h3>Recent runs</h3>
          <button
            disabled={working || busy}
            on:click={() => run(() => runPage())}>Refresh runs</button
          >
        </div>
        <p class="help run-time-zone">
          Times shown in {displayZone} · YYYY-MM-DD HH:mm (24-hour).
        </p>
        {#if !working && !runCursor && !runs.some((item) => item.revision === selected!.revision)}
          <p class="muted">
            No runs recorded for the current job version ({selected.revision}).
          </p>
        {/if}
        <ul class="run-list">
          {#each runs as item}<li>
              <button
                class="run-choice"
                disabled={working || busy}
                on:click={() => run(() => inspectRun(item))}
              >
                <span
                  >{inspected?.id === item.id && invalidReviewOutput
                    ? "No usable review report"
                    : runLabel(item.status, item.error_code)}</span
                >
                <small
                  >{item.started_at
                    ? `Started ${when(item.started_at, displayZone)}`
                    : "Not started"}</small
                >
                {#if item.finished_at}<small
                    >Finished {when(item.finished_at, displayZone)}</small
                  >{/if}
                <small>Scheduled {when(item.scheduled_at, displayZone)}</small>
                {#if item.revision < selected.revision}<small
                    class="run-version"
                    >Earlier job version {item.revision} · current version {selected.revision}</small
                  >{/if}
              </button>
              {#if ["pending", "running", "cancel_requested"].includes(item.status)}<button
                  disabled={working || pending || busy}
                  on:click={() => run(() => cancelRun(item.id))}
                  >Cancel this run</button
                >{/if}
            </li>{/each}
        </ul>
        {#if runs.some((item) => item.status === "pending")}<p class="help">
            Waiting runs have not started. Close Settings and leave Kejtu idle.
            The app also checks source permissions and run limits.
          </p>{/if}
        {#if runCursor}<button
            disabled={working || busy}
            on:click={() => run(() => runPage(true))}>More runs</button
          >{/if}
        {#if inspected}
          <div class="run-result">
            <h4>
              {invalidReviewOutput
                ? "No usable review report"
                : runLabel(inspected.status, inspected.error_code)}
            </h4>
            {#if inspected.revision < selected.revision}
              <p class="waiting-note">
                This run is from job version {inspected.revision}. The current
                saved version is {selected.revision}.
              </p>
            {/if}
            {#if invalidReviewOutput || inspected.error_code === "job_review_tool_markup"}
              <p role="alert">
                The model returned tool instructions instead of a review report.
                Those printed instructions did not run. Earlier operations may
                have made permitted note changes; check Run details.
              </p>
              {#if invalidReviewOutput && inspected.status === "completed"}<p
                  class="help"
                >
                  This older run was recorded as completed. Its original record
                  is preserved in Run details.
                </p>{/if}
            {/if}
            {#if inspected.status === "completed" && inspected.error_code === "job_review_limited"}
              <p class="waiting-note">
                This pass reached a work limit. The private report describes
                what was checked and what remains. It does not confirm a
                complete notebook review.
              </p>
            {:else if inspected.error_code}<p role="alert">
                Recorded reason: {inspected.error_code}
              </p>{/if}
            {#if inspected.result_id}<button
                disabled={working || busy}
                on:click={() => run(() => readBody(null))}
                >Read private result</button
              >{/if}
            {#if resultText && !invalidReviewOutput}<p class="result-text">
                {resultText}
              </p>{/if}
            {#if inspected.status === "completed" && !invalidReviewOutput}<p
                class="help"
              >
                Completion does not necessarily mean notes changed or a message
                was delivered.
              </p>{/if}
            <details>
              <summary>Run details</summary>
              <div class="advanced-content">
                <p class="help">
                  Run {inspected.id} · saved revision {inspected.revision}
                </p>
                <p class="help">
                  Recorded status: {runLabel(inspected.status)}
                </p>
                <p class="help">
                  Started: {inspected.started_at
                    ? when(inspected.started_at, displayZone)
                    : "Not started"}. Finished: {inspected.finished_at
                    ? when(inspected.finished_at, displayZone)
                    : "Not finished"}.
                </p>
                <ul>
                  {#each events as item}<li>
                      <button
                        disabled={working || busy}
                        on:click={() => run(() => readBody(item.sequence))}
                        >{item.kind} · {item.bytes} bytes</button
                      >
                    </li>{/each}
                </ul>
                {#if eventCursor}<button
                    disabled={working || busy}
                    on:click={() => run(() => eventPage(true))}
                    >More events</button
                  >{/if}
                {#if body}<p class="help">
                    Exact bytes {body.offset}–{body.end} of {body.total_bytes}. {body.reason ??
                      ""}
                  </p>
                  <pre>{body.content}</pre>
                  {#if body.next_offset !== null}<button
                      disabled={working || busy}
                      on:click={() =>
                        run(() => readBody(bodySequence, body!.next_offset!))}
                      >Next exact page</button
                    >{/if}
                {/if}
              </div>
            </details>
            {#if inspected.result_id && body && !resultText && bodySequence === null}<p
                class="help"
              >
                The result is available in Run details, with exact pages when
                needed.
              </p>{/if}
          </div>
        {/if}
      </section>
      <div class="cancel-job">
        <button
          disabled={working || pending || busy || selected.cancelled}
          on:click={() => run(cancelJob)}>Cancel job and future runs</button
        >
        <p class="help">
          Stops this job permanently. Saved results and notes remain.
        </p>
      </div>
    {/if}
  {/if}
</section>

<style>
  .jobs-panel {
    max-width: 48rem;
    min-width: 0;
  }
  header h2 {
    margin-bottom: 0.35rem;
  }
  .muted,
  .help {
    color: var(--ke-text-secondary);
  }
  .help {
    font-size: var(--ke-text-sm);
    line-height: 1.55;
    margin: 0.3rem 0 0;
  }
  .waiting-note,
  .recovery {
    padding: 0.7rem 0.9rem;
    border-left: 2px solid var(--ke-border-strong);
    background: var(--ke-surface-inset);
    font-size: var(--ke-text-sm);
  }
  .actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin: 1rem 0;
  }
  .job-list,
  .run-list {
    list-style: none;
    padding: 0;
    margin: 0 0 1.5rem;
  }
  .job-list li {
    border-bottom: 1px solid var(--ke-border);
  }
  .job-choice {
    width: 100%;
    display: grid;
    gap: 0.2rem;
    text-align: left;
    padding: 0.85rem;
    border: 0;
    background: transparent;
  }
  .selected .job-choice {
    background: var(--ke-surface-inset);
  }
  .job-choice span,
  .run-choice span {
    font-weight: 550;
  }
  small {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  fieldset.editor {
    border: 0;
    margin: 1.5rem 0;
    padding: 0;
    min-width: 0;
  }
  legend {
    font-size: var(--ke-text-base);
    font-weight: 600;
    padding: 0;
    margin-bottom: 1rem;
    color: var(--ke-text-primary);
  }
  .field {
    min-width: 0;
    margin-bottom: 1rem;
  }
  .field > label {
    display: block;
    margin-bottom: 0.35rem;
    font-size: var(--ke-text-sm);
    font-weight: 550;
  }
  .field > input,
  .field > select {
    display: block;
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }
  .procedure-help {
    margin: -0.4rem 0 1rem;
  }
  .two-columns {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
  }
  .toggle {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin: 0.5rem 0;
  }
  .summary {
    padding: 0.9rem 0;
    margin: 0.7rem 0;
    border-top: 1px solid var(--ke-border);
    border-bottom: 1px solid var(--ke-border);
  }
  .summary p {
    margin: 0;
  }
  .advanced-toggle {
    margin-bottom: 0.4rem;
  }
  #job-advanced {
    margin-top: 0.3rem;
  }
  details {
    border-bottom: 1px solid var(--ke-border);
    margin: 0.6rem 0;
  }
  summary {
    padding: 0.7rem 0;
    font-weight: 550;
  }
  .advanced-content {
    padding: 0.4rem 0 1rem;
  }
  .advanced-content > .help:first-child {
    margin-bottom: 1rem;
  }
  .save-row {
    margin-top: 1.25rem;
  }
  .text-button {
    margin-top: 0.5rem;
  }
  .runs {
    border-top: 1px solid var(--ke-border);
    padding-top: 1rem;
  }
  .section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.75rem;
  }
  .section-heading h3 {
    margin: 0;
  }
  .run-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.4rem 0;
    flex-wrap: wrap;
  }
  .run-choice {
    display: grid;
    gap: 0.2rem;
    text-align: left;
    flex: 1;
  }
  .run-time-zone {
    margin-bottom: 0.75rem;
  }
  .run-version {
    color: var(--ke-text-primary);
  }
  .run-choice {
    overflow-wrap: anywhere;
  }
  .run-result {
    padding: 1rem 0;
  }
  h4 {
    margin: 0 0 0.75rem;
  }
  .result-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .cancel-job {
    margin-top: 1.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--ke-border);
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 24rem;
    overflow: auto;
  }
  [role="alert"] {
    color: var(--ke-danger);
  }
  @media (max-width: 680px) {
    .two-columns {
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
    }
  }
</style>
