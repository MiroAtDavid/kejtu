<script lang="ts">
  import { onMount } from "svelte";
  import { request } from "$lib/api/client";
  import type {
    McpStatus,
    McpServer,
    McpTool,
    FilesStatus,
    Arguments,
  } from "$lib/api/wire.generated";
  import {
    connectionName,
    includedConnections,
    inspectConnection,
    presetRepeatable,
  } from "./connections";

  export let ownerBusy = false;
  export let busy = false;
  export let active = true;
  export let onOpenFiles: () => void = () => {};
  const windows =
    typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent);
  let saved: McpStatus | null = null;
  let files: FilesStatus | null = null;
  let error = "";
  let notice = "";
  let adding = false;
  let configuration = "";
  let network = true;
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
  $: if (mounted && active && !wasActive) {
    wasActive = true;
    void refresh();
  } else if (!active) wasActive = false;
  $: server = saved?.servers.find((row) => row.id === selected);
  $: missing = includedConnections.filter(
    (name) => !saved?.servers.some((row) => row.id === name),
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
  async function refresh() {
    await action(async () => {
      receive(await request("mcp.status", {}));
      checked = {};
      try {
        files = await request("files.status", {});
      } catch {
        files = null;
      }
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
      notice = `${connectionName(row)} is on.`;
    });
  }
  async function setupIncluded() {
    if (ownerBusy || !saved) return;
    await action(async () => {
      const failures: string[] = [];
      for (const name of [...missing]) {
        if (!saved) break;
        // A conflict read-back can reveal a newly imported connection under a
        // later preset's name. It is no longer ours to set up or grant tools.
        if (saved.servers.some((row) => row.id === name)) continue;
        let result: McpStatus;
        try {
          result = await request("mcp.setup", {
            server: name,
            expected_revision: saved.revision,
            use_shared_folders: !windows && !!files?.enabled,
          });
        } catch (caught) {
          // Setup may have committed before a lost reply. Read back once;
          // never retry a mutation or continue with an unknown revision.
          receive(await request("mcp.status", {}));
          failures.push(
            `${connectionName({ id: name })}: ${caught instanceof Error ? caught.message : "Setup failed."}`,
          );
          continue;
        }
        receive(result);
        const row = result.servers.find((item) => item.id === name)!;
        // Browser's fixed safe grant is owned by its existing setup endpoint.
        // New document presets are inspected once, then receive exact names.
        try {
          const catalogue = await discover(row);
          if (!row.enabled)
            await saveUpdate({
              action: "permissions",
              server: name,
              expected_revision: saved!.revision,
              enabled: true,
              allowed_tools: catalogue.map((tool) => tool.name),
              repeatable_tools: presetRepeatable(name, catalogue),
            });
        } catch (caught) {
          failures.push(
            `${connectionName(row)}: ${caught instanceof Error ? caught.message : "Connection check failed."}`,
          );
        }
      }
      notice = "Included tools checked. Existing choices are kept.";
      if (failures.length) error = failures.join("\n");
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
      notice =
        "Connection added. Open it and choose Use all available tools, or choose a smaller selection in Advanced.";
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

<section class="connections">
  <h2 class="settings-title">Connections</h2>
  <p class="hint">
    Let Kejtu browse websites and work with documents. Turn on the connections
    you want to use.
  </p>
  <div class="actions">
    {#if saved && missing.length}<button
        disabled={busy || ownerBusy}
        on:click={setupIncluded}>Set up included tools</button
      >{/if}
    <button disabled={busy} on:click={refresh}>Refresh</button>
    <button
      disabled={busy || ownerBusy || !saved}
      aria-expanded={adding}
      on:click={() => (adding = !adding)}>Add connection</button
    >
  </div>
  {#if !windows && saved && missing.some((name) => name !== "playwright")}
    <p class="hint">
      Document setup uses the folders already shared in Files, with their
      existing read/write permissions.
    </p>
    {#if !files?.enabled}<button disabled={busy} on:click={onOpenFiles}
        >Choose folders in Files</button
      >{/if}
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="hint" role="status">{notice}</p>{/if}
  {#if busy}<p class="hint" role="status">
      Checking and saving… Each connection check can take up to 30 seconds.
    </p>{/if}
  {#if adding}
    <div class="editor">
      <h3>Add a connection</h3>
      <label
        >Server configuration<textarea
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
      <button
        disabled={busy || ownerBusy || !configuration.trim() || !saved}
        on:click={importConfig}>Save disabled</button
      >
    </div>
  {/if}
  {#if saved}
    {#if !saved.servers.length}<p class="hint">
        Set up the included tools to get started.
      </p>{/if}
    <div class="list">
      {#each saved.servers as row (row.id)}
        <div class="server" class:selected={selected === row.id}>
          <button
            class="connection-summary"
            disabled={busy}
            on:click={() => choose(row)}
          >
            <strong>{connectionName(row)}</strong>
            {#if row.description}<span>{row.description}</span>{/if}
            {#if checked[row.id]}<small>{checked[row.id]}</small>{/if}
            {#if !row.allowed_tools.length}<small>No tools selected</small>{/if}
          </button>
          <button
            class="power"
            role="switch"
            aria-checked={row.enabled}
            aria-label={connectionName(row)}
            disabled={busy || (ownerBusy && !row.enabled)}
            on:click={() =>
              row.enabled
                ? update({
                    action: "disable",
                    server: row.id,
                    expected_revision: saved!.revision,
                  })
                : enable(row)}>{row.enabled ? "On" : "Off"}</button
          >
        </div>
      {/each}
    </div>
  {/if}
  {#if server && saved}
    <div class="editor">
      <h3>{connectionName(server)}</h3>
      {#if server.requires_web}<p class="hint">
          Uses Web access and a separate temporary browser, without your
          personal logins.
        </p>{/if}
      {#if server.address.startsWith("@kejtu/") && !server.requires_web}
        <p class="hint">
          This document app uses your Windows account’s file and network access.
          The folder restrictions in Files apply to Kejtu’s built-in Files
          tools.
        </p>
      {:else if ["gridcraft", "pdfcraft", "wordcraft", "craft_files"].includes(server.id)}
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
          disabled={busy || ownerBusy}
          on:click={() => enable(server!, true)}>Use all available tools</button
        >
        <button disabled={busy || ownerBusy} on:click={inspect}
          >Check connection</button
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
        {#if !inspected}<button disabled={busy || ownerBusy} on:click={inspect}
            >Load tool choices</button
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
                <pre>{tool.input_schema}</pre>
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
            Configured credential fields: {server.credential_fields.join(", ")}.
            Values are hidden. Import the configuration again to replace
            credentials or the connection address.
          </p>{/if}
        {#if server.transport === "stdio" && !server.address.startsWith("@kejtu/")}
          <label class="check"
            ><input type="checkbox" bind:checked={localNetwork} />Allow network
            access</label
          >
          <label
            >Folders the server may read<textarea
              rows="2"
              bind:value={readPaths}
            ></textarea></label
          >
          <label
            >Folders the server may change<textarea
              rows="2"
              bind:value={writePaths}
            ></textarea></label
          >
          <p class="hint">
            One absolute folder path per line. Manage shared writable folders in
            Files. Changing access disables the connection and clears tool
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
                write_paths: writePaths
                  .split("\n")
                  .map((p) => p.trim())
                  .filter(Boolean),
              })}>Save access and disable</button
          >
        {/if}
        <p class="hint">
          Removing a connection removes its settings and credentials. Saved chat
          results remain.
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
  {/if}
</section>

<style>
  .connections {
    max-width: 48rem;
  }
  .hint,
  small {
    color: var(--ke-text-secondary);
    line-height: 1.6;
    font-size: var(--ke-text-base);
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
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: var(--ke-text-xs);
  }
  .error {
    color: var(--ke-danger);
    white-space: pre-line;
  }
</style>
