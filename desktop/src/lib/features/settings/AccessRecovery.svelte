<script lang="ts">
  import PeopleAccess from "../people/PeopleAccess.svelte";
  import ReminderAccess from "../reminders/ReminderAccess.svelte";
  import TaskAccess from "../tasks/TaskAccess.svelte";

  export let stateId: string;
  export let busy = false;
  export let locked = false;
  // Read only the existing receipt journals for this archive. Recovery never
  // issues a fresh permission choice; an absent intent can only be released.
  const pending = Object.keys(localStorage).flatMap((key) => {
    for (const feature of ["people", "reminder", "task"] as const) {
      const prefix = `kejtu-next.${feature}-access.v1.${stateId}.`;
      const conversationId = key.slice(prefix.length);
      if (key.startsWith(prefix) && /^[0-9a-f]{32}$/.test(conversationId))
        return [{ key, feature, conversationId, locked: true }];
    }
    return [];
  });
  $: locked = pending.some((item) => item.locked);
</script>

{#if pending.length}
  <section aria-label="Earlier access changes">
    <h3>Earlier access changes</h3>
    <p class="muted">
      Check any unfinished conversation choices before setting access for all
      chats. Nothing is retried.
    </p>
    {#each pending as item (item.key)}
      <details open={item.locked}>
        <summary
          >{item.feature === "people"
            ? "People"
            : item.feature === "reminder"
              ? "Reminders"
              : "Tasks"} · conversation {item.conversationId.slice(
            0,
            8,
          )}</summary
        >
        {#if item.feature === "people"}
          <PeopleAccess
            {stateId}
            conversationId={item.conversationId}
            {busy}
            recoveryOnly
            bind:locked={item.locked}
          />
        {:else if item.feature === "reminder"}
          <ReminderAccess
            {stateId}
            conversationId={item.conversationId}
            {busy}
            recoveryOnly
            bind:locked={item.locked}
          />
        {:else}
          <TaskAccess
            {stateId}
            conversationId={item.conversationId}
            {busy}
            recoveryOnly
            bind:locked={item.locked}
          />
        {/if}
      </details>
    {/each}
  </section>
{/if}
