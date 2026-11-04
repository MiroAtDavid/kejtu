<script lang="ts">
  import { onMount } from "svelte";
  import { openWebLink } from "$lib/api/web-links";
  import { workspaceRequest, type Workspace, type Receipt } from "./workspace";
  import Radio from "./Radio.svelte";
  import ChoicePicker from "$lib/design/ChoicePicker.svelte";
  import { identity } from "$lib/api/client";
  export let workspace: Workspace;
  export let busy = false;
  export let active = true;
  type State = {
    state?: string;
    title?: string;
    url?: string;
    volume?: number;
    position_seconds?: number | null;
    duration_seconds?: number | null;
    speed?: number;
    fullscreen?: boolean;
    video_output_ready?: boolean;
    audio_output_ready?: boolean;
    help?: string;
    results?: {
      title: string;
      url: string;
      channel: string;
      playability: string;
    }[];
    sites?: { name: string; label: string; url: string }[];
    note?: string;
  };
  type Result = Receipt & { data?: State };
  const speedId = `video-speed-${identity()}`;
  const speeds = Array.from({ length: 12 }, (_, index) => (index + 1) / 4);
  let current: Result | null = null,
    error = "",
    statusError = "",
    url = "",
    query = "",
    site = "youtube",
    volume = 20,
    seek = 0,
    speed = "1",
    loading = false,
    acting = false,
    reading = false,
    mounted = false,
    volumeEditing = false,
    seekEditing = false;
  // A status read started before a command must not overwrite its newer result.
  let observation = 0;
  let sites: NonNullable<State["sites"]> = [],
    results: NonNullable<State["results"]> = [],
    help = "";
  $: enabled = !!$workspace.snapshot?.settings.video_enabled;
  $: locked =
    busy || loading || acting || $workspace.busy || !!$workspace.pending;
  $: running =
    enabled && !!current?.data?.state && current.data.state !== "stopped";
  $: duration = finite(current?.data?.duration_seconds)
    ? current!.data!.duration_seconds!
    : 0;
  $: seekLimit = Math.min(duration, 604800);
  $: speedOptions = [...new Set([...speeds, Number(speed)])]
    .sort((a, b) => a - b)
    .map((value) => ({
      value: String(value),
      label: value === 1 ? "1× · Normal" : `${value}×`,
    }));
  $: if (!active || !enabled) observation += 1;
  $: if (mounted && active && enabled && !loading) void observe();

  function finite(value: unknown): value is number {
    return typeof value === "number" && Number.isFinite(value) && value >= 0;
  }
  function time(value: number) {
    const seconds = Math.max(0, Math.floor(value));
    const minutes = Math.floor(seconds / 60);
    return minutes >= 60
      ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
      : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function accept(result: Result) {
    current = result;
    const data = result.data;
    if (!volumeEditing && finite(data?.volume))
      volume = Math.min(100, data.volume);
    if (!seekEditing)
      seek = finite(data?.position_seconds)
        ? Math.floor(data.position_seconds)
        : 0;
    if (finite(data?.speed) && data.speed > 0) speed = String(data.speed);
  }
  async function observe() {
    if (!mounted || !active || !enabled || locked || reading || document.hidden)
      return;
    reading = true;
    const started = observation;
    try {
      const result = await workspaceRequest<Result>("media.command", {
        command: "video status",
      });
      if (mounted && active && started === observation) {
        accept(result);
        statusError = "";
      }
    } catch (caught) {
      if (mounted && active && started === observation)
        statusError = String(caught);
    } finally {
      reading = false;
    }
  }
  async function read(command: string) {
    if (locked) return;
    loading = true;
    error = "";
    try {
      const result = await workspaceRequest<Result>("media.command", {
        command,
      });
      if (command === "video sites") sites = result.data?.sites || [];
      else if (command === "video help") help = result.data?.help || "";
      else if (command.startsWith("video search"))
        results = result.data?.results || [];
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }
  async function effect(command: string) {
    if (locked || !enabled) return;
    acting = true;
    observation += 1;
    error = "";
    try {
      const result = await workspace.mutate<Result>("media.command", {
        command,
      });
      if (result) {
        accept(result);
        statusError = "";
      }
    } catch (caught) {
      error = String(caught);
    } finally {
      acting = false;
    }
  }
  async function commitVolume() {
    await effect(`video volume ${volume}`);
    volumeEditing = false;
  }
  async function commitSeek() {
    if (seekLimit > 0)
      await effect(`video seek ${Math.min(seekLimit, Math.max(0, seek))}`);
    seekEditing = false;
  }
  onMount(() => {
    mounted = true;
    void read("video sites");
    const timer = setInterval(() => void observe(), 2000);
    return () => {
      mounted = false;
      observation += 1;
      clearInterval(timer);
    };
  });
</script>

<section class="uni">
  <h1>Listen &amp; watch</h1>
  <p class="muted">
    Listen to radio or open a video in its own window. Playback continues when
    you change views.
  </p>
  <div class="media-grid">
    <article class="card">
      <h2>Radio</h2>
      {#if !$workspace.snapshot?.settings.radio_enabled}<p class="notice">
          Enable radio in Workspace settings.
        </p>{/if}<Radio {workspace} {busy} />
    </article>
    <article class="card stack video-card">
      <div class="row spread video-heading">
        <h2>Video</h2>
        <button disabled={locked || reading || !enabled} on:click={observe}
          >Refresh status</button
        >
      </div>
      {#if !enabled}<p class="notice">
          Enable video in Workspace settings.
        </p>{/if}
      {#if current}<p class="playback-status" role="status">
          {current.data?.title || "Video"}<span class="muted"
            >{current.data?.state || current.status}</span
          >
        </p>{/if}
      <form
        class="video-source"
        on:submit|preventDefault={() =>
          effect(`video play ${JSON.stringify(url)}`)}
      >
        <label
          >Video URL<input
            type="url"
            required
            placeholder="Paste a video link"
            bind:value={url}
          /></label
        >
        <button disabled={locked || !url || !enabled}>Play video</button>
      </form>
      <div class="timeline">
        <label>
          <span class="range-heading"
            ><span>Position</span><span class="time-code"
              >{time(seek)} / {duration > 0 ? time(duration) : "–:––"}</span
            ></span
          >
          <input
            type="range"
            aria-label="Video position"
            aria-valuetext={`${time(seek)} of ${duration > 0 ? time(duration) : "unknown duration"}`}
            min="0"
            max={seekLimit || 1}
            step="1"
            bind:value={seek}
            style={`--progress: ${seekLimit > 0 ? Math.min(100, (seek / seekLimit) * 100) : 0}%`}
            disabled={locked || !running || seekLimit <= 0}
            on:input={() => (seekEditing = true)}
            on:change={commitSeek}
            on:blur={() => (seekEditing = false)}
            on:pointercancel={() => (seekEditing = false)}
          />
        </label>
        {#if running && duration <= 0}<p class="muted">
            Seeking is unavailable for this video or live stream.
          </p>{/if}
      </div>
      <div class="row playback-actions">
        <button
          disabled={locked || !running}
          on:click={() =>
            effect(
              current?.data?.state === "paused"
                ? "video resume"
                : "video pause",
            )}>{current?.data?.state === "paused" ? "Resume" : "Pause"}</button
        >
        <button
          disabled={locked || !running}
          on:click={() => effect("video stop")}>Stop</button
        >
        <button
          disabled={locked || !running}
          on:click={() =>
            effect(
              `video fullscreen ${current?.data?.fullscreen ? "off" : "on"}`,
            )}
          >{current?.data?.fullscreen
            ? "Leave fullscreen"
            : "Fullscreen"}</button
        >
      </div>
      <div class="video-options">
        <label class="video-volume">
          <span class="range-heading"
            ><span>Volume</span><span>{volume}%</span></span
          >
          <input
            type="range"
            aria-label="Video volume"
            aria-valuetext={`${volume}%`}
            min="0"
            max="100"
            step="1"
            bind:value={volume}
            style={`--progress: ${volume}%`}
            disabled={locked || !running}
            on:input={() => (volumeEditing = true)}
            on:change={commitVolume}
            on:blur={() => (volumeEditing = false)}
            on:pointercancel={() => (volumeEditing = false)}
          />
        </label>
        <ChoicePicker
          id={speedId}
          label="Playback speed"
          labelOnly
          value={speed}
          options={speedOptions}
          disabled={locked || !running}
          onChange={(value) => {
            speed = value;
            void effect(`video speed ${value}`);
          }}
        />
      </div>
      <details>
        <summary>Commands &amp; playback help</summary><button
          disabled={locked}
          on:click={() => read("video help")}>Read video help</button
        >
        <pre>{help}</pre>
      </details>
    </article>
  </div>
  <section class="card">
    <h2>Find a video</h2>
    <p class="muted">
      Uses the current Web permission. YouTube/Bilibili search through the
      curated extractor; other sites use your existing selected search provider.
      Search matches may still be unavailable when opened.
    </p>
    <form
      on:submit|preventDefault={() =>
        read(`video search ${site} ${JSON.stringify(query)} --limit 5`)}
    >
      <div class="row search-fields">
        <label
          >Site<select bind:value={site}
            >{#each sites as item}<option value={item.name}>{item.label}</option
              >{/each}</select
          ></label
        ><label
          >Search<input required maxlength="300" bind:value={query} /></label
        ><button disabled={locked || !query.trim()}
          >Search supported site</button
        >
      </div>
    </form>
    {#each results as result}<article class="search-result">
        <h3>{result.title}</h3>
        <p class="muted">{result.channel} · {result.playability}</p>
        <div class="row">
          <button
            disabled={locked || !enabled}
            on:click={() => {
              url = result.url;
              void effect(`video play ${JSON.stringify(result.url)}`);
            }}>Play this result</button
          ><button
            on:click={() =>
              openWebLink(result.url).catch(
                (caught) => (error = String(caught)),
              )}>Open source page ↗</button
          >
        </div>
      </article>{/each}
  </section>
  {#if error || statusError}<p class="error" role="alert">
      {error || statusError}
    </p>{/if}
</section>

<style>
  .media-grid {
    display: grid;
    grid-template-columns: minmax(220px, 1fr) minmax(320px, 2fr);
    gap: 24px;
    margin: 24px 0;
  }

  .video-card {
    gap: 20px;
    align-content: start;
  }
  .video-heading h2 {
    margin: 0;
  }
  .media-grid > article > h2 {
    margin-top: 0;
  }
  .video-heading button {
    background: transparent;
  }
  .playback-status {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .playback-status .muted {
    display: block;
    text-transform: capitalize;
  }
  .video-source {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 8px;
  }
  .video-source button,
  .search-fields button {
    min-height: 36px;
  }
  .row.search-fields {
    align-items: flex-end;
  }
  .range-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .time-code {
    font-variant-numeric: tabular-nums;
  }
  .timeline p {
    margin: 6px 0 0;
  }
  .video-options {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 150px;
    align-items: start;
    gap: 28px;
  }
  .playback-actions {
    gap: 8px;
  }
  .video-card input[type="range"] {
    -webkit-appearance: none;
    appearance: none;
    display: block;
    width: 100%;
    height: 20px;
    padding: 0;
    margin: 2px 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    cursor: pointer;
  }
  .video-card input[type="range"]::-webkit-slider-runnable-track {
    height: 2px;
    border-radius: 1px;
    background: linear-gradient(
      to right,
      var(--ke-text-secondary) 0 var(--progress),
      var(--ke-border-strong) var(--progress) 100%
    );
  }
  .video-card input[type="range"]::-webkit-slider-thumb {
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
  .video-card input[type="range"]::-moz-range-track {
    height: 2px;
    border-radius: 1px;
    background: var(--ke-border-strong);
  }
  .video-card input[type="range"]::-moz-range-progress {
    height: 2px;
    border-radius: 1px;
    background: var(--ke-text-secondary);
  }
  .video-card input[type="range"]::-moz-range-thumb {
    width: 8px;
    height: 8px;
    border: 0;
    border-radius: 50%;
    background: var(--ke-text-secondary);
    box-shadow: none;
  }
  .video-card input[type="range"]:focus-visible {
    outline: 1px solid var(--ke-text-secondary);
    outline-offset: 2px;
  }
  .video-card input[type="range"]:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .video-options input[type="range"] {
    height: 38px;
  }

  @media (max-width: 480px) {
    .video-source,
    .video-options {
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
    }
  }
  .search-result {
    border-top: 1px solid var(--ke-border);
    padding: 14px 0;
  }
  pre {
    white-space: pre-wrap;
    font:
      12px/1.8 ui-monospace,
      monospace;
  }
  @media (max-width: 850px) {
    .media-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
