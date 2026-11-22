<script lang="ts">
  import { onMount } from "svelte";
  import RemoteSignIn from "./RemoteSignIn.svelte";
  import PackageIcon from "./PackageIcon.svelte";
  import ShelfItemLayout from "./ShelfItemLayout.svelte";
  import ShelfItemHeading from "./ShelfItemHeading.svelte";
  import ShelfCard from "./ShelfCard.svelte";
  import ShelfDialog from "./ShelfDialog.svelte";
  import {
    partIcon,
    partTitle,
    packageFacts,
    type ShelfOwner,
  } from "./shelf-library";
  import ConnectionSchema from "./ConnectionSchema.svelte";
  import { request } from "$lib/api/client";
  import type {
    McpStatus,
    McpServer,
    McpTool,
    FilesStatus,
    Arguments,
  } from "$lib/api/wire.generated";
  import {
    bundledTerminals,
    includedConnections,
    inspectConnection,
    setUpIncludedTools,
  } from "./connections";

  export let ownerBusy = false;
  export let busy = false;
  export let active = true;
  export let onOpenFiles: () => void = () => {};
  /** A connection another panel asks to open; the tick changes with each request. */
  export let focus: { name: string; tick: number } = { name: "", tick: 0 };
  export let filter = "";
  export let owners = new Map<string, ShelfOwner[]>();
  export let parent: ShelfOwner | undefined = undefined;
  export let onOpenItem: ((name: string) => void) | undefined = undefined;
  /** The name shown for a connection; its exact key stays its identity. */
  function title(id: string) {
    return partTitle("connection", id, owners.get(id)?.[0] ?? parent);
  }
  $: visible =
    saved?.servers.filter((row) =>
      [
        row.id,
        title(row.id),
        row.description,
        ...(owners.get(row.id) ?? []).map((owner) => owner.title),
      ]
        .join(" ")
        .toLowerCase()
        .includes(filter.trim().toLowerCase()),
    ) ?? [];
  const windows =
    typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent);
  const linux =
    typeof navigator !== "undefined" && /Linux/i.test(navigator.userAgent);
  export let saved: McpStatus | null = null;
  let files: FilesStatus | null = null;
  let readingFiles = false;
  let error = "";
  let notice = "";
  /** On the Shelf, the page's Add connection button opens the import window. */
  export let adding = false;
  let configuration = "";
  // Matches the backend default: an imported local server gets no network until
  // the person allows it here, or the pasted configuration says so explicitly.
  let network = false;
  let selected = "";
  let tools: McpTool[] = [];
  let allowed: string[] = [];
  let repeatable: string[] = [];
  let inspected = false;
  let readPaths = "";
  let writePaths = "";
  let localNetwork = false;
  let advanced = false;
  let toolFilter = "";
  let toolLimit = 32;
  let checked: Record<string, string> = {};
  let mounted = false;
  let wasActive = false;
  let focused = 0;
  $: if (mounted && active && !wasActive) {
    wasActive = true;
    if (onOpenItem) void readFiles();
    else void refresh();
  } else if (!active) wasActive = false;
  // Opened from Packages: select that connection once the saved list is read.
  $: if (focus.tick !== focused && saved && !busy) {
    focused = focus.tick;
    const row = saved.servers.find((item) => item.id === focus.name);
    if (row) choose(row);
    else {
      selected = "";
      error = "This connection is no longer configured.";
    }
  }
  $: server = saved?.servers.find((row) => row.id === selected);
  // A connection saved before keys moved to the keyring; never an included tool,
  // whose own launch settings are not credentials.
  $: keysInSettings =
    server?.credential_storage === "settings" &&
    !(includedConnections as readonly string[]).includes(server.id);
  $: missing = includedConnections.filter(
    (name) => !saved?.servers.some((row) => row.id === name),
  );
  $: missingTerminals = bundledTerminals.filter(
    (name) => !saved?.servers.some((row) => row.id === name),
  );
  // On the Shelf, each tool that comes with Kejtu but is missing gets a card.
  $: notSetUp = [...missing, ...(linux ? missingTerminals : [])].filter(
    (name) =>
      `${name} ${title(name)}`
        .toLowerCase()
        .includes(filter.trim().toLowerCase()),
  );
  $: matchingTools = tools.filter((tool) =>
    `${tool.name} ${tool.description}`
      .toLowerCase()
      .includes(toolFilter.toLowerCase()),
  );

  function receive(value: McpStatus) {
    saved = value;
    inspected = false;
    tools = [];
    if (selected && !value.servers.some((row) => row.id === selected))
      selected = "";
    const current = value.servers.find((row) => row.id === selected);
    if (current) {
      allowed = [...current.allowed_tools];
      repeatable = [...current.repeatable_tools];
      readPaths = current.read_paths.join("\n");
      writePaths = current.write_paths.join("\n");
      localNetwork = current.network;
    }
  }
  async function action(run: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    notice = "";
    try {
      await run();
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "The connection operation failed.";
    } finally {
      busy = false;
    }
  }
  async function readFiles() {
    readingFiles = true;
    try {
      files = await request("files.status", {});
    } catch {
      files = null;
    } finally {
      readingFiles = false;
    }
  }
  async function refresh() {
    await action(async () => {
      const value = await request("mcp.status", {});
      if (focus.name) selected = focus.name;
      receive(value);
      checked = {};
      await readFiles();
    });
  }
  function choose(row: McpServer) {
    if (busy) return;
    selected = row.id;
    tools = [];
    inspected = false;
    allowed = [...row.allowed_tools];
    repeatable = [...row.repeatable_tools];
    readPaths = row.read_paths.join("\n");
    writePaths = row.write_paths.join("\n");
    localNetwork = row.network;
    advanced = false;
    toolFilter = "";
    toolLimit = 32;
    error = "";
    notice = "";
  }
  async function discover(row: McpServer) {
    if (!saved) throw new Error("Refresh connections first.");
    try {
      const result = await inspectConnection(row.id, saved.revision);
      checked = { ...checked, [row.id]: "Connection checked" };
      return result;
    } catch (caught) {
      checked = { ...checked, [row.id]: "Needs attention" };
      throw caught;
    }
  }
  async function inspect() {
    if (ownerBusy || !server) return;
    await action(async () => {
      inspected = false;
      tools = [];
      tools = await discover(server!);
      inspected = true;
      notice = "Connection checked. No tool actions were run.";
    });
  }
  async function saveUpdate(args: Arguments["mcp.update"]) {
    try {
      receive(await request("mcp.update", args));
    } catch (caught) {
      saved = null;
      throw new Error(
        (caught instanceof Error ? caught.message : "Save was not confirmed.") +
          " Refresh to check the saved state before trying again.",
      );
    }
  }
  async function update(args: Arguments["mcp.update"]) {
    if (ownerBusy && args.action !== "disable") return;
    await action(async () => {
      await saveUpdate(args);
      notice = "Connection updated.";
    });
  }
  async function enable(row: McpServer, all = false) {
    if (ownerBusy || !saved) return;
    await action(async () => {
      const catalogue = await discover(row);
      const names = catalogue.map((tool) => tool.name);
      // Re-enabling preserves an existing limited selection. Only the explicit
      // full-suite action (or first setup) grants the current whole catalogue.
      const grants = all ? names : row.allowed_tools;
      if (grants.some((name) => !names.includes(name)))
        throw new Error(
          "Some saved tools are no longer available. Open Advanced to review the selection before turning this connection on.",
        );
      await saveUpdate({
        action: "permissions",
        server: row.id,
        expected_revision: saved!.revision,
        enabled: true,
        allowed_tools: grants,
        repeatable_tools: row.repeatable_tools.filter((name) =>
          grants.includes(name),
        ),
      });
      notice = `${title(row.id)} is on.`;
    });
  }
  async function addTerminal(name: (typeof bundledTerminals)[number]) {
    if (ownerBusy || !saved) return;
    await action(async () => {
      try {
        receive(
          await request("mcp.setup", {
            server: name,
            expected_revision: saved!.revision,
            use_shared_folders: false,
          }),
        );
      } catch (caught) {
        receive(await request("mcp.status", {}));
        throw caught;
      }
    });
  }
  /** Sets up the given included tools; a Not set up card passes only its own. */
  async function setupIncluded(
    names: readonly (typeof includedConnections)[number][] = missing,
  ) {
    if (ownerBusy || readingFiles || !saved) return;
    await action(async () => {
      const failures = await setUpIncludedTools({
        names,
        saved: saved!,
        sharedFolders: !windows && !!files?.enabled,
        status: (value) => (value ? receive(value) : (saved = null)),
        checked: (name, ok) =>
          (checked = {
            ...checked,
            [name]: ok ? "Connection checked" : "Needs attention",
          }),
      });
      notice = "Included tools checked. Existing choices are kept.";
      if (failures.length)
        error = failures
          .map((failure) => `${title(failure.name)}: ${failure.message}`)
          .join("\n");
    });
  }
  async function importConfig() {
    if (!saved || ownerBusy) return;
    await action(async () => {
      const value = JSON.parse(configuration);
      if (value?.mcpServers && typeof value.mcpServers === "object") {
        for (const row of Object.values(value.mcpServers) as Record<
          string,
          unknown
        >[]) {
          if (
            row &&
            typeof row === "object" &&
            "command" in row &&
            !("network" in row)
          )
            row.network = network;
        }
      }
      try {
        receive(
          await request("mcp.import", {
            configuration: JSON.stringify(value),
            expected_revision: saved!.revision,
          }),
        );
      } catch (caught) {
        saved = null;
        throw new Error(
          (caught instanceof Error
            ? caught.message
            : "Import was not confirmed.") + " Refresh before trying again.",
        );
      }
      configuration = "";
      adding = false;
      // A confirmed import consumed this choice; the next one starts off again.
      // Failed or unconfirmed imports keep it with the draft they belong to.
      network = false;
      const names = Object.keys(value?.mcpServers ?? {});
      const keyring = saved?.servers.some(
        (item) =>
          names.includes(item.id) && item.credential_storage === "keyring",
      );
      notice =
        "Connection added. Open it and choose Use all available tools, or choose a smaller selection in Advanced." +
        (keyring
          ? " Its keys are kept in the system keyring, not in the settings file."
          : "");
    });
  }
  function toggle(name: string, enabled: boolean) {
    allowed = enabled ? [...allowed, name] : allowed.filter((n) => n !== name);
    if (!enabled) repeatable = repeatable.filter((n) => n !== name);
  }
  onMount(() => {
    mounted = true;
  });
</script>

{#snippet power(row: McpServer)}
  <button
    class="power"
    role="switch"
    aria-checked={row.enabled}
    aria-label={title(row.id)}
    disabled={busy ||
      (ownerBusy && !row.enabled) ||
      (!row.available && !row.enabled)}
    on:click={() =>
      row.enabled
        ? update({
            action: "disable",
            server: row.id,
            expected_revision: saved!.revision,
          })
        : enable(row)}>{row.enabled ? "On" : "Off"}</button
  >
{/snippet}

<section class="connections">
  {#if focus.name && (server?.id === focus.name || error)}
    <ShelfItemHeading
      title={title(focus.name)}
      icon={partIcon("connection", focus.name, parent)}
      category={parent?.category}
      subtitle={parent ? parent.title + " · Connection" : "Connection"}
    >
      {#if server?.id === focus.name && saved}{@render power(server)}{/if}
    </ShelfItemHeading>
  {/if}
  {#if !focus.name}
    {#if !onOpenItem}<div class="actions">
        {#if saved && missing.length}<button
            disabled={busy || ownerBusy || readingFiles}
            on:click={() => setupIncluded()}>Set up included tools</button
          >{/if}
        <button disabled={busy} on:click={refresh}>Refresh</button>
        {#if linux && saved}
          {#each missingTerminals as name}
            <button
              disabled={busy || ownerBusy}
              on:click={() => addTerminal(name)}>Add {title(name)}</button
            >
          {/each}
        {/if}
        <button
          disabled={busy || ownerBusy || !saved}
          aria-expanded={adding}
          on:click={() => (adding = !adding)}>Add connection</button
        >
      </div>
    {:else if error}<div class="actions">
        <button disabled={busy} on:click={refresh}>Refresh</button>
      </div>{/if}
    {#if !windows && saved && missing.some((name) => name !== "playwright")}
      <p class="hint">
        Document setup uses the folders already shared in Files, with their
        existing read/write permissions.
      </p>
      {#if !files?.enabled}<button disabled={busy} on:click={onOpenFiles}
          >Choose folders in Files</button
        >{/if}
    {/if}
  {:else if error}
    <button disabled={busy} on:click={refresh}>Refresh</button>
  {/if}
  {#if !adding}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if notice}<p class="hint" role="status">{notice}</p>{/if}
    {#if busy}<p class="hint" role="status">
        Checking and saving… Each connection check can take up to 30 seconds.
      </p>{/if}
  {/if}
  {#if adding}
    <ShelfDialog
      title="Add connection"
      closeLabel="Close add connection"
      {busy}
      onClose={() => (adding = false)}
    >
      <label
        >Server configuration<textarea
          data-first
          rows="8"
          spellcheck="false"
          bind:value={configuration}
          placeholder={'{"mcpServers":{"example":{"command":"npx","args":["package-name@version"]}}}'}
        ></textarea></label
      >
      <p class="hint">
        Paste the mcpServers configuration from the app’s instructions.
        Replacing an existing name resets its permissions. API keys stay in
        private settings and are not included in chat.
      </p>
      <label class="check"
        ><input type="checkbox" bind:checked={network} />Allow network access
        for imported local servers</label
      >
      <p class="hint">
        {#if windows}This Windows build supports the included browser and
          document tools. Other local or remote connections are not available
          yet.{:else}Checking a connection starts its configured program.
          Personal folders are shared through Files. Use a trusted package and a
          fixed version.{/if}
      </p>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
      {#if busy}<p class="hint" role="status">
          Checking and saving… Each connection check can take up to 30 seconds.
        </p>{/if}
      <svelte:fragment slot="footer">
        <button disabled={busy} on:click={() => (adding = false)}>Cancel</button
        >
        <button
          class="primary"
          disabled={busy || ownerBusy || !configuration.trim() || !saved}
          on:click={importConfig}>Save disabled</button
        >
      </svelte:fragment>
    </ShelfDialog>
  {/if}
  {#if saved && !focus.name && onOpenItem}
    <div class="cp-cards">
      {#each visible as row (row.id)}
        {@const owner = owners.get(row.id)?.[0]}
        <ShelfCard
          title={title(row.id)}
          state={!row.available
            ? "Needs attention"
            : row.enabled
              ? "On"
              : "Off"}
          summary={owners
            .get(row.id)
            ?.map((owner) => owner.title)
            .join(" · ") || "Connection"}
          icon={partIcon("connection", row.id, owner)}
          category={owner?.category}
          part={"connection:" + row.id}
          disabled={busy}
          onOpen={() => onOpenItem?.(row.id)}
        />
      {/each}
      {#each notSetUp as name (name)}
        <ShelfCard
          title={title(name)}
          state="Not set up"
          summary="Connection"
          icon={partIcon("connection", name)}
          part={"connection:" + name}
          action="Set up"
          actionLabel={`Set up ${title(name)}`}
          actionDisabled={busy ||
            ownerBusy ||
            ((missing as readonly string[]).includes(name) && readingFiles)}
          onAction={() =>
            (bundledTerminals as readonly string[]).includes(name)
              ? addTerminal(name as (typeof bundledTerminals)[number])
              : setupIncluded([name as (typeof includedConnections)[number]])}
        />
      {/each}
    </div>
  {:else if saved && !focus.name}
    {#if !saved.servers.length}<p class="hint">
        Set up the included tools to get started.
      </p>{/if}
    <div class="list">
      {#each visible as row (row.id)}
        {@const owner = owners.get(row.id)?.[0]}
        <div class="server" class:selected={selected === row.id}>
          <button
            class="connection-summary"
            data-part={"connection:" + row.id}
            disabled={busy}
            on:click={() => (onOpenItem ? onOpenItem(row.id) : choose(row))}
          >
            <span class="connection-name"
              ><PackageIcon
                compact
                name={partIcon("connection", row.id, owner)}
                category={owner?.category}
              /><strong>{title(row.id)}</strong></span
            >
            {#if row.description}<span>{row.description}</span>{/if}
            {#if owners.has(row.id)}<small
                >{owners
                  .get(row.id)
                  ?.map((owner) => owner.title)
                  .join(" · ")}</small
              >{/if}
            {#if checked[row.id]}<small>{checked[row.id]}</small>{/if}
            {#if !row.allowed_tools.length}<small>No tools selected</small>{/if}
            {#if !row.available}<small class="attention"
                >Needs attention: its program is missing</small
              >{/if}
          </button>
          {@render power(row)}
        </div>
      {/each}
    </div>
  {/if}
  {#if saved && !focus.name && filter.trim() && !visible.length && !notSetUp.length}<p
      class="cp-empty"
    >
      No matching connections.
    </p>{/if}
  {#if server && saved}
    <ShelfItemLayout
      inline={!focus.name}
      label="Connection details"
      facts={[
        {
          label: "State",
          value: !server.available
            ? "Needs attention"
            : server.enabled
              ? "On"
              : "Off",
        },
        { label: "Name", value: server.id },
        { label: "Allowed tools", value: String(server.allowed_tools.length) },
        ...packageFacts(parent),
      ]}
    >
      <div class={focus.name ? "cp-section" : "editor"}>
        {#if focus.name && server.description}<p>{server.description}</p>{/if}
        {#if !focus.name}<h3>{title(server.id)}</h3>{/if}
        {#if server.transport === "http" && !windows}
          {#key server.id}
            <RemoteSignIn
              server={server.id}
              revision={saved.revision}
              disabled={ownerBusy || !active}
              bind:busy
              onChanged={refresh}
            />
          {/key}
        {/if}
        {#if !server.available}<p class="hint attention">
            Needs attention: this connection’s program is missing ({server.address}).
            Reinstall it, or remove the connection under Advanced. Its saved
            tool choices are kept and apply again once the program is back.
          </p>{/if}
        {#if server.transport === "http"}<p class="hint">
            Connects to the service’s online MCP server.{server.requires_web
              ? " Follows the Web access setting."
              : ""}
          </p>
        {:else if server.requires_web}<p class="hint">
            Uses Web access and a separate temporary browser, without your
            personal logins.
          </p>{/if}
        {#if server.address.startsWith("@kejtu/") && !server.requires_web}
          <p class="hint">
            This document app uses your Windows account’s file and network
            access. The folder restrictions in Files apply to Kejtu’s built-in
            Files tools.
          </p>
        {:else if ["gridcraft", "pdfcraft", "wordcraft", "craft_files", ...bundledTerminals].includes(server.id)}
          <p class="hint">
            Choose which folders this connection can use in Settings → Privacy
            &amp; access → Files.
          </p>
          {#if !files?.enabled || !files.servers.includes(server.id)}<button
              disabled={busy}
              on:click={onOpenFiles}>Choose folders in Files</button
            >{/if}
        {/if}
        <div class="actions">
          <button
            disabled={busy || ownerBusy || !server.available}
            on:click={() => enable(server!, true)}
            >Use all available tools</button
          >
          <button
            disabled={busy || ownerBusy || !server.available}
            on:click={inspect}>Check connection</button
          >
          <button
            disabled={busy}
            aria-expanded={advanced}
            on:click={() => (advanced = !advanced)}>Advanced</button
          >
        </div>
        <p class="hint">
          Turning a connection back on keeps your saved tool choices. “Use all
          available tools” checks and enables its complete current set.
        </p>
        {#if advanced}
          <h4>Tool selection</h4>
          <p class="hint">
            {server.allowed_tools.length} tools allowed. Select fewer tools here if
            you prefer.
          </p>
          {#if !inspected}<button
              disabled={busy || ownerBusy || !server.available}
              on:click={inspect}>Load tool choices</button
            >{:else}
            <label
              >Find a tool<input
                bind:value={toolFilter}
                on:input={() => (toolLimit = 32)}
                placeholder="Search tools…"
              /></label
            >
            <div class="actions">
              <button
                disabled={busy}
                on:click={() => (allowed = tools.map((tool) => tool.name))}
                >Select all</button
              ><button
                disabled={busy}
                on:click={() => {
                  allowed = [];
                  repeatable = [];
                }}>Select none</button
              >
            </div>
            {#each matchingTools.slice(0, toolLimit) as tool (tool.name)}
              <div class="tool">
                <label class="check"
                  ><input
                    type="checkbox"
                    checked={allowed.includes(tool.name)}
                    disabled={busy}
                    on:change={(event) =>
                      toggle(tool.name, event.currentTarget.checked)}
                  /><strong>{tool.name}</strong></label
                >
                <p class="hint">{tool.description}</p>
                <!-- A publisher's word, shown as such. It grants and limits nothing. -->
                {#if tool.annotations?.read_only}<p class="hint">
                    Marked read-only by its publisher.
                  </p>{:else if tool.annotations?.destructive}<p class="hint">
                    Marked by its publisher as able to delete or overwrite.
                  </p>{/if}
                <details>
                  <summary>Technical details</summary>
                  <label class="check"
                    ><input
                      type="checkbox"
                      checked={repeatable.includes(tool.name)}
                      disabled={!allowed.includes(tool.name) || busy}
                      on:change={(event) =>
                        (repeatable = event.currentTarget.checked
                          ? [...repeatable, tool.name]
                          : repeatable.filter((n) => n !== tool.name))}
                    />Allow identical calls more than once per message</label
                  >
                  {#key tool}
                    <ConnectionSchema
                      server={server.id}
                      revision={saved.revision}
                      {tool}
                    />
                  {/key}
                </details>
              </div>
            {/each}
            {#if matchingTools.length > toolLimit}<button
                disabled={busy}
                on:click={() => (toolLimit += 32)}>Show more tools</button
              >{/if}
            <button
              disabled={busy || ownerBusy}
              on:click={() =>
                update({
                  action: "permissions",
                  server: selected,
                  expected_revision: saved!.revision,
                  enabled: true,
                  allowed_tools: allowed,
                  repeatable_tools: repeatable,
                })}>Save selection and enable</button
            >
          {/if}
          <h4>Connection access</h4>
          <p class="hint">
            {server.transport === "stdio" ? "Local program" : "Remote service"}: {server.address}
          </p>
          {#if server.credential_fields.length}<p class="hint">
              Configured credential fields: {server.credential_fields.join(
                ", ",
              )}.
              {#if server.credential_storage === "keyring"}Values are kept in
                the system keyring and hidden here.{:else if keysInSettings}Values
                are hidden. Some are still stored in Kejtu’s connection settings
                file, as an earlier version saved them. Importing the
                configuration again moves them to the system keyring.{:else}Values
                are hidden.{/if}
              Import the configuration again to replace credentials or the connection
              address.
            </p>{/if}
          {#if server.transport === "stdio" && !server.address.startsWith("@kejtu/")}
            <label class="check"
              ><input type="checkbox" bind:checked={localNetwork} />Allow
              network access</label
            >
            <label
              >Folders the server may read<textarea
                rows="2"
                bind:value={readPaths}
              ></textarea></label
            >
            {#if files?.configured}
              <p class="hint">
                Writable folders are managed in Settings → Files, even when file
                access is off. Read-only program folders can still be edited
                here.
              </p>
              <button disabled={busy} on:click={onOpenFiles}
                >Manage folders in Files</button
              >
            {:else}
              <label
                >Folders the server may change<textarea
                  rows="2"
                  bind:value={writePaths}
                ></textarea></label
              >
            {/if}
            <p class="hint">
              One absolute folder path per line. Manage shared writable folders
              in Files. Changing access disables the connection and clears tool
              permissions.
            </p>
            <button
              disabled={busy || ownerBusy}
              on:click={() =>
                update({
                  action: "access",
                  server: selected,
                  expected_revision: saved!.revision,
                  network: localNetwork,
                  read_paths: readPaths
                    .split("\n")
                    .map((p) => p.trim())
                    .filter(Boolean),
                  write_paths: files?.configured
                    ? []
                    : writePaths
                        .split("\n")
                        .map((p) => p.trim())
                        .filter(Boolean),
                })}>Save access and disable</button
            >
          {/if}
          <p class="hint">
            Removing a connection removes its settings and credentials. Saved
            chat results remain.
          </p>
          <button
            disabled={busy || ownerBusy}
            on:click={() =>
              update({
                action: "remove",
                server: selected,
                expected_revision: saved!.revision,
              })}>Remove connection</button
          >
        {/if}
      </div>
    </ShelfItemLayout>
  {/if}
</section>

<style>
  .cp-section > :first-child {
    margin-top: 0;
  }
  .connections {
    max-width: 48rem;
  }
  .hint,
  small {
    color: var(--ke-text-secondary);
    line-height: 1.6;
    font-size: var(--ke-text-base);
  }
  .attention {
    color: var(--ke-warning);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .list {
    display: grid;
    gap: 0.65rem;
    margin-block: 1.25rem;
  }
  .server {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.8rem 1rem;
    border: 1px solid var(--ke-border);
    border-radius: var(--ke-radius-lg);
  }
  .connections .connection-summary {
    display: grid;
    gap: 0.3rem;
    flex: 1;
    min-width: 0;
    text-align: left;
    background: none;
    padding: 0;
    border: 0;
    overflow-wrap: anywhere;
  }
  .connections .connection-summary:hover:not(:disabled) {
    background: transparent;
  }
  .connection-summary span {
    font-size: var(--ke-text-sm);
    color: var(--ke-text-secondary);
    font-weight: normal;
  }
  .connection-summary .connection-name {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--ke-text-primary);
  }
  .power {
    min-width: 3.5rem;
    flex: none;
  }
  .power[aria-checked="true"] {
    background: var(--ke-accent);
    color: var(--ke-on-accent, white);
  }
  .selected {
    background: var(--ke-surface-inset);
  }
  .editor {
    border-top: 1px solid var(--ke-border);
    padding-top: 1rem;
    margin-top: 1rem;
  }
  label {
    display: grid;
    gap: 0.4rem;
    margin: 0.7rem 0;
    font-size: var(--ke-text-base);
  }
  .check {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
  }
  .check input {
    flex: none;
    margin-top: 0.15rem;
  }
  textarea {
    box-sizing: border-box;
    width: 100%;
    font-family: var(--ke-font-mono);
  }
  .tool {
    border-bottom: 1px solid var(--ke-border);
    padding-bottom: 0.6rem;
    margin-block: 0.7rem;
    overflow-wrap: anywhere;
  }
  .error {
    color: var(--ke-danger);
    white-space: pre-line;
  }
</style>
