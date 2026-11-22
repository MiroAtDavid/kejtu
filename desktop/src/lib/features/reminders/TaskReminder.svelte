<script lang="ts">
  import { onMount } from "svelte";
  import { identity } from "$lib/api/client";
  import type { TaskRecord } from "$lib/api/wire.generated";
  import { civilInstant } from "$lib/features/workspace/workspace";
  import { ReminderSave } from "./task-reminder";
  export let stateId: string;
  export let task: TaskRecord;
  export let timezone: string;
  export let disabled = false;
  export let locked = false;
  let open = false,
    at = "",
    busy = false,
    pending = false,
    absent = false,
    error = "",
    notice = "";
  const key = `kejtu-next.task-reminder.v1.${stateId}.${task.id}`;
  const journal = new ReminderSave(key);
  $: locked = busy || pending;
  async function run(work: () => Promise<unknown>) {
    busy = true;
    error = "";
    try {
      await work();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
      pending = !!journal.pending;
      absent = journal.absent;
    }
  }
  function draft(value: string) {
    at = value;
    localStorage.setItem(key + ".draft", at);
  }
  function recovered() {
    notice = "The reminder was saved.";
    localStorage.removeItem(key + ".draft");
    at = "";
    open = false;
  }
  async function save() {
    const starts = civilInstant(at, timezone);
    if (Date.parse(starts) <= Date.now())
      throw new Error("Choose a future reminder time.");
    await journal.begin({
      mutation_key: identity(),
      reactivate: false,
      definition: {
        title: task.title,
        text: task.title,
        enabled: true,
        support: [],
        note_id: null,
        task_id: task.id,
        task_revision: task.revision,
        schedule: {
          starts_at: starts,
          timezone,
          repeat: "none",
          interval: 1,
          catch_up: "coalesce",
          grace_seconds: 0,
        },
      },
    });
    notice = `Reminder saved for ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(new Date(starts))} (${timezone}).`;
    localStorage.removeItem(key + ".draft");
    at = "";
    open = false;
  }
  onMount(() => {
    at = localStorage.getItem(key + ".draft") ?? "";
    open = !!at || !!localStorage.getItem(key);
    if (localStorage.getItem(key))
      void run(async () => {
        const saved = await journal.restore();
        if (saved) recovered();
      });
  });
</script>

<div class="task-reminder">
  <button
    type="button"
    disabled={disabled || locked}
    aria-expanded={open}
    on:click={() => (open = !open)}>Remind me</button
  >
  {#if open || pending}
    <form on:submit|preventDefault={() => run(save)}>
      <label
        >Reminder time <input
          type="datetime-local"
          required
          value={at}
          disabled={disabled || locked}
          on:input={(e) => draft(e.currentTarget.value)}
        /></label
      >
      <small>{timezone}. The task’s date stays unchanged.</small>
      <button disabled={disabled || locked || !at}>Save reminder</button>
    </form>
  {/if}
  {#if error}<p role="status">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}
    <button
      disabled={busy}
      on:click={() =>
        run(async () => {
          const saved = await journal.resolve();
          if (saved) recovered();
        })}>Check status</button
    >
    {#if absent}<button
        disabled={busy}
        on:click={() => {
          journal.keep();
          pending = false;
          absent = false;
        }}>Keep unchanged</button
      >{/if}
  {/if}
</div>

<style>
  /* Sits in the task page's details card; controls take the page's look. */
  .task-reminder {
    min-width: 0;
  }
  form {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px;
    margin: 8px 0 0;
  }
  label {
    display: grid;
    flex: 1 1 100%;
    gap: 4px;
    color: var(--ke-text-tertiary);
    font-size: 11.5px;
  }
  input {
    width: 100%;
  }
  small,
  p {
    margin: 6px 0 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  small {
    flex: 1 1 100%;
    margin: 0;
  }
</style>
