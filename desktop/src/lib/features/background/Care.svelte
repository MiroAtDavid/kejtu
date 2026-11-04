<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import {
    DEFAULT_JOB_LIMITS,
    JOB_LIMIT_BOUNDS,
  } from "$lib/api/wire.generated";
  import { request, identity } from "$lib/api/client";
  import type {
    Profile,
    JobDefinition,
    JobRecord,
    CareStatus,
    CareChange,
    CareSource,
    JobBody,
  } from "$lib/api/wire.generated";
  import { JobJournal } from "./journal";
  import { newJob, localZone, startInput, scheduleTime, when } from "./editor";

  export let stateId: string;
  export let profile: Profile;
  export let busy = false;
  export let navigationLocked = false;
  const journal = new JobJournal(stateId + ".care");
  const undoKey = `kejtu-next.care-undo.v1.${stateId}`;
  let job: JobRecord | null = null;
  let draft: JobDefinition = {
    ...newJob(profile),
    kind: "notebook_care",
    title: "Keep notebook tidy",
    idle_seconds: 60,
    permissions: { read_memory: true, write_notes: true, deliver: false },
    limits: { ...DEFAULT_JOB_LIMITS.notebook_care },
  };
  draft.schedule.repeat = "daily";
  let start = startInput(draft.schedule.starts_at, localZone());
  let status: CareStatus | null = null;
  let changes: CareChange[] = [];
  let cursor: string | null = null;
  let working = false,
    ready = false,
    advanced = false,
    history = false,
    pending = false,
    absent = false;
  let error = "",
    notice = "";
  let pendingUndo: { group_id: string; mutation_key: string } | null = null;
  let selected: {
    group_id: string;
    source: CareSource;
    page: JobBody;
    text: string;
  } | null = null;
  let destroyed = false;
  const labels = {
    up_to_date: "Up to date",
    working: "Working",
    continuing_later: "Continuing later",
    needs_attention: "Needs attention",
  };
  $: navigationLocked = working || pending || pendingUndo !== null;
  $: locked = busy || !ready || navigationLocked;
  $: checked =
    (status?.counts.reviewed ?? 0) +
    (status?.counts.changed ?? 0) +
    (status?.counts.proposed ?? 0);

  async function run(action: () => Promise<void>) {
    if (working) return;
    working = true;
    error = "";
    try {
      await action();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      working = false;
      pending = journal.pending !== null;
      absent = journal.absent;
    }
  }
  async function refresh() {
    if (!job) return;
    status = await request("care.status", { job_id: job.id });
    if (history) await changePage();
  }
  async function load() {
    let next: string | null = null;
    let found: JobRecord | undefined;
    do {
      const page = await request("jobs.list", { cursor: next, page_size: 50 });
      found = page.items.find(
        (item) => item.kind === "notebook_care" && !item.cancelled,
      );
      next = page.next_cursor;
    } while (!found && next);
    if (destroyed) return;
    job = found ?? null;
    if (job) {
      const detail = await request("jobs.read", { job_id: job.id });
      draft = structuredClone(detail.definition);
      start = startInput(draft.schedule.starts_at, localZone());
      await refresh();
    }
    ready = true;
  }
  async function save() {
    const definition = structuredClone(draft);
    if (startInput(definition.schedule.starts_at, localZone()) !== start)
      definition.schedule.starts_at = scheduleTime(
        start,
        localZone(),
        definition.schedule.timezone,
      );
    definition.schedule.repeat = "daily";
    definition.conversation_id = null;
    definition.permissions.read_memory = true;
    definition.permissions.deliver = false;
    definition.delivery = "quiet";
    await journal.begin({
      operation: "jobs.save",
      arguments: job
        ? {
            job_id: job.id,
            expected_revision: job.revision,
            definition,
            mutation_key: identity(),
          }
        : { definition, mutation_key: identity() },
    });
    notice = definition.enabled
      ? "Daily notebook care saved. Work continues while Kejtu is open and idle."
      : "Notebook care paused. Saved progress is retained.";
    await load();
  }
  async function changePage(more = false) {
    if (!job) return;
    const page = await request("care.changes", {
      job_id: job.id,
      cursor: more ? cursor : null,
    });
    changes = more ? [...changes, ...page.items] : page.items;
    cursor = page.next_cursor;
  }
  function revisions(change: CareChange) {
    return [
      ...change.sources.map((source, i) => ({
        source,
        label: `Original ${i + 1} of ${change.sources.length}`,
      })),
      ...change.outputs.map((source, i) => ({
        source,
        label: `Result ${i + 1} of ${change.outputs.length}`,
      })),
    ];
  }
  function revisionIndex(change: CareChange, current: CareSource | undefined) {
    return revisions(change).findIndex(
      ({ source }) =>
        source.record_id === current?.record_id &&
        source.revision_id === current?.revision_id,
    );
  }
  async function toggleRevision(change: CareChange) {
    if (selected?.group_id === change.id) selected = null;
    else await read(change.id, revisions(change)[0].source);
  }
  async function read(group_id: string, source: CareSource, more = false) {
    const previous = more ? selected : null;
    const offset = previous?.page.next_offset ?? 0;
    const page = await request("care.read", {
      group_id,
      ...source,
      offset,
      maximum_bytes: 16384,
    });
    if (
      page.reason ||
      page.offset !== offset ||
      (previous && page.sha256 !== previous.page.sha256)
    )
      throw new Error(
        "The exact note page could not be continued. Read it again.",
      );
    selected = {
      group_id,
      source,
      page,
      text: (previous?.text ?? "") + page.content,
    };
  }
  async function undo(group_id: string) {
    if (!pendingUndo) {
      pendingUndo = { group_id, mutation_key: identity() };
      // Persist before dispatch. A lost response retries this same operation.
      localStorage.setItem(undoKey, JSON.stringify(pendingUndo));
    }
    if (pendingUndo.group_id !== group_id)
      throw new Error("Resolve the pending Undo first.");
    try {
      await request("care.undo", pendingUndo);
    } catch (cause) {
      if (
        cause &&
        typeof cause === "object" &&
        "code" in cause &&
        [
          "care_undo_conflict",
          "care_undo_revoked",
          "care_undo_unavailable",
          "care_group_missing",
        ].includes(String(cause.code))
      ) {
        localStorage.removeItem(undoKey);
        pendingUndo = null;
      }
      throw cause;
    }
    localStorage.removeItem(undoKey);
    pendingUndo = null;
    selected = null;
    notice =
      "The original notes were restored. Later conflicting edits are never overwritten.";
    await refresh();
  }
  onMount(() => {
    void run(async () => {
      const raw = localStorage.getItem(undoKey);
      if (raw) {
        const value = JSON.parse(raw);
        if (
          Object.keys(value).sort().join() !== "group_id,mutation_key" ||
          !/^[a-f0-9]{32}$/.test(value.group_id) ||
          !/^[a-f0-9]{32}$/.test(value.mutation_key)
        )
          throw new Error(
            "The saved Undo receipt is invalid; no action was sent.",
          );
        pendingUndo = value;
      }
      await journal.restore();
      await load();
    });
    const timer = setInterval(() => {
      if (job && !working && !busy && !destroyed)
        void refresh().catch((cause) => {
          error = String(cause);
        });
    }, 10000);
    return () => {
      destroyed = true;
      clearInterval(timer);
    };
  });
</script>

<section class="care" aria-label="Daily notebook care">
  <div class="heading">
    <h2>Keep notebook tidy</h2>
    {#if status}<span class="status-label" role="status"
        >{!status.enabled ? "Paused" : labels[status.status]}</span
      >{/if}
  </div>
  <p>
    Clean up existing notes: shorten repetition, merge duplicates and split
    mixed topics. Useful details and originals are kept. Progress resumes
    automatically until the sweep is finished.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}
    <p>A saved settings change needs reconciliation.</p>
    <button
      disabled={working || busy}
      on:click={() =>
        run(async () => {
          await journal.resolve();
          await load();
        })}>Check saved change</button
    >
    {#if absent}<button
        disabled={working || busy}
        on:click={() =>
          run(async () => {
            await journal.retry();
            await load();
          })}>Retry same settings change</button
      >{/if}
  {/if}
  {#if pendingUndo}
    <p>An Undo response is unresolved. Retrying uses the same operation.</p>
    <button
      disabled={working || busy}
      on:click={() => run(() => undo(pendingUndo!.group_id))}>Retry Undo</button
    >
  {/if}
  <fieldset disabled={locked}>
    <label class="choice"
      ><input type="checkbox" bind:checked={draft.enabled} /> Keep notebook tidy daily</label
    >
    <p class="hint">
      {draft.permissions.write_notes
        ? "Automatic cleanup with Undo."
        : "Review only — no notes will change."}
      Cleanup may revise, merge, split and retire current authored notes. Originals
      remain recoverable. Imported notes and rejected notes are excluded.
    </p>
    <div class="actions">
      <button class="primary" on:click={() => run(save)}
        >Save notebook care</button
      >
      <button
        aria-expanded={advanced}
        on:click={() => {
          advanced = !advanced;
        }}>Advanced care settings</button
      >
    </div>
    {#if advanced}
      <div class="advanced">
        <label
          >Mode
          <select
            use:choiceSelect={draft.permissions.write_notes
              ? "automatic"
              : "review"}
            aria-label="Mode"
            value={draft.permissions.write_notes ? "automatic" : "review"}
            on:change={(event) => {
              draft.permissions.write_notes =
                event.currentTarget.value === "automatic";
            }}
          >
            <option value="review">Review only</option>
            <option value="automatic">Automatic cleanup with Undo</option>
          </select>
        </label>

        <label
          >Daily schedule starts ({localZone()})
          <input
            type="text"
            placeholder="YYYY-MM-DD HH:mm"
            bind:value={start}
          /></label
        >

        <p>
          Saved model: {draft.profile.model}. Runs only while Kejtu is open and
          idle.
        </p>
        <button
          on:click={() => {
            draft.profile = structuredClone(profile);
          }}>Use current saved model</button
        >
        <label
          >Model calls per slice <input
            type="number"
            min="1"
            max={JOB_LIMIT_BOUNDS.calls.max}
            bind:value={draft.limits.calls}
          /></label
        >
        <label
          >Groups per slice <input
            type="number"
            min="1"
            max={JOB_LIMIT_BOUNDS.operations.max}
            bind:value={draft.limits.operations}
          /></label
        >
        <label
          >Run time limit (seconds) <input
            type="number"
            min="1"
            max={JOB_LIMIT_BOUNDS.seconds.max}
            bind:value={draft.limits.seconds}
          /></label
        >
        <p class="hint">
          One shared time allowance includes all model requests and the final
          result.
        </p>
        <label
          >Idle seconds before starting <input
            type="number"
            min="30"
            max="86400"
            bind:value={draft.idle_seconds}
          /></label
        >
        <p class="hint">
          Each sweep continues in short batches until every eligible note is
          checked. There is no daily batch cap. Incomplete responses retry
          automatically; repeated failures are shown here without discarding
          progress.
        </p>
      </div>
    {/if}
  </fieldset>
  {#if status}
    <p>
      {checked} current revisions checked · {status.counts.pending ?? 0} remaining
      · {(status.counts.deferred ?? 0) + (status.counts.blocked ?? 0)} need attention
    </p>
    <p class="hint">
      Coverage applies to eligible current notes and supplied groups. It does
      not certify factual accuracy or comparison of every pair.
    </p>
    {#if status.attention}<p>
        Paused after repeated failures: {status.attention}. Review settings and
        save to retry.
      </p>{/if}
    {#if status.retry_at}<p>Retry after {when(status.retry_at)}.</p>{/if}
    {#if status.enabled && status.status === "continuing_later"}<p class="hint">
        Waiting for idle time. Kejtu resumes from the next note; no approval is
        needed.
      </p>{/if}
    {#if (status.counts.blocked ?? 0) + (status.counts.deferred ?? 0) > 0}<p
        class="hint"
      >
        Some notes could not be finished after retries. Their originals are
        safe; other notes keep progressing.
      </p>
      <button disabled={locked} on:click={() => run(save)}
        >Retry unfinished notes</button
      >
    {/if}
    <button
      disabled={working}
      aria-expanded={history}
      on:click={() =>
        run(async () => {
          history = !history;
          if (history) await changePage();
        })}>View changes</button
    >
    <button disabled={working} on:click={() => run(refresh)}
      >Refresh progress</button
    >
  {/if}
  {#if history}
    <ul class="changes">
      {#each changes as change (change.id)}
        <li>
          <strong
            >{change.undone_at
              ? "Undone"
              : change.status.replaceAll("_", " ")}</strong
          >
          · {when(change.created_at)}
          <p>
            {change.checked} checked · {change.revised} revised · {change.produced}
            created · {change.retired} retired
          </p>
          {#if change.reason}<p class="hint">
              {(
                {
                  care_response_incomplete:
                    "The model response was cut off. Retrying with complete originals.",
                  care_plan_invalid:
                    "The model returned an unusable edit plan.",
                  provider_context_limit:
                    "This group did not fit the saved model; retrying a smaller group.",
                  semantic_unchanged: "Checked — no useful change needed.",
                  semantic_defer:
                    "More information is needed before making a change.",
                } as Record<string, string>
              )[change.reason] ?? change.reason.replaceAll("_", " ")}
            </p>{/if}
          <div class="actions">
            {#if change.sources.length || change.outputs.length}
              <button
                disabled={working}
                aria-expanded={selected?.group_id === change.id}
                aria-controls={`care-revision-${change.id}`}
                on:click={() => run(() => toggleRevision(change))}
                >{selected?.group_id === change.id
                  ? "Hide revisions"
                  : "View revisions"}</button
              >
              <span class="hint"
                >{change.sources.length}
                {change.sources.length === 1 ? "original" : "originals"} · {change
                  .outputs.length}
                {change.outputs.length === 1 ? "result" : "results"}</span
              >
            {/if}
            {#if change.status === "changed" && !change.undone_at}<button
                disabled={locked}
                on:click={() => run(() => undo(change.id))}>Undo</button
              >{/if}
          </div>
          {#if selected?.group_id === change.id}
            <!-- Keep exact revision text with its change; the picker stays compact
                 even when a merge/split includes many originals or results. -->
            <article
              id={`care-revision-${change.id}`}
              class="revision-detail"
              aria-label="Exact notebook revision"
            >
              <label>
                Revision
                <select
                  use:choiceSelect={revisionIndex(change, selected?.source)}
                  aria-label="Revision to read"
                  disabled={working}
                  value={revisionIndex(change, selected?.source)}
                  on:change={(event) => {
                    const index = Number(event.currentTarget.value);
                    // Failed reads retain the previously displayed identity and text.
                    event.currentTarget.value = String(
                      revisionIndex(change, selected?.source),
                    );
                    void run(() =>
                      read(change.id, revisions(change)[index].source),
                    );
                  }}
                >
                  {#each revisions(change) as revision, index}
                    <option value={index}>{revision.label}</option>
                  {/each}
                </select>
              </label>
              <pre>{selected.text}</pre>
              {#if selected.page.next_offset !== null}<button
                  disabled={working}
                  on:click={() =>
                    run(() => read(selected!.group_id, selected!.source, true))}
                  >Continue exact note</button
                >{/if}
            </article>
          {/if}
        </li>
      {/each}
    </ul>
    {#if cursor}<button
        disabled={working}
        on:click={() => run(() => changePage(true))}>Older changes</button
      >{/if}
  {/if}
</section>

<style>
  .heading,
  .actions {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  .heading {
    justify-content: space-between;
  }
  fieldset {
    padding: 0;
    border: 0;
    display: grid;
    gap: 0.8rem;
  }
  label {
    display: flex;
    gap: 0.7rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .choice {
    font-weight: 600;
  }
  .advanced {
    display: grid;
    gap: 0.8rem;
    padding: 0.8rem 0;
  }
  .changes {
    padding: 0;
    list-style: none;
  }
  .changes li {
    border-top: 1px solid var(--ke-border);
    padding: 1rem 0;
  }
  .revision-detail {
    padding: 1rem 0 0.25rem;
  }
  .revision-detail select {
    width: min(100%, 24rem);
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    line-height: 1.5;
    max-height: 28rem;
    overflow: auto;
  }
</style>
