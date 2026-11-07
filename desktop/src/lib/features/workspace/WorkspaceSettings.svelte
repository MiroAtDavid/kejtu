<script lang="ts">
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import fallbackTimezones from "$lib/design/timezones.json";
  import { workspaceRequest, type Workspace, type Settings } from "./workspace";

  export let workspace: Workspace;
  export let onArrange: () => void = () => {};
  export let onMedia: () => void = () => {};
  export let busy = false;
  let draft: Settings | null = null,
    revision = -1,
    layoutRevision = -1,
    dirty = false,
    error = "";
  const computerTimezone =
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  let knownTimezones: string[];
  try {
    knownTimezones = (
      Intl as typeof Intl & { supportedValuesOf(key: string): string[] }
    ).supportedValuesOf("timeZone");
  } catch {
    knownTimezones = fallbackTimezones;
  }
  $: timezones = Array.from(
    new Set([
      "UTC",
      computerTimezone,
      draft?.availability.timezone || "UTC",
      ...knownTimezones,
    ]),
  )
    .sort()
    .map((value) => ({ value, label: value.replaceAll("_", " ") }));
  function chooseTimezone(value: string) {
    if (!draft || locked) return;
    draft.availability.timezone = value;
    dirty = true;
  }
  let calendars: { id: string; name: string; selected: boolean }[] = [];
  let loadingCalendars = false;
  $: access = draft?.model_read ? (draft.model_write ? "edit" : "read") : "off";
  function chooseAccess(value: string) {
    if (!draft || locked) return;
    draft.model_read = value !== "off";
    draft.model_write = value === "edit";
    dirty = true;
  }
  function recommended() {
    if (!draft || locked) return;
    // This explicit staged preset never replaces saved permissions at startup.
    // Media, external-calendar grants and widgets keep their existing owners.
    draft = {
      ...draft,
      university_enabled: true,
      model_read: true,
      model_write: true,
      availability: {
        timezone: computerTimezone,
        buffer_minutes: 15,
        windows: [],
      },
    };
    dirty = true;
  }
  $: if (
    $workspace.snapshot &&
    !dirty &&
    (revision !== $workspace.snapshot.revision ||
      layoutRevision !== $workspace.snapshot.layout_revision)
  ) {
    draft = structuredClone($workspace.snapshot.settings);
    // Weekly windows are retired. An ordinary settings save must not perpetuate
    // an invisible legacy restriction; loading this form writes nothing.
    draft.availability.windows = [];
    if (
      $workspace.snapshot.revision === 0 &&
      !draft.university_enabled &&
      draft.availability.timezone === "UTC"
    )
      draft.availability.timezone =
        Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    revision = $workspace.snapshot.revision;
    layoutRevision = $workspace.snapshot.layout_revision;
  }
  $: locked = busy || $workspace.busy || !!$workspace.pending;
  async function save() {
    if (!draft || locked) return false;
    if (!draft.model_read) draft.model_write = false;
    const result = await workspace.mutate("settings.save", {
      settings: { ...draft, widgets: $workspace.snapshot!.settings.widgets },
      expected_revision: revision,
      expected_layout_revision: $workspace.snapshot!.layout_revision,
    });
    if (result) {
      dirty = false;
      revision = -1;
    }
    return !!result;
  }
  async function listCalendars() {
    if (locked || loadingCalendars || !draft?.calendar_enabled) return;
    error = "";
    // The button explicitly says when finding calendars also saves this form.
    if (!$workspace.snapshot?.settings.calendar_enabled && !(await save()))
      return;
    loadingCalendars = true;
    try {
      calendars = (
        await workspaceRequest<{ calendars: typeof calendars }>(
          "calendars.list",
        )
      ).calendars;
    } catch (caught) {
      error = String(caught);
    } finally {
      loadingCalendars = false;
    }
  }
</script>

<section class="uni workspace-settings" aria-label="Workspace settings">
  <h2 class="settings-title">Workspace</h2>
  <p class="muted">Projects, planning and the tools you keep close at hand.</p>
  {#if draft}
    <form
      on:submit|preventDefault={save}
      on:input={() => (dirty = true)}
      on:change={() => (dirty = true)}
    >
      <fieldset disabled={locked}>
        <legend>Projects &amp; planning</legend>
        <label
          ><input type="checkbox" bind:checked={draft.university_enabled} />Show
          Projects</label
        >
        <ChoicePicker
          id="workspace-conversation-access"
          label="Kejtu’s help in conversation"
          labelOnly
          value={access}
          options={[
            { value: "edit", label: "Read and update" },
            { value: "read", label: "Read only" },
            { value: "off", label: "Off" },
          ]}
          disabled={locked}
          onChange={chooseAccess}
        />
        <p class="hint">
          Let Kejtu work with your projects and schedule when you ask. Shared
          folders use the access you choose in Files.
        </p>
        <div class="row">
          <div class="timezone-field">
            <ChoicePicker
              id="workspace-timezone"
              label="Timezone"
              labelOnly
              value={draft.availability.timezone}
              options={timezones}
              searchable
              disabled={locked}
              required
              onChange={chooseTimezone}
            />
          </div>
          <button
            class="computer-zone"
            type="button"
            disabled={locked ||
              draft.availability.timezone === computerTimezone}
            on:click={() => chooseTimezone(computerTimezone)}
            >Use computer timezone</button
          >
        </div>
        <p class="hint">
          Your Kejtu calendar works without connecting another calendar.
        </p>
        <div class="recommended">
          <button type="button" disabled={locked} on:click={recommended}
            >Use recommended setup</button
          >
          <p class="hint">
            Projects on, Kejtu able to read and update, computer timezone and a
            15-minute planning buffer. Save below to apply.
          </p>
        </div>
      </fieldset>
      <fieldset disabled={locked}>
        <legend>Widgets</legend>
        <p class="hint">
          Choose what appears beside your conversations and projects.
        </p>
        <button type="button" disabled={locked} on:click={onArrange}
          >Edit widgets</button
        >
      </fieldset>
      <details class="workspace-options">
        <summary>Media &amp; external calendars</summary>
        <fieldset disabled={locked}>
          <legend>Media</legend>
          <label
            ><input
              type="checkbox"
              bind:checked={draft.radio_enabled}
            />Radio</label
          >
          <label
            ><input
              type="checkbox"
              bind:checked={draft.video_enabled}
            />Video</label
          >
          <p class="hint">
            Use the installed media player. Turning these on does not start
            playback. Conversational access also lets Kejtu control enabled
            media.
          </p>
          {#if $workspace.snapshot?.settings.radio_enabled || $workspace.snapshot?.settings.video_enabled}
            <button type="button" disabled={locked} on:click={onMedia}
              >Open media player</button
            >
          {/if}
        </fieldset>
        <fieldset disabled={locked}>
          <legend>External calendars</legend>
          <label
            ><input type="checkbox" bind:checked={draft.calendar_enabled} />Show
            desktop calendar appointments</label
          >
          {#if draft.calendar_enabled}
            <p class="hint">
              Read-only appointments from your desktop calendar service.
            </p>
            <button
              type="button"
              disabled={locked || loadingCalendars}
              on:click={listCalendars}
            >
              {loadingCalendars
                ? "Finding calendars…"
                : $workspace.snapshot?.settings.calendar_enabled
                  ? "Choose calendars"
                  : "Save and find calendars"}</button
            >
            {#each calendars as calendar}
              <label
                ><input
                  type="checkbox"
                  value={calendar.id}
                  bind:group={draft.calendars}
                />{calendar.name}</label
              >
            {/each}
            {#if !calendars.length && draft.calendars.length}
              <p class="hint">
                {draft.calendars.length} saved calendar{draft.calendars
                  .length === 1
                  ? ""
                  : "s"} selected.
              </p>
            {/if}
          {/if}
        </fieldset>
      </details>
      <details class="workspace-options">
        <summary>Planning preferences</summary>
        <fieldset disabled={locked}>
          <label class="buffer-field"
            >Break / travel time (minutes)<input
              type="number"
              min="0"
              max="240"
              bind:value={draft.availability.buffer_minutes}
            /></label
          >
          <p class="hint">Leave this much time between planned commitments.</p>
        </fieldset>
      </details>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
      <div class="row save-actions">
        <button disabled={locked || !dirty}>Save workspace settings</button>
        {#if dirty}<button
            type="button"
            disabled={locked}
            on:click={() => {
              dirty = false;
              revision = -1;
            }}>Discard changes</button
          >{/if}
      </div>
    </form>
  {:else}<p class="muted">Loading saved workspace settings…</p>{/if}
</section>

<style>
  .timezone-field {
    min-width: 0;
    flex: 1;
  }
  .row {
    display: flex;
    align-items: end;
    gap: 12px;
    flex-wrap: wrap;
  }
  .uni .computer-zone {
    background: transparent;
    font-size: var(--ke-text-2xs);
  }
  fieldset {
    display: grid;
    gap: 12px;
    border: 0;
    padding: 0;
  }
  fieldset > button {
    justify-self: start;
  }
  legend {
    font-size: var(--ke-text-sm);
    color: var(--ke-text-secondary);
    padding: 0;
  }
  .hint {
    margin: 0;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
  }
  .recommended {
    display: grid;
    gap: 8px;
    justify-items: start;
  }
  .workspace-options {
    border-top: 1px solid var(--ke-border);
  }
  .buffer-field {
    display: grid;
    gap: 6px;
    max-width: 220px;
  }
  .save-actions {
    margin-top: 24px;
  }
</style>
