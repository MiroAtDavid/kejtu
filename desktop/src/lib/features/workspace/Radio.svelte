<script lang="ts">
  import { onMount } from "svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import { identity } from "$lib/api/client";
  import Icon from "$lib/design/Icon.svelte";
  import RadioIllustration from "./RadioIllustration.svelte";
  import { workspaceRequest, type Workspace, type Receipt } from "./workspace";
  export let workspace: Workspace;
  export let busy = false;
  export let compact = false;
  export let comfortable = false;
  export let observed: MediaResult | null = null;
  type PlayerState = {
    state?: string;
    station?: string;
    volume?: number;
    metadata?: Record<string, string>;
    tracks?: { title: string; station: string; observed_at: string }[];
    notice?: string;
  };
  type MediaResult = Receipt & { player?: string; data?: PlayerState };
  const stationPickerId = `radio-station-${identity()}`;
  $: stationOptions = Object.entries(stations).map(([value, label]) => ({
    value,
    label,
    shortLabel: label.split(" · ")[0],
  }));
  let station = "morning",
    volume = 20,
    error = "",
    stations: Record<string, string> = {},
    history: PlayerState["tracks"] = [];
  let acting = false,
    volumeEditing = false,
    lastStation = "";
  $: player = observed?.data;
  $: playing = player?.state === "playing";
  $: running = ["playing", "paused", "buffering", "loading"].includes(
    player?.state || "",
  );
  $: if (player?.station && player.station !== lastStation) {
    station = player.station;
    lastStation = player.station;
  }
  $: paused = player?.state === "paused";
  $: playbackLabel =
    running && player?.station === station
      ? paused
        ? "Resume"
        : "Pause"
      : "Play";
  $: if (!volumeEditing && player?.volume !== undefined) volume = player.volume;
  $: trackTitle = player?.metadata?.["icy-title"] || player?.metadata?.title;

  $: locked = busy || acting || $workspace.busy || !!$workspace.pending;
  async function effect(command: string) {
    if (locked) return;
    error = "";
    acting = true;
    try {
      const result = await workspace.mutate<MediaResult>("media.command", {
        command,
      });
      if (result) observed = result;
    } catch (caught) {
      error = String(caught);
    } finally {
      acting = false;
    }
  }
  async function status() {
    error = "";
    try {
      observed = await workspaceRequest<MediaResult>("media.command", {
        command: "radio status",
      });
      if (observed.data?.volume !== undefined) volume = observed.data.volume;
    } catch (caught) {
      error = String(caught);
    }
  }
  async function tracks() {
    try {
      const result = await workspaceRequest<MediaResult>("media.command", {
        command: "radio history --last 10",
      });
      history = result.data?.tracks || [];
    } catch (caught) {
      error = String(caught);
    }
  }
  onMount(() => {
    if (!compact && $workspace.snapshot?.settings.radio_enabled) void status();
    void workspaceRequest<{ data: { stations: Record<string, string> } }>(
      "media.command",
      { command: "radio stations" },
    )
      .then((result) => (stations = result.data.stations))
      .catch((caught) => (error = String(caught)));
  });
</script>

<section
  class="radio"
  class:compact
  class:comfortable
  aria-label="Radio player"
>
  {#if !compact || comfortable}
    <RadioIllustration
      playing={playing &&
        !error &&
        !!$workspace.snapshot?.settings.radio_enabled}
    />
  {/if}
  {#if !compact}<p class="muted" role="status">
      {player?.state || "No observation yet"}{player?.station
        ? ` · ${player.station}`
        : ""}
    </p>{/if}
  {#if compact && ["loading", "buffering"].includes(player?.state || "")}<p
      class="track"
      role="status"
    >
      {player?.state === "loading" ? "Loading station…" : "Buffering…"}
    </p>{/if}
  <ChoicePicker
    id={stationPickerId}
    label="Station"
    bind:value={station}
    options={stationOptions}
    placeholder="Loading stations…"
    {compact}
    hideLabel
    singleLine
  />
  {#if trackTitle}<p class="track">{trackTitle}</p>{/if}
  <div class="radio-controls">
    <div class="row radio-buttons">
      <button
        class="play-toggle"
        class:is-playing={playing && player?.station === station}
        type="button"
        aria-label={playbackLabel}
        title={playbackLabel}
        disabled={locked || !$workspace.snapshot?.settings.radio_enabled}
        on:click={() =>
          effect(
            running && player?.station === station
              ? "radio toggle"
              : `radio play ${station}`,
          )}
      >
        <Icon
          name={playbackLabel === "Pause" ? "pause" : "play"}
          size={compact ? 14 : 16}
        />
      </button>
      {#if running}<button
          type="button"
          aria-label="Stop"
          title="Stop"
          disabled={locked}
          on:click={() => effect("radio stop")}
          ><Icon name="stop" size={16} /></button
        >{/if}
    </div>
    <label class="radio-volume" title={`Volume ${volume}%`}>
      <span class="visually-hidden">Volume</span>
      <svg
        width="16"
        height="16"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        aria-hidden="true"><path d="M3 7h4l5-4v14l-5-4H3zM15 6q4 4 0 8" /></svg
      >
      <input
        aria-label="Volume"
        type="range"
        style={`--volume: ${volume}%`}
        min="0"
        max="100"
        bind:value={volume}
        disabled={locked || !$workspace.snapshot?.settings.radio_enabled}
        on:input={() => (volumeEditing = true)}
        on:change={async () => {
          await effect(`radio volume ${volume}`);
          volumeEditing = false;
        }}
      />
      <output aria-hidden="true">{volume}%</output>
    </label>
  </div>
  {#if !compact}<div class="row">
      <button class="plain" on:click={status}>Observe status</button
      >{#if !compact}<button class="plain" on:click={tracks}
          >Observed history</button
        >{/if}
    </div>
  {/if}
  {#each history || [] as track}<p class="source">
      {track.title}<br />{track.station} · {track.observed_at}
    </p>{/each}
  {#if error}<div class="radio-error" role="alert">
      <span>Radio unavailable</span>
      <div class="row">
        <button type="button" on:click={status}>Check status</button>
        <button type="button" on:click={() => (error = "")}>Dismiss</button>
      </div>
      <details>
        <summary>Details</summary>
        <p>{error}</p>
      </details>
    </div>{/if}
</section>

<style>
  .radio {
    display: grid;
    gap: 10px;
    min-width: 0;
  }
  .radio p {
    margin: 0;
  }
  .radio .row {
    gap: 4px;
  }
  .radio button {
    font-size: var(--ke-text-2xs);
    padding: 4px 6px;
  }
  .track {
    font-size: var(--ke-text-xs);
    overflow-wrap: anywhere;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
  .radio-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    margin: 4px 0;
  }
  .radio .radio-buttons {
    display: flex;
    flex: none;
    align-items: center;
    gap: 4px;
    margin: 0;
  }
  .radio .radio-volume {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 8px;
    min-width: 0;
    color: var(--ke-text-secondary);
  }
  .radio-volume svg {
    flex: none;
  }
  .radio-volume output {
    flex: none;
    min-width: 28px;
    font-size: var(--ke-text-3xs);
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  /* Override the native GTK range theme: keep a generous hit area around
     the thin visible track and small handle. Keyboard behavior stays native. */
  .radio input[type="range"] {
    -webkit-appearance: none;
    appearance: none;
    display: block;
    width: 100%;
    min-width: 0;
    flex: 1;
    height: 20px;
    padding: 0;
    margin: 2px 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    cursor: pointer;
  }
  .radio input[type="range"]::-webkit-slider-runnable-track {
    height: 2px;
    border-radius: 1px;
    background: linear-gradient(
      to right,
      var(--ke-text-secondary) 0 var(--volume),
      var(--ke-border-strong) var(--volume) 100%
    );
  }
  .radio input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 8px;
    height: 8px;
    margin-top: -3px;
    border: 0;
    border-radius: 50%;
    background: var(--ke-text-secondary);
    box-shadow: none;
  }
  .radio input[type="range"]::-moz-range-track {
    height: 2px;
    border-radius: 1px;
    background: var(--ke-border-strong);
  }
  .radio input[type="range"]::-moz-range-progress {
    height: 2px;
    border-radius: 1px;
    background: var(--ke-text-secondary);
  }
  .radio input[type="range"]::-moz-range-thumb {
    width: 8px;
    height: 8px;
    border: 0;
    border-radius: 50%;
    background: var(--ke-text-secondary);
    box-shadow: none;
  }
  .radio input[type="range"]:focus-visible {
    outline: 1px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  .radio input[type="range"]:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .compact {
    gap: 10px;
  }
  .compact button {
    background: transparent;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-sm);
    color: var(--ke-text-secondary);
    cursor: pointer;
    padding: 3px 9px;
  }
  .radio .radio-buttons button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    min-height: 28px;
    padding: 0;
    border: 0;
    border-radius: var(--ke-radius-sm);
    background: transparent;
    color: var(--ke-text-secondary);
    cursor: pointer;
  }
  .radio .radio-buttons button:hover:not(:disabled) {
    background: var(--ke-surface-inset);
    color: var(--ke-text-primary);
  }
  .radio .radio-buttons button:focus-visible {
    outline: 1px solid var(--ke-text-secondary);
    outline-offset: -3px;
  }
  .radio .radio-buttons .play-toggle {
    width: 36px;
    height: 36px;
    min-height: 36px;
    border-radius: 50%;
    background: var(--ke-accent);
    color: var(--ke-on-accent);
  }
  .radio .radio-buttons .play-toggle:hover:not(:disabled) {
    background: var(--ke-accent-hover);
    color: var(--ke-on-accent);
  }
  .radio .radio-buttons .play-toggle:focus-visible {
    outline: 2px solid var(--ke-focus-ring);
    outline-offset: 3px;
  }
  .compact:not(.comfortable) .radio-buttons .play-toggle {
    width: 28px;
    height: 28px;
    min-height: 28px;
  }
  .radio.comfortable .radio-buttons .play-toggle {
    width: 30px;
    height: 30px;
    min-height: 30px;
  }
  .radio.compact .radio-buttons .play-toggle {
    background: color-mix(in srgb, var(--ke-accent) 12%, transparent);
    color: var(--ke-text-secondary);
  }
  .radio.compact .radio-buttons .play-toggle:hover:not(:disabled),
  .radio.compact .radio-buttons .play-toggle.is-playing {
    background: color-mix(in srgb, var(--ke-accent) 20%, transparent);
    color: var(--ke-text-primary);
  }
  .compact button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .compact .track {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-2xs);
  }
  .radio-error {
    padding: 10px;
    border-radius: var(--ke-radius-md);
    background: var(--ke-accent-soft);
    font-size: var(--ke-text-2xs);
    overflow-wrap: anywhere;
  }
  .radio-error .row {
    margin: 6px 0;
  }
  .radio-error details {
    color: var(--ke-text-secondary);
  }
  .radio-error summary {
    cursor: pointer;
  }
  .radio-error p {
    margin-top: 6px;
  }
</style>
