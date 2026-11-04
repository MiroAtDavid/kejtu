<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import SourceLink from "$lib/features/chat/SourceLink.svelte";
  import { onMount } from "svelte";
  import { identity, request } from "$lib/api/client";
  import { WebSettingsJournal, type Status, type Receipt } from "./settings";

  import type { WebStatus } from "$lib/api/wire.generated";

  export let stateId: string;
  export let busy = false;
  export let locked = false;
  // The parent keys by archive; late replies cannot change another archive.
  const journal = new WebSettingsJournal(stateId);
  let current: Status | null = null;
  let providers: WebStatus["providers"] = [];
  let selected = "searxng";
  let apiKey = "";
  $: selectedProvider = providers.find((provider) => provider.id === selected);
  $: keyName = selected === "brave" ? "Brave" : selectedProvider?.label;
  $: usage = selectedProvider?.usage;
  $: keyLabel = {
    loading: "Loading",
    unavailable: "Keyring unavailable",
    saved: "Saved in desktop keyring",
    missing: "No key saved",
    not_required: "Not required",
  }[selectedProvider?.key_status ?? "unavailable"];

  let working = false,
    ready = false,
    pending = false,
    absent = false,
    destroyed = false;
  let error = "",
    notice = "";
  $: locked = working || pending;

  async function read() {
    current = null;
    ready = false;
    const value = await journal.read();
    if (!destroyed) {
      current = value;
      selected = value.provider;
      ready = true;
    }
  }
  async function status() {
    const value = await request("web.status", {});
    if (!destroyed) providers = value.providers;
  }
  async function credential(action: "save" | "remove" | "reload") {
    const provider = selected;
    if (provider !== "brave" && provider !== "tavily") return;
    const name = keyName;
    const value = apiKey;
    apiKey = "";
    const reply = await request(
      "web.key",
      action === "save"
        ? { action, provider, api_key: value }
        : { action, provider },
    );
    if (!destroyed) {
      providers = reply.providers;
      notice =
        action === "save"
          ? `${name} key saved. No search was sent.`
          : action === "remove"
            ? `${name} key removed.`
            : "Key status refreshed.";
    }
  }
  async function finish(receipt: Receipt | null) {
    if (destroyed) return;
    notice = receipt
      ? "Web access updated."
      : "No saved change found. Retry this exact choice or release it and refresh.";
    await read(); // A past mutation receipt is not current permission authority.
  }
  async function load() {
    ready = false;
    current = null;
    const receipt = await journal.restore();
    if (receipt || journal.pending) await finish(receipt);
    else await read();
    await status();
  }
  async function change(enabled: boolean, provider = current?.provider) {
    if (!current || !provider || !ready || busy || journal.pending) return;
    await finish(
      await journal.begin({
        action: "set",
        enabled,
        provider,
        expected_revision: current.revision,
        mutation_key: identity(),
      }),
    );
  }
  async function run(action: () => Promise<void>) {
    if (working) return;
    working = true;
    error = "";
    try {
      await action();
    } catch (caught) {
      if (!destroyed)
        error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (!destroyed) {
        working = false;
        pending =
          journal.pending !== null ||
          localStorage.getItem(journal.key) !== null;
        absent = journal.absent;
      }
    }
  }
  onMount(() => {
    void run(load);
    return () => {
      apiKey = "";
      destroyed = true;
      locked = false;
    };
  });
</script>

<section aria-label="Web access">
  <p class="panel-intro">
    Search and read public pages when a reply needs them. Applies to
    conversations with tool access; background work stays separate.
  </p>
  <p>
    Web search and page reading: <strong
      >{current ? (current.enabled ? "On" : "Off") : "Unavailable"}</strong
    >
  </p>
  <p>
    Search words go to your selected search provider. Page reads contact the
    source website. Saved sources can be inspected under Tool activity.
  </p>
  <div class="provider-choice">
    <label for="search-provider">Search provider</label>
    <select
      use:choiceSelect={selected}
      id="search-provider"
      bind:value={selected}
      disabled={!ready || working || busy || pending}
      on:change={() => {
        apiKey = "";
      }}
    >
      {#each providers as provider}<option value={provider.id}
          >{provider.label}</option
        >{/each}
    </select>
    <button
      disabled={!ready ||
        working ||
        busy ||
        pending ||
        !selectedProvider ||
        selected === current?.provider}
      on:click={() => run(() => change(current!.enabled, selected))}
      >Apply provider</button
    >
  </div>
  <p class="muted">
    Active: {providers.find((p) => p.id === current?.provider)?.label ??
      current?.provider ??
      "Unavailable"}. Search options follow the active provider; no automatic
    provider switching.
  </p>
  <details class="options">
    <summary>Search options · {selectedProvider?.label ?? selected}</summary>
    {#if selected === "brave"}
      <p>
        Web, news, places, images and videos. Ask normally, for example “Find
        cafés in Vienna” or “Search ORF articles from September 2024”. Kejtu can
        choose country, language and dates for each search.
      </p>
      <p>
        Queries support operators such as site:orf.at, quoted phrases and
        excluded terms. Optional Goggles can favour or exclude sources for a web
        or news search; no Goggles are applied by default.
      </p>
      <p>
        Places can include addresses, hours, ratings and contact details when
        available. A radius favours nearby results; it is not a strict boundary.
        Saved sources show the search options and returned information.
      </p>
    {:else if selected === "tavily"}
      <p>
        Search the web, news and finance sources, with domain, language and
        recency filters. Kejtu starts with basic search; advanced searches can
        retrieve more detail and use more account credits.
      </p>
      <p>
        No key is needed for rate-limited keyless access. Add your own Tavily
        API key below to use your account quota. Search returns source excerpts
        and links; Kejtu can read the original pages when needed.
      </p>
    {:else}
      <p>
        The existing SearXNG search stays available: general search plus web,
        news, reference, maps, science, software, video and music pools, with
        language, domain and day/week/month/year filters.
      </p>
    {/if}
    <p class="muted">
      Provider changes apply to the next message. Page reading is independent of
      search; PDF reading is not available yet.
    </p>
  </details>
  {#if selectedProvider && selectedProvider.key_status !== "not_required"}
    <div class="credentials">
      <label for="search-key"
        >{keyName} API key{selected === "tavily" ? " (optional)" : ""}</label
      >
      <input
        id="search-key"
        type="password"
        bind:value={apiKey}
        autocomplete="off"
        spellcheck="false"
        disabled={working || busy || pending}
        placeholder="Paste key"
      />
      <p class="muted">
        {selected === "tavily" && selectedProvider.key_status === "missing"
          ? "Keyless access · no key saved"
          : keyLabel}
      </p>
      <button
        disabled={!apiKey || working || busy || pending}
        on:click={() => run(() => credential("save"))}
        >Save {keyName} key</button
      >
      <button
        disabled={working || busy || pending}
        on:click={() => run(() => credential("reload"))}>Reload key</button
      >
      <button
        disabled={working ||
          busy ||
          pending ||
          selectedProvider.key_status !== "saved"}
        on:click={() => run(() => credential("remove"))}>Remove key</button
      >
      <p class="muted">
        Stored in the desktop keyring. Opening Settings and saving a key do not
        send searches.
      </p>
      <p class="muted">
        Account usage and limits are managed in the <SourceLink
          href={selected === "tavily"
            ? "https://app.tavily.com/"
            : "https://api-dashboard.search.brave.com/"}
          >{keyName} dashboard</SourceLink
        >.
      </p>
    </div>
  {/if}
  {#if usage}
    <div class="usage" aria-label="Search usage">
      <p>
        <strong>This month · {selectedProvider?.label}</strong>
        <span class="muted">({usage.month}, UTC)</span>
      </p>
      <dl>
        <div>
          <dt>Search attempts</dt>
          <dd>{usage.attempts}</dd>
        </div>
        <div>
          <dt>Successful</dt>
          <dd>{usage.successful}</dd>
        </div>
        <div>
          <dt>Failed</dt>
          <dd>{usage.failed}</dd>
        </div>
        <div>
          <dt>Cancelled / unknown</dt>
          <dd>{usage.cancelled} / {usage.unknown}</dd>
        </div>
        <div>
          <dt>Results returned</dt>
          <dd>{usage.results}</dd>
        </div>
        <div>
          <dt>Average successful search</dt>
          <dd>
            {usage.average_ms === null
              ? "—"
              : `${(usage.average_ms / 1000).toFixed(1)} s`}
          </dd>
        </div>
      </dl>
      <p class="muted">
        Local counts since this feature was installed. Cached results and page
        reads do not add search attempts. These are not billing totals.
      </p>
      <button disabled={working} on:click={() => run(status)}
        >Refresh stats</button
      >
    </div>
  {/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if pending}
    <button
      disabled={working}
      on:click={() => run(async () => finish(await journal.resolve()))}
      >Resolve web access change</button
    >
    {#if absent}
      <button
        disabled={working || busy}
        on:click={() => run(async () => finish(await journal.retry()))}
        >Retry same choice</button
      >
      <button
        disabled={working}
        on:click={() =>
          run(async () => {
            journal.release();
            await read();
          })}>Release uncommitted choice</button
      >
    {/if}
  {:else}
    <button
      disabled={!ready || working || busy || !current}
      on:click={() => run(() => change(!current!.enabled))}
    >
      {current?.enabled ? "Turn web off" : "Turn web on"}
    </button>
    <button disabled={working || busy} on:click={() => run(read)}
      >Refresh access</button
    >
  {/if}
</section>

<style>
  .provider-choice,
  .options,
  .credentials,
  .usage {
    margin-bottom: var(--ke-space-5);
    max-width: 42rem;
  }
  .provider-choice > button {
    margin-top: var(--ke-space-3);
  }
  summary {
    cursor: pointer;
    margin-bottom: var(--ke-space-3);
  }
  label {
    display: block;
    margin-bottom: var(--ke-space-2);
  }
  select,
  input {
    margin-bottom: var(--ke-space-3);
    max-width: 100%;
    box-sizing: border-box;
  }
  select {
    margin-right: var(--ke-space-2);
  }
  input {
    display: block;
    width: 100%;
  }
  .muted {
    color: var(--ke-text-secondary);
    font-size: 0.9rem;
  }
  .credentials :global(a) {
    color: inherit;
    text-underline-offset: 3px;
  }
  dl {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--ke-space-3);
    margin: 0 0 var(--ke-space-4);
  }
  dt {
    color: var(--ke-text-secondary);
    font-size: 0.85rem;
  }
  dd {
    margin: var(--ke-space-1) 0 0;
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 600px) {
    dl {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  p {
    margin: 0 0 var(--ke-space-4);
    max-width: 42rem;
    line-height: 1.6;
  }
</style>
