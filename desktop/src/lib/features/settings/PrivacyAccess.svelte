<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import type { AccessSettings, Arguments } from "$lib/api/wire.generated";
  import { choiceSelect } from "$lib/design/choice-select";
  import PreferenceRecall from "../notebook/PreferenceRecall.svelte";
  import AccessRecovery from "./AccessRecovery.svelte";

  export let stateId: string;
  export let busy = false;
  export let locked = false;
  export let onOpen: (
    panel: "files" | "web" | "connections" | "workspace" | "notes",
  ) => void = () => {};
  export let hasConversation = false;
  type Feature = "people" | "reminders" | "tasks";
  const features: { id: Feature; title: string; description: string }[] = [
    {
      id: "people",
      title: "People & relationships",
      description:
        "Recall people you have mentioned, or also remember new details about them.",
    },
    {
      id: "reminders",
      title: "Reminders",
      description:
        "Let Kejtu read and manage reminders you ask for. Desktop notification pop-ups are not connected yet.",
    },
    {
      id: "tasks",
      title: "Personal tasks",
      description:
        "Let Kejtu read and manage your task list. Local task controls remain available when this is off.",
    },
  ];
  const destinations = [
    {
      id: "files" as const,
      label: "Files & folders",
      hint: "Choose shared folders and file permissions",
    },
    {
      id: "web" as const,
      label: "Web access",
      hint: "Search, browsing and providers",
    },
    {
      id: "connections" as const,
      label: "Connected tools",
      hint: "Browser and other tool permissions",
    },
    {
      id: "workspace" as const,
      label: "Study & workspace",
      hint: "Study features, calendars and workspace access",
    },
  ];
  const journal = `kejtu-next.access-settings.v1.${stateId}`;
  let saved: AccessSettings | null = null;
  let choices: Record<Feature, string> = {
    people: "",
    reminders: "",
    tasks: "",
  };
  let working = true,
    uncertain = false,
    destroyed = false;
  let preferenceLock = false,
    recoveryLock = false;
  let error = "",
    notice = "";
  $: editing = saved
    ? features.find(({ id }) => choices[id] !== (saved![id] ?? ""))?.id
    : undefined;
  $: locked =
    working || uncertain || !!editing || preferenceLock || recoveryLock;
  $: blocked = busy || working || uncertain || preferenceLock || recoveryLock;

  function accept(value: AccessSettings) {
    saved = value;
    choices = {
      people: value.people ?? "",
      reminders: value.reminders ?? "",
      tasks: value.tasks ?? "",
    };
  }
  async function load() {
    if (destroyed) return;
    working = true;
    error = "";
    try {
      const pending = localStorage.getItem(journal);
      const value = await request("access.read", {});
      if (destroyed) return;
      // Read-back establishes current authority, including a lost save reply.
      // It never treats an old browser choice as authority or resends it.
      if (localStorage.getItem(journal) !== pending)
        throw new Error(
          "Another access change is pending. Check saved access again.",
        );
      if (pending !== null) localStorage.removeItem(journal);
      accept(value);
      if (uncertain || pending !== null)
        notice =
          "Saved access checked. The choices below are current; nothing was retried.";
      uncertain = false;
    } catch (caught) {
      if (!destroyed) {
        error = String(caught);
        uncertain = localStorage.getItem(journal) !== null;
      }
    } finally {
      if (!destroyed) working = false;
    }
  }
  async function save(feature: Feature) {
    if (blocked || !saved || editing !== feature || !choices[feature]) return;
    const args = {
      feature,
      value: choices[feature],
      expected_revision: saved.revision,
    } as Arguments["access.save"];
    working = true;
    notice = error = "";
    try {
      if (localStorage.getItem(journal) !== null)
        throw new Error("Check the previous access change first.");
      const pending = JSON.stringify(args);
      localStorage.setItem(journal, pending);
      const value = await request("access.save", args);
      if (destroyed) return;
      if (localStorage.getItem(journal) !== pending)
        throw new Error("Access recovery changed. Check saved access.");
      localStorage.removeItem(journal);
      accept(value);
      notice = `${features.find((item) => item.id === feature)!.title} saved for all conversations.`;
    } catch (caught) {
      if (!destroyed) {
        uncertain = true;
        error = `${String(caught)} Check saved access before trying again.`;
      }
    } finally {
      if (!destroyed) working = false;
    }
  }
  function discard() {
    if (!working && !uncertain && saved) accept(saved);
  }
  function open(panel: Parameters<typeof onOpen>[0]) {
    if (!locked && !busy) onOpen(panel);
  }
  onMount(() => {
    void load();
    return () => {
      destroyed = true;
    };
  });
</script>

<section aria-label="Privacy and access">
  <h2 class="settings-title">Privacy &amp; access</h2>
  <p class="panel-intro">
    Choose what Kejtu can use. Save once for existing and new conversations;
    your choices stay after a restart.
  </p>
  <AccessRecovery {stateId} {busy} bind:locked={recoveryLock} />
  <h3>Across your conversations</h3>
  <p class="muted">
    Until you choose an app-wide setting, each conversation keeps its previous
    permissions. No access is enabled automatically.
  </p>
  <div class="access-list">
    {#each features as feature (feature.id)}
      <div class="access-row">
        <div>
          <h4>{feature.title}</h4>
          <p class="muted">{feature.description}</p>
        </div>
        <div class="access-control">
          <label
            ><span class="control-label">{feature.title}</span>
            <select
              aria-label={`${feature.title} access`}
              use:choiceSelect={choices[feature.id]}
              bind:value={choices[feature.id]}
              disabled={!saved ||
                blocked ||
                (!!editing && editing !== feature.id)}
            >
              {#if !saved || saved[feature.id] === null}<option value=""
                  >Previous conversation choices</option
                >{/if}
              <option value="off">Off</option>
              {#if feature.id === "people"}<option value="read"
                  >Recall only</option
                ><option value="write">Recall and remember</option>
              {:else}<option value="on">On</option>{/if}
            </select>
          </label>
          {#if editing === feature.id}
            <div class="row-actions">
              <button disabled={blocked} on:click={() => save(feature.id)}
                >Save {feature.title === "People & relationships"
                  ? "People"
                  : feature.title}</button
              >
              <button
                class="plain"
                disabled={working || uncertain}
                on:click={discard}>Discard change</button
              >
            </div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
  <p class="muted">
    These choices keep existing memory restrictions. A conversation without
    memory access stays restricted. Task editing also needs note-writing access.
    Turning a feature off does not delete its records or cancel existing
    schedules.
  </p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if error || uncertain}<button disabled={working || busy} on:click={load}
      >Check saved access</button
    >{/if}

  <PreferenceRecall
    busy={busy || working || uncertain || !!editing || recoveryLock}
    bind:locked={preferenceLock}
  />
  {#if hasConversation}<button
      class="plain"
      disabled={locked || busy}
      on:click={() => open("notes")}>Review saved notes →</button
    >{/if}
  <h3>Other access settings</h3>
  <p class="muted">
    These settings already stay saved. Open the relevant page to review or
    change them.
  </p>
  <div class="access-links">
    {#each destinations as destination}<button
        class="plain"
        disabled={locked || busy}
        on:click={() => open(destination.id)}
        ><span
          ><strong>{destination.label}</strong><small>{destination.hint}</small
          ></span
        ><span aria-hidden="true">›</span></button
      >{/each}
  </div>
</section>

<style>
  .control-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  h4 {
    font-size: 13px;
    font-weight: 500;
    margin: 0 0 5px;
  }
  .access-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 240px;
    gap: 30px;
    padding: 22px 0;
    border-bottom: 1px solid var(--ke-border);
  }
  .access-row:first-child {
    border-top: 1px solid var(--ke-border);
  }
  .access-row p {
    margin: 0;
  }
  .access-control label {
    display: grid;
    gap: 7px;
  }
  .row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 10px;
  }
  .access-links {
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 30px;
  }
  .access-links button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    text-align: left;
    padding: 16px 0;
    border: 0;
    border-top: 1px solid var(--ke-border);
    border-radius: 0;
  }
  strong {
    font-weight: 500;
  }
  small {
    display: block;
    margin-top: 3px;
  }
  @media (max-width: 800px) {
    .access-row {
      grid-template-columns: 1fr;
      gap: 12px;
    }
    .access-control {
      max-width: 300px;
    }
    .access-links {
      grid-template-columns: 1fr;
    }
  }
</style>
