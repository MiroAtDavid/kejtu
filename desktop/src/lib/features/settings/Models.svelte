<script lang="ts">
  import { choiceSelect } from "$lib/design/choice-select";
  import { onMount, onDestroy } from "svelte";
  import { request } from "$lib/api/client";
  import type {
    ModelSettings,
    Profile,
    Provider,
  } from "$lib/api/wire.generated";
  import ChatGPTConnection from "./ChatGPTConnection.svelte";
  import { CREDENTIAL_PROVIDERS } from "$lib/api/wire.generated";

  export let ownerBusy = false;
  export let busy = false;
  export let onChanged: () => Promise<void> = async () => {};
  let saved: ModelSettings | null = null;
  let selected: Provider = "deepseek";
  let drafts: Record<Provider, Profile> | null = null;
  let key = "";
  let notice = "";
  let error = "";
  let models: string[] = [];
  let checkedIdentity = "";
  let thinking = "high";
  let chatgptConnected = false;
  let choices: { model: string; label: string }[] = [];
  let catalogPending = false;
  let catalogLoading = false;
  let catalogError = "";
  let catalogNotice = "";
  $: profile = drafts?.[selected];
  $: descriptor = saved?.providers.find((item) => item.id === selected);
  $: keyStatus = saved?.key_statuses[selected] ?? "missing";
  $: thinkingOptions =
    descriptor?.models.find((item) => item.model === profile?.model)
      ?.thinking_options ??
    descriptor?.thinking_options ??
    [];
  $: suggestedModels = [
    ...new Set([
      ...(descriptor?.models.map((item) => item.model) ?? []),
      ...models,
    ]),
  ];
  $: dirty =
    !!profile &&
    JSON.stringify(profile) !== JSON.stringify(saved?.profiles[selected]);
  $: checked = profile && checkedIdentity === JSON.stringify(profile);
  $: disabled = busy || ownerBusy;
  // A successful explicit sign-in/account change requests one refresh after
  // the connection owner releases its busy lock. Opening Settings stays local.
  $: if (
    catalogPending &&
    selected === "chatgpt" &&
    chatgptConnected &&
    !disabled
  ) {
    catalogPending = false;
    void check();
  }

  function receive(value: ModelSettings, reset = false) {
    saved = value;
    if (reset || !drafts) {
      drafts = structuredClone(value.profiles);
      selected = value.active;
      showThinking();
    }
  }
  function showThinking() {
    const value = drafts?.[selected].thinking_level;
    thinking =
      value === false ? "off" : value === true ? "on" : (value ?? "off");
  }
  function choose(value: Provider) {
    if (disabled) return;
    key = "";
    selected = value;
    models = [];
    choices = [];
    catalogPending = false;
    catalogError = "";
    catalogNotice = "";
    checkedIdentity = "";
    notice = "";
    showThinking();
  }
  function changeModel() {
    const options =
      descriptor?.models.find((item) => item.model === profile?.model)
        ?.thinking_options ??
      descriptor?.thinking_options ??
      [];
    if (
      !options.includes(thinking as (typeof options)[number]) &&
      options.length
    ) {
      thinking = options[0];
      changeThinking();
    }
  }
  function changeThinking() {
    if (profile)
      profile.thinking_level =
        thinking === "off"
          ? false
          : thinking === "on"
            ? true
            : (thinking as Profile["thinking_level"]);
  }
  async function run(action: () => Promise<void>, readOnly = false) {
    // Reading saved local profiles is safe during foreground work. Blocking the
    // initial read would leave this retained panel empty even after work ends.
    if (busy || (ownerBusy && !readOnly)) return;
    busy = true;
    error = "";
    notice = "";
    try {
      await action();
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "The model settings operation could not be confirmed. Reload saved settings to check.";
    } finally {
      busy = false;
    }
  }
  async function reload() {
    await run(
      async () => receive(await request("models.read", {}), true),
      true,
    );
  }
  async function save(activate = false) {
    if (!profile || !saved) return;
    const value = structuredClone(profile);
    await run(async () => {
      const result = await request("models.save", {
        profile: value,
        active: activate ? selected : saved!.active,
        expected_revision: saved!.revision,
      });
      receive(result);
      drafts = {
        ...drafts!,
        [selected]: structuredClone(result.profiles[selected]),
      };
      await onChanged();
      notice = activate
        ? "Profile selected for new conversations."
        : "Model profile saved.";
    });
  }
  async function check() {
    if (!profile) return;
    const value = structuredClone(profile);
    checkedIdentity = "";
    await run(async () => {
      const subscription = value.provider === "chatgpt";
      catalogLoading = subscription;
      catalogError = "";
      catalogNotice = "";
      try {
        const result = await request("models.check", { profile: value });
        models = result.models;
        choices = result.choices ?? [];
        checkedIdentity = JSON.stringify(value);
        if (subscription) {
          catalogNotice = choices.length
            ? `${choices.length} models available${result.has_more ? " (showing the first 50)" : ""}.`
            : "No models were returned for this account. Try loading the list again.";
        } else {
          notice = result.model_available
            ? "Connected. Model found; no generation requested."
            : `Connected. ${value.model} is not in this ${selected === "chatgpt" ? "connection’s" : "key’s"} model list.`;
          if (result.has_more)
            notice += " Showing up to 50 models, with supported choices first.";
        }
      } catch (caught) {
        if (!subscription) throw caught;
        catalogError =
          caught instanceof Error
            ? caught.message
            : "The model list could not be loaded. Try again.";
      } finally {
        catalogLoading = false;
      }
    });
  }
  async function changeKey(action: "save" | "remove" | "reload") {
    const provider = CREDENTIAL_PROVIDERS.find((item) => item === selected);
    if (!provider) return;
    await run(async () => {
      // Secret entry stays only in this component and the private native request.
      // Never use localStorage, mutation journals, profile drafts or URL state.
      const value =
        action === "save"
          ? { action, provider, api_key: key.trim() }
          : { action, provider };
      key = "";
      receive(await request("models.key", value));
      checkedIdentity = "";
      notice =
        action === "save"
          ? "API key saved in your desktop keyring. Kejtu will remember it after restart."
          : action === "remove"
            ? "API key removed."
            : "Key status refreshed.";
    });
  }
  onMount(() => {
    let mounted = true;
    void reload();
    // Only while startup keyring restoration is pending; this reads local
    // status and never checks a provider or leaves periodic model traffic.
    const timer = setInterval(() => {
      if (!Object.values(saved?.key_statuses ?? {}).includes("loading")) {
        clearInterval(timer);
        return;
      }
      if (!busy)
        void request("models.read", {})
          .then((value) => {
            if (mounted && !busy) receive(value);
          })
          .catch(() => {});
    }, 1000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  });
  onDestroy(() => {
    key = "";
    busy = false;
  });
</script>

<div class="models-panel">
  <h2 class="settings-title">Models</h2>
  <p class="muted">
    Save a profile and choose it for new conversations. Existing conversations
    keep their recorded model settings.
  </p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if !saved && busy}<p role="status">Loading saved model settings…</p>{/if}
  {#if ownerBusy && !busy}
    <p class="muted">Model changes are available when current work finishes.</p>
  {/if}
  {#if saved && profile}
    <p class="active">
      New conversations: <strong
        >{saved.providers.find((item) => item.id === saved?.active)
          ?.label}</strong
      >
      · {saved.profiles[saved.active].model}
    </p>
    <nav class="settings-tabs" aria-label="Model profiles">
      {#each saved.providers as item (item.id)}
        <button
          type="button"
          class:selected={selected === item.id}
          aria-pressed={selected === item.id}
          {disabled}
          on:click={() => choose(item.id)}>{item.label}</button
        >
      {/each}
    </nav>
    {#if selected === "openai"}
      <p class="muted">
        Use an OpenAI API key. Usage is billed to your API account.
      </p>
    {/if}
    {#if selected === "chatgpt"}
      {#if saved.active === "chatgpt"}<p class="muted">
          Using ChatGPT plan for new conversations.
        </p>{/if}
      <ChatGPTConnection
        bind:busy
        {disabled}
        onStatus={(value) => {
          chatgptConnected = value;
        }}
        onConnected={async () => {
          models = [];
          choices = [];
          checkedIdentity = "";
          catalogError = "";
          catalogNotice = "";
          catalogPending = true;
        }}
      />
    {/if}
    <fieldset {disabled}>
      <legend>{descriptor?.label} model</legend>
      {#if descriptor?.editable_endpoint}
        <label
          >Ollama address<input
            type="url"
            bind:value={profile.endpoint}
            spellcheck="false"
          /></label
        >
      {/if}
      {#if selected === "chatgpt"}
        <label
          >Model
          <select
            bind:value={profile.model}
            on:change={changeModel}
            disabled={!choices.length}
          >
            {#if !choices.some((item) => item.model === profile?.model)}<option
                value={profile.model}
                >{profile.model} · {choices.length
                  ? "Not in this account’s model list"
                  : "Load available models"}</option
              >{/if}
            {#each choices as item}
              <option
                value={item.model}
                disabled={!descriptor?.models.some(
                  (known) => known.model === item.model,
                )}
                >{item.label}{descriptor?.models.some(
                  (known) => known.model === item.model,
                )
                  ? ""
                  : " · Not supported by this Kejtu version"}</option
              >
            {/each}
          </select>
        </label>
        <div class="catalog-controls" aria-busy={catalogLoading}>
          <button
            type="button"
            disabled={disabled || !chatgptConnected}
            on:click={check}
          >
            {catalogLoading
              ? "Loading models…"
              : choices.length
                ? "Refresh models"
                : "Load models"}
          </button>
          {#if catalogError}<p class="error" role="alert">
              {catalogError}
            </p>{/if}
          {#if catalogNotice}<p class="notice" role="status">
              {catalogNotice}
            </p>{/if}
        </div>
      {:else}
        <label
          >Model<input
            bind:value={profile.model}
            on:input={changeModel}
            spellcheck="false"
            list="available-models"
          /></label
        >
        <datalist id="available-models"
          >{#each suggestedModels as name}<option value={name}
            ></option>{/each}</datalist
        >
      {/if}
      <div class="fields">
        <label
          >Context window<input
            type="number"
            min="1"
            step="1"
            bind:value={profile.context_limit}
          /></label
        >
        <label
          >Reply allowance<input
            type="number"
            min="1"
            step="1"
            bind:value={profile.generation_limit}
          /></label
        >
        <label
          >Thinking<select
            use:choiceSelect={thinking}
            bind:value={thinking}
            on:change={changeThinking}
          >
            {#each thinkingOptions as option}<option value={option}
                >{option.charAt(0).toUpperCase() + option.slice(1)}</option
              >{/each}
          </select></label
        >
        <label
          >Fallback time limit (seconds)<input
            type="number"
            min="1"
            max="86400"
            bind:value={profile.timeout_seconds}
          /></label
        >
      </div>
      <p class="muted">
        The reply allowance includes thinking. Context and reply settings must
        fit the chosen model. Chat uses its saved execution time limit;
        background jobs use their saved run time limit. The fallback applies
        when no explicit execution time is selected.
      </p>
      {#if selected === "chatgpt"}<p class="muted">
          The ChatGPT connection checks the reply allowance after completion; it
          cannot cap generation on the server. A response over the allowance
          stops before its tool calls run. Time and response-size limits still
          apply.
        </p>{/if}
      {#if descriptor?.keep_loaded}<label class="checkbox"
          ><input type="checkbox" bind:checked={profile.keep_loaded} /> Keep model
          loaded</label
        >{/if}
    </fieldset>
    <div class="actions">
      {#if selected !== "chatgpt"}<button
          type="button"
          disabled={disabled ||
            (descriptor?.credential_required &&
              !["saved", "session"].includes(keyStatus))}
          on:click={check}>Check connection</button
        >{/if}
      <button
        type="button"
        disabled={disabled || (!dirty && saved.revision > 0)}
        on:click={() => save()}>Save profile</button
      >
      <button
        type="button"
        disabled={disabled ||
          dirty ||
          saved.active === selected ||
          (selected === "chatgpt" && !chatgptConnected)}
        on:click={() => save(true)}>Use for new conversations</button
      >
    </div>
    {#if checked && models.length && selected !== "chatgpt"}<p class="muted">
        Available: {models.join(", ")}
      </p>{/if}
    {#if descriptor?.credential_required}
      <section class="credential" aria-label={`${descriptor.label} API key`}>
        <h3>API key</h3>
        <p class="muted">
          {keyStatus === "saved"
            ? "Saved in your desktop keyring. Restored automatically when Kejtu starts."
            : keyStatus === "loading"
              ? "Reading the desktop keyring…"
              : keyStatus === "unavailable"
                ? "The desktop keyring could not be read. Unlock it and refresh key status."
                : keyStatus === "session"
                  ? "A key is available for this session. Save it here to remember it after restart."
                  : `No saved API key. Add it once to use ${descriptor.label} after restart.`}
        </p>
        <label
          >{keyStatus === "saved" ? "Replace API key" : "API key"}<input
            type="password"
            bind:value={key}
            autocomplete="off"
            autocapitalize="none"
            spellcheck="false"
            {disabled}
          /></label
        >
        <div class="actions">
          <button
            type="button"
            disabled={disabled || !key.trim()}
            on:click={() => changeKey("save")}>Save API key</button
          >
          <button
            type="button"
            disabled={disabled ||
              keyStatus === "missing" ||
              keyStatus === "loading"}
            on:click={() => changeKey("remove")}>Remove saved key</button
          >
          <button type="button" {disabled} on:click={() => changeKey("reload")}
            >Refresh key status</button
          >
        </div>
      </section>
    {/if}
  {/if}
  <button class="reload" type="button" disabled={busy} on:click={reload}
    >Reload saved settings</button
  >
</div>

<style>
  .models-panel {
    max-width: 680px;
    font: inherit;
  }
  .muted {
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-sm);
  }
  .active {
    margin: 24px 0;
    font-size: var(--ke-text-sm);
  }
  .catalog-controls {
    margin: -8px 0 20px;
  }
  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
    min-width: 0;
  }
  legend {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 20px;
    font-size: var(--ke-text-sm);
  }
  input,
  select {
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
  }
  .fields {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0 20px;
  }
  .checkbox {
    flex-direction: row;
    align-items: center;
  }
  .checkbox input {
    width: auto;
  }
  .credential {
    margin-top: 32px;
    border-top: 1px solid var(--ke-border-strong);
    padding-top: 24px;
  }
  .reload {
    border: 0;
    padding: 0;
    margin-top: 24px;
    color: var(--ke-text-secondary);
  }
  .error {
    color: var(--ke-danger);
    font-size: var(--ke-text-sm);
  }
  .notice {
    font-size: var(--ke-text-sm);
  }
  @media (max-width: 680px) {
    .fields {
      grid-template-columns: 1fr;
    }
  }
</style>
