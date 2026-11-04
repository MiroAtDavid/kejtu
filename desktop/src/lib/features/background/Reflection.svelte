<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount } from "svelte";
  import {
    DEFAULT_JOB_LIMITS,
    JOB_LIMIT_BOUNDS,
  } from "$lib/api/wire.generated";
  import { request, identity } from "$lib/api/client";
  import { JobJournal } from "./journal";
  import {
    newJob,
    localZone,
    startInput,
    scheduleTime,
    when,
    runLabel,
    privateResult,
  } from "./editor";
  import type {
    Profile,
    JobRecord,
    JobDefinition,
    JobRun,
    JobBody,
  } from "$lib/api/wire.generated";
  export let stateId: string;
  export let profile: Profile;
  export let busy = false;
  export let navigationLocked = false;
  const selectionKey = `kejtu-next.reflection-job.v1.${stateId}`;
  const journal = new JobJournal(
    stateId + ".reflection",
    request,
    (receipt) => {
      // Retain the committed identity before releasing the mutation receipt.
      // A failed read-back must never turn the next Save into a second job.
      localStorage.setItem(selectionKey, receipt.job_id);
    },
  );
  let job: JobRecord | null = null;
  let draft: JobDefinition = {
    ...newJob(profile),
    kind: "idle_review",
    title: "Reflection",
    enabled: true,
    idle_seconds: 60,
    permissions: { read_memory: true, write_notes: true, deliver: false },
    limits: { ...DEFAULT_JOB_LIMITS.idle_review },
  };
  draft.schedule.repeat = "daily";
  let start = startInput(draft.schedule.starts_at, localZone());
  let working = false,
    ready = false,
    pending = false,
    absent = false,
    advanced = false,
    history = false;
  let error = "",
    notice = "";
  let runs: JobRun[] = [];
  let runCursor: string | null = null;
  let body: JobBody | null = null;
  let selectedRun: string | null = null;
  let destroyed = false;
  $: navigationLocked = working || pending;
  $: disabled = busy || !ready || navigationLocked;
  $: report = privateResult(body);
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
  async function load() {
    ready = false;
    const pinned = localStorage.getItem(selectionKey);
    if (pinned) {
      if (!/^[a-f0-9]{32}$/.test(pinned))
        throw new Error("The saved reflection identity is invalid.");
      const result = await request("jobs.read", { job_id: pinned });
      if (result.definition.kind !== "idle_review")
        throw new Error("The saved job is not a reflection.");
      if (!result.job.cancelled) {
        if (destroyed) return;
        job = result.job;
        draft = structuredClone(result.definition);
        start = startInput(draft.schedule.starts_at, localZone());
        ready = true;
        return;
      }
      localStorage.removeItem(selectionKey);
    }
    let cursor: string | null = null;
    let found: JobRecord | undefined;
    do {
      const page = await request("jobs.list", { cursor, page_size: 50 });
      found = page.items.find(
        (item) => item.kind === "idle_review" && !item.cancelled,
      );
      cursor = page.next_cursor;
    } while (!found && cursor);
    if (destroyed) return;
    job = found ?? null;
    if (job) {
      const result = await request("jobs.read", { job_id: job.id });
      localStorage.setItem(selectionKey, job.id);
      draft = structuredClone(result.definition);
      start = startInput(draft.schedule.starts_at, localZone());
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
    ready = false;
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
    await load();
    notice = draft.enabled
      ? "Reflection saved. It runs while Kejtu is open and idle."
      : "Reflection paused.";
  }
  async function results(more = false) {
    if (!job) return;
    const page = await request("jobs.runs", {
      job_id: job.id,
      cursor: more ? runCursor : null,
    });
    runs = more ? [...runs, ...page.items] : page.items;
    runCursor = page.next_cursor;
  }
  async function read(id: string, offset = 0) {
    const page = await request("jobs.result", {
      run_id: id,
      offset,
      maximum_bytes: 16384,
    });
    if (
      page.reason ||
      page.offset !== offset ||
      (offset && body?.sha256 !== page.sha256)
    )
      throw new Error(
        "The exact report could not be continued. Read it again.",
      );
    body = page;
    selectedRun = id;
  }
  onMount(() => {
    void run(async () => {
      await journal.restore();
      await load();
    });
    return () => {
      destroyed = true;
    };
  });
</script>

<section aria-label="Reflection settings">
  <h2>Reflection</h2>
  <p>
    Consider useful connections, observations and open questions from accessible
    notes. Reflection can add a new thought or update a note; it can also decide
    there is nothing useful to save.
  </p>
  <p class="hint">
    These are Kejtu’s observations, not verified facts. Reflection does not send
    messages, carry out tasks or replace Notebook tidy.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if !ready && !pending && !working}<button on:click={() => run(load)}
      >Reload saved reflection</button
    >{/if}
  {#if pending}
    <p>A settings save needs its recorded outcome checked.</p>
    <button
      disabled={working || busy}
      on:click={() =>
        run(async () => {
          await journal.resolve();
          await load();
        })}>Check saved reflection</button
    >
    {#if absent}
      <button
        disabled={working || busy}
        on:click={() =>
          run(async () => {
            await journal.retry();
            await load();
          })}>Retry same reflection save</button
      >
      <button
        disabled={working}
        on:click={() =>
          run(async () => {
            journal.release();
            await load();
          })}>Release uncommitted choice</button
      >
    {/if}
  {/if}
  <fieldset {disabled}>
    <div class="reflection-options">
      <label class="option-row">
        <span>Reflect while idle</span>
        <span class="switch-control"
          ><input type="checkbox" bind:checked={draft.enabled} /></span
        >
      </label>
      <label class="option-row">
        <span>How often</span>
        <span class="frequency">
          <select
            use:choiceSelect={draft.schedule.repeat}
            bind:value={draft.schedule.repeat}
          >
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="none">Once</option>
          </select>
        </span>
      </label>
      <label class="option-row">
        <span>Save useful observations in the notebook</span>
        <span class="switch-control"
          ><input
            type="checkbox"
            bind:checked={draft.permissions.write_notes}
          /></span
        >
      </label>
    </div>
    <div class="actions">
      <button class="primary" on:click={() => run(save)}>Save reflection</button
      ><button aria-expanded={advanced} on:click={() => (advanced = !advanced)}
        >Advanced reflection settings</button
      >
    </div>
    {#if advanced}
      <label
        >Start ({localZone()}) <input type="text" bind:value={start} /></label
      >
      <label
        >Idle seconds <input
          type="number"
          min="30"
          max="86400"
          bind:value={draft.idle_seconds}
        /></label
      >
      <p>Saved model: {draft.profile.model}</p>
      <button on:click={() => (draft.profile = structuredClone(profile))}
        >Use current saved model</button
      >
      <label
        >Model requests per run <input
          type="number"
          min="1"
          max={JOB_LIMIT_BOUNDS.calls.max}
          bind:value={draft.limits.calls}
        /></label
      >
      <label
        >Tool operations per run <input
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
      <p class="hint">
        Each reflection ends with a private report. Broader custom jobs and
        their history are under Advanced.
      </p>
    {/if}
  </fieldset>
  {#if job}
    <button
      disabled={working}
      aria-expanded={history}
      on:click={() =>
        run(async () => {
          history = !history;
          if (history) await results();
        })}>View reflection results</button
    >
  {/if}
  {#if history}
    <div class="actions">
      <button disabled={working} on:click={() => run(() => results())}
        >Refresh results</button
      >
    </div>
    {#if !runs.length}<p>No reflection has run yet.</p>{/if}
    <ul>
      {#each runs as item}<li>
          {when(item.started_at || item.scheduled_at)} · {runLabel(
            item.status,
            item.error_code,
          )}
          {#if item.result_id}<button
              disabled={working}
              on:click={() => run(() => read(item.id))}>Read report</button
            >{/if}
        </li>{/each}
    </ul>
    {#if runCursor}<button
        disabled={working}
        on:click={() => run(() => results(true))}>Older results</button
      >{/if}
    {#if body}<article aria-label="Reflection report">
        <pre>{report ?? body.content}</pre>
        {#if body.next_offset !== null}<button
            disabled={working}
            on:click={() => run(() => read(selectedRun!, body!.next_offset!))}
            >Continue report</button
          >{/if}
      </article>{/if}
  {/if}
</section>

<style>
  fieldset {
    display: grid;
    gap: 1.1rem;
  }
  label {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.6rem;
  }
  .reflection-options {
    max-width: 36rem;
  }
  .reflection-options .option-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 20px;
    min-height: 64px;
    padding: 12px 0;
    margin: 0;
    border-bottom: 1px solid var(--ke-border);
  }
  .switch-control,
  .frequency {
    justify-self: end;
  }
  .switch-control {
    display: flex;
  }
  .frequency {
    width: 112px;
  }
  .hint {
    color: var(--ke-text-secondary);
  }
  li {
    margin: 0.8rem 0;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    line-height: 1.6;
    max-height: 28rem;
    overflow: auto;
  }
</style>
