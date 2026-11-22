<script lang="ts">
  import { onMount } from "svelte";
  import { request, identity } from "$lib/api/client";
  import {
    DEFAULT_JOB_LIMITS,
    type Profile,
    type JobRecord,
    type JobRun,
    type JobDefinition,
    type InterestItem,
  } from "$lib/api/wire.generated";
  import { JobJournal } from "./journal";
  import { newJob, when, runLabel } from "./editor";

  export let stateId: string;
  export let profile: Profile;
  export let busy = false;
  export let navigationLocked = false;
  const journal = new JobJournal(stateId + ".interests");
  let job: JobRecord | null = null;
  let lastRun: JobRun | null = null;
  let draft: JobDefinition = {
    ...newJob(profile),
    kind: "interest_following",
    title: "Follow interests",
    conversation_id: null,
    enabled: false,
    idle_seconds: 60,
    delivery: "quiet",
    permissions: { read_memory: true, write_notes: false, deliver: false },
    limits: { ...DEFAULT_JOB_LIMITS.interest_following },
  };
  draft.schedule.repeat = "minutes";
  draft.schedule.interval = 60;
  let items: InterestItem[] = [];
  let working = false,
    ready = false,
    pending = false,
    absent = false;
  let error = "",
    notice = "";
  $: navigationLocked = working || pending;
  $: disabled = busy || !ready || navigationLocked;

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
    let cursor: string | null = null;
    let found: JobRecord | undefined;
    do {
      const page = await request("jobs.list", { cursor, page_size: 50 });
      found = page.items.find(
        (item) => item.kind === "interest_following" && !item.cancelled,
      );
      cursor = page.next_cursor;
    } while (!found && cursor);
    job = found ?? null;
    if (job)
      draft = structuredClone(
        (await request("jobs.read", { job_id: job.id })).definition,
      );
    lastRun = job
      ? ((
          await request("jobs.runs", {
            job_id: job.id,
            cursor: null,
            page_size: 1,
          })
        ).items[0] ?? null)
      : null;
    items = (await request("interests.list", {})).items;
    ready = true;
  }

  async function save() {
    const definition = structuredClone(draft);
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
      ? "Interest following saved. Checks run while Kejtu is open and idle."
      : "Interest following is off.";
  }

  async function change(item: InterestItem, action: "stop" | "later") {
    try {
      await request("interests.change", {
        ident: item.id,
        expected_revision: item.revision,
        action,
        mutation_key: identity(),
      });
      notice =
        action === "stop"
          ? `Stopped following ${item.topic}.`
          : `${item.topic} will stay quiet for a week.`;
    } finally {
      // A lost response is resolved by reading the saved state, never by
      // automatically sending another mutation with a new identity.
      await load();
    }
  }

  onMount(() => {
    void run(async () => {
      await journal.restore();
      await load();
    });
  });
</script>

<section aria-label="Interest following settings">
  <h2>Interests</h2>
  <p>
    Let Kejtu occasionally check public information about interests you have
    shared, and bring up a useful find when a conversation leaves room for it.
  </p>
  <p class="hint">
    Starts off. Enabling this permits checks based on accessible “about you”
    notes with supporting words from you. It does not send messages on its own
    or access email, files or accounts.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}
    <p>A settings save needs its recorded outcome checked.</p>
    <button
      disabled={working || busy}
      on:click={() =>
        run(async () => {
          await journal.resolve();
          await load();
        })}>Check saved setting</button
    >
    {#if absent}
      <button
        disabled={working || busy}
        on:click={() =>
          run(async () => {
            await journal.retry();
            await load();
          })}>Retry same save</button
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
    <label class="option-row"
      ><span>Follow my interests</span><input
        type="checkbox"
        bind:checked={draft.enabled}
      /></label
    >
    <label class="option-row"
      ><span>Checks per day, shared across all interests</span><input
        aria-label="Shared daily checks"
        type="number"
        min="1"
        max="3"
        bind:value={draft.limits.runs_per_day}
      /></label
    >
    <p class="hint">
      Each check allows up to two model requests, one search and two page reads
      within two minutes, using the saved model’s limits. Failed and interrupted
      checks still count. This limits work, not a guaranteed price.
    </p>
    <p class="hint">
      Search providers receive a public topic, such as “Formula 1”, rather than
      your supporting personal quote. Your configured model receives the
      eligible note and source evidence. Web access must also be enabled.
    </p>
    <p class="hint">
      At most one aside per day, with at least a week between mentions of a
      topic. There is no daily target. Findings expire; a failed check produces
      no announcement.
    </p>
    <p>Saved model: {draft.profile.model}</p>
    <div class="actions">
      <button on:click={() => (draft.profile = structuredClone(profile))}
        >Use current saved model</button
      >
      <button class="primary" on:click={() => run(save)}>Save interests</button>
    </div>
  </fieldset>
  <div class="list-heading">
    <h3>What Kejtu follows</h3>
    <button disabled={working || pending} on:click={() => run(load)}
      >Refresh</button
    >
  </div>
  {#if lastRun}<p class="hint">
      Last check: {runLabel(lastRun.status)}{lastRun.finished_at
        ? ` · ${when(lastRun.finished_at)}`
        : ""}.{#if lastRun.status === "failed"}
        No new finding was announced.{/if}
    </p>{/if}
  {#if ready && !items.length}<p class="hint">
      No supported interests have been followed yet. A note without clear
      supporting user evidence is left alone.
    </p>{/if}
  <ul>
    {#each items as item (item.id)}
      <li>
        <strong>{item.topic}</strong>{#if item.stopped}<span class="hint">
            · Stopped</span
          >{/if}
        <blockquote>{item.quote}</blockquote>
        <p class="hint">
          {item.checked_at
            ? `Last checked ${when(item.checked_at)}.`
            : "No completed check yet."}
          {#if !item.stopped}Next eligible check: {when(item.next_at)}.{/if}
        </p>
        {#if !item.stopped}<div class="actions">
            <button {disabled} on:click={() => run(() => change(item, "later"))}
              >Not now</button
            >
            <button {disabled} on:click={() => run(() => change(item, "stop"))}
              >Stop following</button
            >
          </div>{/if}
      </li>
    {/each}
  </ul>
</section>

<style>
  fieldset {
    display: grid;
    gap: 1rem;
  }
  .option-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    min-height: 3rem;
    max-width: 38rem;
  }
  input[type="number"] {
    width: 5rem;
  }
  .hint {
    color: var(--ke-text-secondary);
    max-width: 72ch;
  }
  .actions,
  .list-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  .list-heading {
    margin-top: 2rem;
  }
  ul {
    list-style: none;
    padding: 0;
  }
  li {
    padding: 1rem 0;
    border-bottom: 1px solid var(--ke-border);
  }
  blockquote {
    margin: 0.6rem 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
