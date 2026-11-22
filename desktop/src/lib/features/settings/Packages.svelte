<script lang="ts">
  import "$lib/design/collection.css";
  import { onMount, onDestroy, tick } from "svelte";
  import Icon from "$lib/design/Icon.svelte";
  import { toPageTop } from "$lib/design/collection";
  import {
    chooseFolder,
    identity,
    NativeFailure,
    request,
  } from "$lib/api/client";
  import type {
    McpStatus,
    ShelfCatalogue,
    ShelfCatalogueEntry,
    ShelfData,
    ShelfDescription,
    ShelfInstallation,
    ShelfReceipt,
    ShelfStatus,
    SkillStatus,
  } from "$lib/api/wire.generated";
  import Skills from "./Skills.svelte";
  import Connections from "./Connections.svelte";
  import { readSkillLibrary } from "./skill-library";
  import {
    packageIdentity,
    packageFacts,
    partIcon,
    partTitle,
    readableName,
    shelfLibrary,
    type ShelfFilter,
    type ShelfOwner,
  } from "./shelf-library";
  import PackageShelf from "./PackageShelf.svelte";
  import PackageImport from "./PackageImport.svelte";
  import ShelfItemLayout from "./ShelfItemLayout.svelte";
  import ShelfItemHeading from "./ShelfItemHeading.svelte";
  import ShelfCard from "./ShelfCard.svelte";
  import PackageIcon from "./PackageIcon.svelte";
  import PackagePreview from "./PackagePreview.svelte";
  import RemoteSignIn from "./RemoteSignIn.svelte";
  import LocalAccountSetup from "./LocalAccountSetup.svelte";
  import { setupPackage, type SetupResult } from "./package-setup";
  import {
    attention,
    included,
    overlapsFor,
    type PackageOverlap,
    origin,
    packageState,
    packageStateNames,
    partsOf,
    partStateNames,
    size,
    stamp,
    stepLine,
    summary,
    unsettled,
  } from "./packages";

  /**
   * Packages: skills and tools installed together, through the Shelf coordinator.
   *
   * Install & enable completes setup through the existing Skills and Connections
   * owners. Account sign-in stays here; individual controls remain in Advanced.
   * Merely opening an installed package never changes its permissions.
   *
   * No operation is repeated here. After any result that is not a plain refusal,
   * the saved state is read back and shown.
   *
   * It also shows what a backup takes of each package's own data, with the one
   * choice a person has (leave it out), and what a restored backup left to do.
   */
  export let ownerBusy = false;
  export let busy = false;
  export let active = true;
  export let entry: { filter?: ShelfFilter } = {};
  export let onOpenFiles: () => void = () => {};
  export let onOpenSkills: (name: string) => void = (name) =>
    openPart("skill", name);
  export let onOpenConnections: (name: string) => void = (name) =>
    openPart("connection", name);
  let listBusy = false;
  let partBusy = false;
  $: busy = listBusy || partBusy;
  let lastEntry: typeof entry | null = null;
  let part: { kind: "skill" | "connection"; name: string } | null = null;
  let returnFocus: HTMLElement | null = null;
  let filter: ShelfFilter = "All";
  const filters: ShelfFilter[] = ["All", "Packages", "Skills", "Connections"];
  let installedQuery = "";
  // The page's one add button follows the chip: a package folder, a skill or a connection.
  let skillAdding = false;
  let connectionAdding = false;
  $: addKind =
    view === "installed" && filter === "Skills"
      ? ("skill" as const)
      : view === "installed" && filter === "Connections"
        ? ("connection" as const)
        : ("package" as const);
  function startAdding() {
    error = "";
    if (addKind === "skill") skillAdding = true;
    else if (addKind === "connection") connectionAdding = true;
    else adding = true;
  }
  $: library = shelfLibrary(
    shelf?.items ?? [],
    catalogue?.entries ?? [],
    skills,
    connections?.servers ?? null,
  );
  $: visibleRows = library.rows.filter(
    (row) =>
      (filter !== "Packages" || row.kind === "package") &&
      row.search.includes(installedQuery.trim().toLowerCase()),
  );
  function openPart(kind: "skill" | "connection", name: string) {
    if (busy) return;
    returnFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    part = { kind, name };
  }
  async function leavePart() {
    if (busy) return;
    const previous = part;
    part = null;
    await refresh();
    await tick();
    if (returnFocus?.isConnected) returnFocus.focus();
    else if (previous) {
      const trigger = [
        ...root.querySelectorAll<HTMLElement>("[data-part]"),
      ].find(
        (node) => node.dataset.part === previous.kind + ":" + previous.name,
      );
      trigger?.focus();
    }
    returnFocus = null;
  }

  // Refusals the coordinator gives before anything is recorded: the draft stays.
  const refusals = [
    "skill_exists",
    "mcp_exists",
    "mcp_credentials",
    "mcp_configuration",
    "mcp_unavailable",
    "mcp_installation",
    "shelf_collision",
    "shelf_installed",
    "shelf_attention",
    "shelf_busy",
    "shelf_web",
    "shelf_source",
    "shelf_catalogue",
    "shelf_download",
    "shelf_time",
    "shelf_record",
    "shelf_unsupported",
    "package_included",
    "shelf_dependency",
    "native_busy",
  ];

  let shelf: ShelfStatus | null = null;
  let catalogue: ShelfCatalogue | null = null;
  let catalogueError = "";
  let packageError = "";
  let shelfChoice: ShelfCatalogueEntry | null = null;
  let view: "browse" | "installed" = "browse";
  $: source = shelfChoice
    ? {
        kind: "catalogue" as const,
        id: shelfChoice.id,
        digest: shelfChoice.digest,
      }
    : { kind: "folder" as const, directory: preview?.directory ?? "" };

  let connections: McpStatus | null = null;
  let skillStatus: SkillStatus | null = null;
  $: skills = skillStatus?.items ?? null;
  let skillsRevision = 0;
  // What packages keep and what backups take; null when it could not be read.
  let data: ShelfData | null = null;
  let error = "";
  let notice = "";
  let lines: string[] = [];
  let adding = false;
  let directory = "";
  let preview: ShelfDescription | null = null;
  let target: ShelfInstallation | null = null;
  // Where the preview is shown: on the page, or in the card of the package it would update.
  let previewAt: "form" | "detail" = "form";
  let collision = "";
  let doing = "";
  let selected = "";
  let removing = false;
  let deleteData = false;
  let mounted = false;
  let wasActive = false;
  let setupAccount:
    | (Exclude<SetupResult, { kind: "ready" }> & { installation: string })
    | null = null;
  let setupReady = "";
  let collapsedParts = "";
  $: advanced = !!current && collapsedParts !== current.id;
  let shelfQuery = "";
  let shelfCategory = "All";
  // An installed package opened from a Browse card goes back to Browse.
  let openedFromBrowse = false;
  /** Back lands on the card the page was opened from. */
  async function focusCard(key: "entry" | "package", id: string | undefined) {
    if (!id) return;
    await tick();
    const card = [
      ...(root?.querySelectorAll<HTMLElement>(`[data-${key}]`) ?? []),
    ].find((node) => node.dataset[key] === id);
    (card?.matches("button")
      ? card
      : card?.querySelector<HTMLElement>(".open")
    )?.focus();
  }
  function leavePackage() {
    const id = shelfChoice?.id;
    dismiss();
    void focusCard("entry", id);
  }
  function leaveInstalled() {
    if (!current || busy) return;
    const { id, origin: from } = current;
    const toBrowse = openedFromBrowse && !!from.catalogue_id;
    choose(current);
    if (toBrowse) {
      view = "browse";
      void focusCard("entry", from.catalogue_id);
    } else void focusCard("package", id);
  }
  let root: HTMLElement;
  // One thing at a time: a package's own page (from the Shelf or a folder,
  // or an installed one) takes the place of the lists.
  $: page = part
    ? "part"
    : preview && previewAt === "form"
      ? "package"
      : current && view === "installed"
        ? "installed"
        : "list";
  let shownPage = "list";
  $: if (page !== shownPage) {
    shownPage = page;
    void tick().then(() => toPageTop(root));
  }

  $: if (mounted && active && (!wasActive || entry !== lastEntry)) {
    wasActive = true;
    if (entry !== lastEntry) {
      lastEntry = entry;
      resetList(entry.filter ? "installed" : "browse");
      part = null;
      installedQuery = "";
      filter = entry.filter ?? "All";
    }
    if (!part) void refresh();
  } else if (!active) {
    wasActive = false;
    adding = false;
    skillAdding = false;
    connectionAdding = false;
  }
  $: overlaps = preview
    ? overlapsFor(
        preview,
        shelf?.items ?? [],
        connections?.servers ?? null,
        skills,
        target,
      )
    : [];
  $: unread = preview ? unreadOwners(preview, shelf, connections, skills) : [];
  function unreadOwners(
    found: ShelfDescription,
    packages: ShelfStatus | null,
    servers: McpStatus | null,
    library: SkillStatus["items"] | null,
  ) {
    const missing: string[] = [];
    if (!packages) missing.push("installed packages");
    if (found.components.some((part) => part.kind !== "skill") && !servers)
      missing.push("connections");
    if (found.components.some((part) => part.kind === "skill") && !library)
      missing.push("skills");
    return missing;
  }
  function openOverlap(item: PackageOverlap) {
    if (item.kind === "connection") onOpenConnections(item.key);
    else if (item.kind === "skill") onOpenSkills(item.key);
    else {
      // Opening is a choice of the existing installation, never an adoption.
      dismiss();
      selected = item.key;
      view = "installed";
      removing = false;
      deleteData = false;
    }
  }
  $: current = shelf?.items.find((item) => item.id === selected) ?? null;
  $: currentParts = current
    ? partsOf(current, connections?.servers ?? null, skills)
    : [];
  $: currentState = current ? packageState(current, currentParts) : "off";
  $: hasEnabledParts = currentParts.some((part) => part.state === "on");
  $: currentEntry = catalogue?.entries.find(
    (entry) => entry.id === current?.origin.catalogue_id,
  );
  $: currentOwner = current
    ? packageIdentity(current, currentEntry)
    : undefined;
  /** A package's name and what it lends its parts' names. */
  function ownerOf(item: ShelfInstallation) {
    return packageIdentity(
      item,
      catalogue?.entries.find((entry) => entry.id === item.origin.catalogue_id),
    );
  }
  function packageTitle(id: string, identity: string) {
    const item = shelf?.items.find((item) => item.id === id);
    return item ? ownerOf(item).title : readableName(identity);
  }
  function connectionTitle(id: string) {
    return partTitle("connection", id, library.connectionOwners.get(id)?.[0]);
  }
  // A shared part follows the package it was opened from. In a filter, the row
  // and its page use the same first owner. Replaced parts have lost that owner.
  function ownerFor(owners: ShelfOwner[] | undefined, preferred?: string) {
    return owners?.find((owner) => owner.id === preferred) ?? owners?.[0];
  }
  $: focusedOwner = part
    ? ownerFor(
        (part.kind === "skill"
          ? library.skillOwners
          : library.connectionOwners
        ).get(part.name),
        current?.id,
      )
    : undefined;
  $: needsSetup =
    currentState !== "on" ||
    currentParts.some(
      (part) =>
        part.kind === "connection" &&
        !connections?.servers.find((row) => row.id === part.name)?.allowed_tools
          .length,
    );
  $: hasData = !!current?.components.some(
    (component) => component.kind === "plugin_data",
  );
  $: kept = data?.items.find((item) => item.installation_id === current?.id);
  // A plain read (opening the Shelf, Refresh, opening a package) is quiet:
  // controls are held for its moment, but nothing dims and no line is added,
  // so the page does not flicker or jump each time it is opened.
  let quiet = false;
  async function action(run: () => Promise<void>, label = "") {
    if (listBusy || partBusy) return;
    listBusy = true;
    quiet = !label;
    doing = label || "Reading…";
    error = "";
    notice = "";
    lines = [];
    try {
      await run();
    } catch (caught) {
      error =
        caught instanceof Error
          ? caught.message
          : "The package operation could not be confirmed.";
    } finally {
      listBusy = false;
    }
  }

  /** The record, and each part's state from the owner that holds it. */
  async function load(allowPartial = false) {
    // A standalone item can exist even when there are no packages.
    const [value, servers, found] = await Promise.all([
      request("shelf.status", {}).catch((cause) => {
        if (!allowPartial) throw cause;
        return null;
      }),
      request("mcp.status", {}).catch(() => null),
      readSkillLibrary().catch(() => null),
    ]);
    let held: ShelfData | null = null;
    try {
      held = await request("shelf.data", {});
    } catch {
      held = null;
    }
    shelf = value;
    packageError = value
      ? ""
      : "Packages could not be read. Skills and Connections are still available.";
    connections = servers;
    skillStatus = found;
    if (found) skillsRevision = found.revision;
    data = held;
    if (selected && value && !value.items.some((item) => item.id === selected))
      selected = "";
  }
  async function refresh() {
    collision = "";
    await action(async () => {
      try {
        catalogue = await request("shelf.catalogue", {});
        catalogueError = "";
      } catch {
        catalogueError =
          "The Shelf could not be loaded. Your installed packages are still available.";
      }
      await load(true);
    });
  }

  /** A mutation's answer, or its error after the saved state was read back. */
  async function settle<T>(send: () => Promise<T>): Promise<T> {
    try {
      return await send();
    } catch (caught) {
      const refused =
        caught instanceof NativeFailure && refusals.includes(caught.code);
      try {
        await load();
      } catch {
        shelf = null;
      }
      if (refused) throw caught;
      preview = null;
      target = null;
      throw new Error(
        (caught instanceof Error
          ? caught.message
          : "The result could not be confirmed.") +
          " Nothing is repeated. The list shows what is there now.",
      );
    }
  }
  function report(receipt: ShelfReceipt) {
    lines = receipt.steps.map(stepLine);
  }

  async function pick() {
    await action(async () => {
      const chosen = await chooseFolder(directory.trim());
      if (chosen) directory = chosen;
    }, "Choosing a folder…");
  }
  async function look(folder: string, installation: ShelfInstallation | null) {
    if (!folder) return;
    await action(async () => {
      collision = "";
      preview = null;
      shelfChoice = null;
      const found = await request("shelf.describe", { directory: folder });
      await load();
      // Only "Look for an update" chooses an existing installation. A matching
      // display name from another folder is not evidence of the same package.
      target = installation;
      previewAt = installation ? "detail" : "form";
      preview = found;
      adding = false;
    }, "Looking at the package…");
  }
  function resetList(next: "installed" | "browse") {
    dismiss();
    view = next;
    selected = "";
    collapsedParts = "";
    removing = false;
    deleteData = false;
    adding = false;
  }
  function dismiss() {
    shelfChoice = null;
    preview = null;
    target = null;
    collision = "";
  }

  async function chooseShelf(
    entry: ShelfCatalogueEntry,
    installation: ShelfInstallation | null = null,
  ) {
    await action(async () => {
      dismiss();
      adding = false;
      await load();
      shelfChoice = entry;
      preview = entry.package;
      target = installation;
      previewAt = installation ? "detail" : "form";
      view = installation ? "installed" : "browse";
    });
  }

  /** Install from a Shelf card: open the package's page, then the same install
   *  as its button, with the same checks. Anything in the way stays on the page. */
  async function installFromCard(entry: ShelfCatalogueEntry) {
    if (busy || ownerBusy) return;
    await chooseShelf(entry);
    if (shelfChoice?.id === entry.id && preview === entry.package)
      await install();
  }

  async function install() {
    if (ownerBusy || !preview?.installable) return;
    const found = preview;
    await action(async () => {
      // Re-read before submitting: a connection may have changed since preview.
      await load();
      if (
        unreadOwners(found, shelf, connections, skills).length ||
        overlapsFor(
          found,
          shelf?.items ?? [],
          connections?.servers ?? null,
          skills,
          null,
        ).some((item) => item.blocking)
      )
        return;
      let receipt: ShelfReceipt;
      try {
        receipt = await settle(() =>
          request("shelf.install", {
            mutation_key: identity(),
            identity: found.name,
            version: found.version,
            source,
            components: found.components,
            replace_existing: false,
          }),
        );
      } catch (caught) {
        if (
          caught instanceof NativeFailure &&
          [
            "skill_exists",
            "mcp_exists",
            "mcp_credentials",
            "shelf_collision",
            "shelf_installed",
          ].includes(caught.code)
        ) {
          collision =
            "An existing package, skill or connection now conflicts with this install. Nothing was installed. Check again and open the existing item to remove it first.";
          return;
        }
        throw caught;
      }
      dismiss();
      adding = false;
      directory = "";
      await load();
      report(receipt);
      const listed = shelf?.items.some(
        (item) => item.id === receipt.installation_id,
      );
      if (listed) {
        selected = receipt.installation_id;
        view = "installed";
      }
      if (receipt.status === "completed" && listed) {
        await finishSetup(receipt.installation_id);
      } else
        error = listed
          ? "The install did not complete. The package is listed and needs attention."
          : "The install did not complete. Nothing was installed.";
    }, "Installing… A package that downloads a program can take a few minutes.");
  }

  async function finishSetup(id: string) {
    setupReady = "";
    setupAccount = null;
    const item = shelf?.items.find((entry) => entry.id === id);
    if (!item)
      throw new Error("Read the installed package before finishing setup.");
    doing = "Setting up the package…";
    let result: SetupResult;
    try {
      result = await setupPackage(item, () => mounted && active && !ownerBusy);
    } finally {
      // Inspect saved state after every outcome, including lost mutation replies.
      // A failed step is never automatically submitted again.
      await load();
    }
    if (result.kind !== "ready") {
      setupAccount = { ...result, installation: id };
      notice = result.message;
    } else {
      const saved = shelf?.items.find((entry) => entry.id === id);
      const parts = saved
        ? partsOf(saved, connections?.servers ?? null, skills)
        : [];
      if (
        !parts.length ||
        parts.some((part) => part.state !== "on") ||
        connections?.servers.some(
          (row) => row.installation_id === id && !row.allowed_tools.length,
        )
      )
        throw new Error(
          "Setup could not be confirmed. Check the saved package and finish setup when ready.",
        );
      setupReady = id;
      notice = "Ready to use. The package’s skills and tools are enabled.";
    }
  }
  async function enablePackage() {
    if (!current || ownerBusy) return;
    const id = current.id;
    await action(() => finishSetup(id), "Setting up the package…");
  }
  async function accountConnected() {
    const id = setupAccount?.installation;
    if (!id || selected !== id || !active || ownerBusy) return;
    await action(() => finishSetup(id), "Finishing setup…");
  }

  async function update() {
    if (ownerBusy || !preview?.installable || !target) return;
    const found = preview;
    const installation = target;
    // What each of its tools could use before: an update never carries that over.
    const earlier = new Map(
      (connections?.servers ?? []).map((row) => [row.id, row.allowed_tools]),
    );
    await action(async () => {
      await load();
      if (
        unreadOwners(found, shelf, connections, skills).length ||
        overlapsFor(
          found,
          shelf?.items ?? [],
          connections?.servers ?? null,
          skills,
          installation,
        ).some((item) => item.blocking)
      )
        return;
      const receipt = await settle(() =>
        request("shelf.update", {
          mutation_key: identity(),
          installation_id: installation.id,
          version: found.version,
          source,
          components: found.components,
        }),
      );
      dismiss();
      await load();
      report(receipt);
      for (const step of receipt.steps) {
        const before = earlier.get(step.component_key) ?? [];
        if (
          step.component_kind === "connection" &&
          step.action === "register" &&
          step.outcome === "applied" &&
          before.length
        )
          lines = [
            ...lines,
            `Tool ${partTitle("connection", step.component_key, ownerOf(installation))}: before the update it could use ${before.join(", ")}. Check it and choose again in Connections.`,
          ];
      }
      selected = installation.id;
      if (receipt.status === "completed")
        notice = `Updated to ${found.version}. A changed tool or skill is switched off until you turn it on again.`;
      else
        error =
          "The update did not complete. The package shows which version it is on.";
    }, "Updating… A new program can take a few minutes to download.");
  }

  async function repair() {
    if (ownerBusy || !current) return;
    const installation = current;
    await action(async () => {
      const receipt = await settle(() =>
        request("shelf.repair", {
          mutation_key: identity(),
          installation_id: installation.id,
        }),
      );
      await load();
      report(receipt);
      if (receipt.status === "completed")
        notice = "Checked. Nothing about it is unsettled.";
      else
        error =
          "Some parts are missing or still need attention. Reinstall missing programs, or check the details below.";
    }, "Checking what is there…");
  }

  async function reinstall(installationId: string) {
    if (ownerBusy) return;
    await action(async () => {
      const receipt = await settle(() =>
        request("shelf.repair", {
          mutation_key: identity(),
          installation_id: installationId,
          reinstall: true,
        }),
      );
      await load();
      report(receipt);
      if (receipt.status === "completed")
        notice =
          "Programs reinstalled. Check and enable their tools in Connections. Existing data was kept.";
      else
        error =
          "The programs could not all be reinstalled. Check the details below.";
    }, "Reinstalling programs… This can take a few minutes.");
  }

  async function remove() {
    if (ownerBusy || !current) return;
    const installation = current;
    const erase = hasData && deleteData;
    await action(async () => {
      const receipt = await settle(() =>
        request("shelf.remove", {
          mutation_key: identity(),
          installation_id: installation.id,
          delete_data: erase,
        }),
      );
      removing = false;
      deleteData = false;
      await load();
      report(receipt);
      if (receipt.status === "completed")
        notice = `${ownerOf(installation).title} was removed. Conversations and what its tools found stay readable.`;
      else error = "The removal did not complete. The package needs attention.";
    }, "Removing…");
  }

  /** Switch off every part that is on, through the owner of each. */
  async function turnOff() {
    if (!current) return;
    const parts = currentParts.filter((part) => part.state === "on");
    await action(async () => {
      try {
        for (const part of parts) {
          if (part.kind === "connection")
            connections = await request("mcp.update", {
              action: "disable",
              server: part.name,
              expected_revision: connections!.revision,
            });
          else
            skillsRevision = (
              await request("skills.enable", {
                name: part.name,
                revision: part.revision,
                enabled: false,
                expected_revision: skillsRevision,
              })
            ).revision;
        }
      } catch (caught) {
        await load();
        throw new Error(
          (caught instanceof Error ? caught.message : "A part was not saved.") +
            " The list shows what is on now.",
        );
      }
      await load();
      notice = "Switched off.";
    }, "Switching off…");
  }

  /** Take this package's own data into backups, or leave it out. */
  async function keepInBackups(include: boolean) {
    if (!current || !data) return;
    const installation = current;
    const revision = data.revision;
    await action(async () => {
      try {
        data = await request("shelf.data_choice", {
          expected_revision: revision,
          installation_id: installation.id,
          include,
        });
      } catch (caught) {
        await load();
        throw caught;
      }
      notice = include
        ? "Its data is part of backups again."
        : "Its data is left out of backups from now on. Each backup says so.";
    }, "Saving…");
  }
  async function restoredSeen() {
    await action(async () => {
      data = await request("shelf.restored_seen", {});
    }, "Saving…");
  }

  function choose(item: ShelfInstallation) {
    if (busy) return;
    openedFromBrowse = false;
    view = "installed";
    selected = selected === item.id ? "" : item.id;
    collapsedParts = "";
    setupAccount = null;
    setupReady = "";
    if (previewAt === "detail") dismiss();
    removing = false;
    deleteData = false;
    error = "";
    notice = "";
    lines = [];
  }
  onMount(() => {
    mounted = true;
  });
  onDestroy(() => {
    mounted = false;
  });
</script>

{#snippet messages(progress = true)}
  {#if ownerBusy}<p class="cp-hint waiting">
      Installing, updating and removing wait until the current reply is
      finished.
    </p>{/if}
  {#if error && !adding}<p role="alert" class="cp-note">{error}</p>{/if}
  {#if notice}<p role="status" class="cp-note">{notice}</p>{/if}
  {#if lines.length}
    <ul class="steps" aria-label="What was done">
      {#each lines as line}<li>{line}</li>{/each}
    </ul>
  {/if}
  {#if progress && busy && !adding && !quiet}<p
      role="status"
      class="cp-hint waiting"
    >
      {doing}
    </p>{/if}
{/snippet}

<section
  class="packages collection-page"
  class:quiet={busy && quiet}
  aria-label="Shelf"
  bind:this={root}
>
  {#if part}
    <button class="cp-back" disabled={busy} on:click={leavePart}
      ><Icon name="left" size={13} />{currentOwner?.title ??
        (preview ? "Package" : "Installed")}</button
    >
    <div class="settings-page shelf-editor">
      {#if part.kind === "skill"}
        <Skills
          {ownerBusy}
          bind:busy={partBusy}
          parent={focusedOwner}
          focus={{ name: part.name, tick: 1 }}
        />
      {:else}
        <Connections
          {ownerBusy}
          {active}
          {onOpenFiles}
          bind:busy={partBusy}
          parent={focusedOwner}
          focus={{ name: part.name, tick: 1 }}
        />
      {/if}
    </div>
  {:else if page === "package" && preview}
    <button
      class="cp-back"
      aria-label="Back to the Shelf"
      disabled={busy}
      on:click={leavePackage}><Icon name="left" size={13} />Shelf</button
    >
    {@render messages()}
    <PackagePreview
      {preview}
      shelfEntry={shelfChoice}
      onRefresh={refresh}
      {target}
      {collision}
      {overlaps}
      {unread}
      onOpenOverlap={openOverlap}
      onRecheck={refresh}
      {busy}
      {ownerBusy}
      onInstall={install}
      onUpdate={update}
      onDismiss={dismiss}
    />
  {:else if page === "installed" && current && currentOwner}
    <button
      class="cp-back"
      aria-label={openedFromBrowse
        ? "Back to the Shelf"
        : "Back to installed packages"}
      disabled={busy}
      on:click={leaveInstalled}
      ><Icon name="left" size={13} />{openedFromBrowse
        ? "Shelf"
        : "Installed"}</button
    >
    <ShelfItemHeading
      tag="strong"
      icon={currentOwner.icon}
      category={currentOwner.category}
      title={currentOwner.title}
      subtitle={summary(current)}
    >
      {#if hasEnabledParts}
        <button class="cp-action" disabled={busy} on:click={turnOff}
          >Disable all</button
        >
      {:else if currentParts.length && needsSetup && !unsettled(current)}
        <button
          class="cp-action primary"
          disabled={busy || ownerBusy}
          on:click={enablePackage}
          >{setupAccount?.installation === current.id
            ? "Check connection again"
            : "Enable"}</button
        >
      {/if}
    </ShelfItemHeading>
    {@render messages()}
    <ShelfItemLayout
      label="Package details"
      facts={[
        { label: "State", value: packageStateNames[currentState] },
        ...packageFacts(currentOwner, "Version"),
      ]}
    >
      <section class="cp-section detail">
        <div class="detail-heading">
          <h3>{current.identity}</h3>
          <button
            class="cp-icon"
            aria-label="Refresh"
            title="Refresh"
            disabled={busy}
            on:click={refresh}><Icon name="refresh" size={14} /></button
          >
        </div>
        {#if current.origin.catalogue_id}
          {@const available = catalogue?.entries.find(
            (entry) => entry.id === current!.origin.catalogue_id,
          )}
          {#if available && available.digest !== current.origin.catalogue_digest}
            <div class="actions">
              <button
                disabled={busy || ownerBusy}
                on:click={() => chooseShelf(available, current)}
                >Review available update</button
              >
            </div>
          {:else if available}<p class="cp-hint">
              This is the version offered on the Shelf.
            </p>{/if}
        {/if}
        {#if included(current)}
          <p>{current.origin.description}</p>
          <p class="cp-hint">
            Included with Kejtu. Guidance loads when relevant. You can disable
            it; this package stays installed and updates with the app.
          </p>
        {:else}
          <p class="cp-hint">
            Version {current.version}, from {origin(current)}
          </p>
        {/if}
        {#if unsettled(current)}
          <p class="attention" role="note">{attention(current)}</p>
          {#if current.unresolved.length}
            <ul class="steps" aria-label="Unsettled steps">
              {#each current.unresolved as step}<li>
                  {stepLine({ ...step, code: null })}
                </li>{/each}
            </ul>
          {/if}
        {/if}
        {#if setupAccount?.installation === current.id}
          <!-- Account setup keeps the look it has in Settings → Connections. -->
          <section class="settings-page" aria-label="Finish package setup">
            <h4>Connect {setupAccount.server}</h4>
            {#key setupAccount.server}
              {#if setupAccount.kind === "inputs"}
                <LocalAccountSetup
                  server={setupAccount.server}
                  revision={connections?.revision ?? setupAccount.revision}
                  fields={setupAccount.fields}
                  disabled={ownerBusy || !active || listBusy}
                  bind:busy={partBusy}
                  onChanged={() =>
                    action(() => load(), "Checking account setup…")}
                  onConnected={accountConnected}
                />
              {:else}
                <RemoteSignIn
                  server={setupAccount.server}
                  revision={connections?.revision ?? setupAccount.revision}
                  disabled={ownerBusy || !active}
                  setup
                  bind:busy
                  onChanged={() => action(() => load(), "Checking sign-in…")}
                  onConnected={accountConnected}
                />
              {/if}
            {/key}
          </section>
        {/if}
        {#each connections?.servers.filter((row) => row.installation_id === current.id && row.inputs?.length) ?? [] as row}
          {#if setupAccount?.server !== row.id}
            <div class="actions">
              <button
                disabled={busy || ownerBusy}
                on:click={() => {
                  setupAccount = {
                    kind: "inputs",
                    server: row.id,
                    revision: connections!.revision,
                    fields: row.inputs!,
                    message: "Enter new account details.",
                    installation: current!.id,
                  };
                }}>Account details</button
              >
            </div>
          {/if}
        {/each}
        {#if currentParts.length}
          <div class="actions">
            {#if hasEnabledParts && needsSetup && !unsettled(current)}
              <button
                class:cp-action={setupAccount?.installation !== current.id}
                class:primary={setupAccount?.installation !== current.id}
                disabled={busy || ownerBusy}
                on:click={enablePackage}
                >{setupAccount?.installation === current.id
                  ? "Check connection again"
                  : "Finish setup & enable"}</button
              >
            {:else if !needsSetup}
              <span class="cp-hint"
                >{setupReady === current.id ? "Ready to use" : "Enabled"}</span
              >
            {/if}
            <button
              disabled={busy}
              aria-expanded={advanced}
              on:click={() => (collapsedParts = advanced ? current!.id : "")}
              >Advanced</button
            >
          </div>
        {/if}
        {#if currentParts.length && advanced}
          <ul class="parts installed" aria-label="Its parts">
            {#each currentParts as part (part.kind + part.name)}
              {@const parent = ownerFor(
                (part.kind === "skill"
                  ? library.skillOwners
                  : library.connectionOwners
                ).get(part.name),
                current.id,
              )}
              <li>
                <span class="part-identity">
                  <PackageIcon
                    compact
                    name={partIcon(part.kind, part.name, parent)}
                    category={parent?.category}
                  />
                  <span class="part-label"
                    ><strong>{partTitle(part.kind, part.name, parent)}</strong>
                    <span
                      class="cp-hint"
                      class:attention={part.state === "missing" ||
                        part.state === "unavailable"}
                      >{part.kind === "skill" ? "Skill" : "Tool"} · {partStateNames[
                        part.state
                      ]}</span
                    ></span
                  >
                </span>
                <button
                  disabled={busy}
                  on:click={() =>
                    part.kind === "skill"
                      ? onOpenSkills(part.name)
                      : onOpenConnections(part.name)}
                  data-part={part.kind + ":" + part.name}
                  aria-label={`Open ${partTitle(part.kind, part.name, parent)} in ${part.kind === "skill" ? "Skills" : "Connections"}`}
                  >{part.kind === "skill"
                    ? "Open in Skills"
                    : "Open in Connections"}</button
                >
              </li>
            {/each}
          </ul>
          <p class="cp-hint">
            Adjust individual skills or tool selections here. Setup enables the
            package’s tools without running any of their actions.
          </p>
        {/if}
        {#if hasData}
          <p class="cp-hint">
            It keeps its own data{kept
              ? ` (${size(kept.bytes)}${kept.complete ? "" : " or more"})`
              : ""}, which stays across updates.
          </p>
          {#if kept}<label class="check"
              ><input
                type="checkbox"
                checked={kept.include}
                disabled={busy}
                on:change={(event) =>
                  keepInBackups(event.currentTarget.checked)}
              />Include its data in backups</label
            >{/if}
        {/if}
        <div class="actions">
          {#if current.origin.kind === "folder"}<button
              disabled={busy || ownerBusy}
              on:click={() => look(current!.origin.directory ?? "", current)}
              >Look for an update</button
            >{/if}
          {#if currentState === "attention"}<button
              disabled={busy || ownerBusy}
              on:click={repair}>Repair</button
            >{/if}
          {#if current.components.some((part) => part.kind === "package_tree") && currentParts.some((part) => part.state === "unavailable")}<button
              disabled={busy || ownerBusy}
              on:click={() => reinstall(current!.id)}>Reinstall programs</button
            >{/if}
          {#if !included(current)}<button
              disabled={busy || ownerBusy}
              aria-expanded={removing}
              on:click={() => (removing = !removing)}>Remove…</button
            >{/if}
        </div>
        {#if !included(current) && current.origin.kind !== "folder" && !current.origin.catalogue_id}<p
            class="cp-hint"
          >
            Looking for an update works for packages installed from a folder.
          </p>{/if}
        {#if removing && !included(current)}
          <div class="confirm" role="group" aria-label="Remove this package">
            <p>
              Remove {current.identity}? Its skills, tools and program files are
              removed. Conversations and what its tools found stay readable.
            </p>
            {#if hasData}<label class="check"
                ><input type="checkbox" bind:checked={deleteData} />Also delete
                the data it kept</label
              >{/if}
            <div class="actions">
              <button disabled={busy || ownerBusy} on:click={remove}
                >Remove package</button
              >
              <button disabled={busy} on:click={() => (removing = false)}
                >Cancel</button
              >
            </div>
          </div>
        {/if}
      </section>
      {#if preview && previewAt === "detail" && target?.id === current.id}
        <PackagePreview
          {preview}
          shelfEntry={shelfChoice}
          onRefresh={refresh}
          {target}
          {collision}
          {overlaps}
          {unread}
          onOpenOverlap={openOverlap}
          onRecheck={refresh}
          {busy}
          {ownerBusy}
          onInstall={install}
          onUpdate={update}
          onDismiss={dismiss}
        />
      {/if}
    </ShelfItemLayout>
  {:else}
    <div class="cp-head">
      <div>
        <h2 class="cp-title">Shelf</h2>
        <!-- Progress shares the status line, so the list below never moves. -->
        <p class="cp-status">
          {#if busy && !adding && !quiet}<span role="status">{doing}</span
            >{:else if shelf && skills && connections && shelf.complete}{library
              .rows.length} installed{:else if busy}Reading…{:else}&nbsp;{/if}
        </p>
      </div>
      <button
        class="cp-action"
        disabled={busy ||
          ownerBusy ||
          (addKind === "skill" && !skills) ||
          (addKind === "connection" && !connections)}
        aria-haspopup="dialog"
        on:click={startAdding}
        >{addKind === "skill"
          ? "Add skill"
          : addKind === "connection"
            ? "Add connection"
            : "Install from"}</button
      >
    </div>
    {@render messages(false)}
    {#if data?.last_backup?.over_limit}
      <p class="cp-hint waiting" role="note">
        The last backup held {size(data.last_backup.data_bytes)} of package data,
        most of it from {data.last_backup.largest_identity}. Nothing was left
        out. To leave a package’s data out of backups, open that package below.
      </p>
    {/if}

    {#if data?.restored}
      <div
        class="cp-card cp-section restored"
        role="group"
        aria-label="After the restore"
      >
        <h3>Restored from a backup</h3>
        <p class="cp-hint">
          Backup of {stamp(data.restored.backup_created_at)}. A backup holds no
          program files and no keys. This is what is still to do.
        </p>
        {#if data.restored.moved_from}<p class="cp-hint">
            The backup came from another folder ({data.restored.moved_from}).
            Tools that point into that folder need to be set up again.
          </p>{/if}
        {#if !data.restored.connections_in_backup}<p class="cp-hint">
            The saved connections were not in this backup: they could not be
            read safely when it was made.
          </p>{/if}
        {#if data.restored.reinstall.length}
          <h4>Install again</h4>
          <ul class="parts">
            {#each data.restored.reinstall as item (item.installation_id)}<li>
                <strong>{packageTitle(item.installation_id, item.identity)}</strong>
                <span class="cp-hint"
                  >Download its pinned programs again. Data included in the
                  backup stays with this installation.</span
                >
                <button
                  disabled={busy || ownerBusy}
                  on:click={() => reinstall(item.installation_id)}
                  >Reinstall programs</button
                >
              </li>{/each}
          </ul>
        {/if}
        {#if data.restored.reconnect.length}
          <h4>Sign in or enter keys again</h4>
          <ul class="parts installed">
            {#each data.restored.reconnect as item (item.connection)}<li>
                <span
                  ><strong>{connectionTitle(item.connection)}</strong>
                  <span class="cp-hint"
                    >{item.fields.join(", ")}: {item.reason === "keyring"
                      ? "kept in the keyring of the computer it was saved on"
                      : "left out of the backup"}</span
                  ></span
                >
                <button
                  disabled={busy}
                  aria-label={`Open ${connectionTitle(item.connection)} in Connections`}
                  on:click={() => onOpenConnections(item.connection)}
                  >Open in Connections</button
                >
              </li>{/each}
          </ul>
        {/if}
        {#if data.restored.data_not_in_backup.length}
          <h4>Data not in this backup</h4>
          <ul class="parts">
            {#each data.restored.data_not_in_backup as item (item.installation_id)}<li
              >
                <strong>{packageTitle(item.installation_id, item.identity)}</strong>
                <span class="cp-hint"
                  >{size(item.bytes)} of its own data was left out of the backup by
                  choice.</span
                >
              </li>{/each}
          </ul>
        {/if}
        <div class="actions">
          <button disabled={busy} on:click={restoredSeen}>Done</button>
        </div>
      </div>
    {/if}

    <div class="cp-bar">
      <div class="cp-pills" role="group" aria-label="Shelf views">
        <button
          class="cp-pill"
          aria-pressed={view === "browse"}
          disabled={busy}
          on:click={() => resetList("browse")}>Browse</button
        >
        <button
          class="cp-pill"
          aria-pressed={view === "installed"}
          disabled={busy}
          on:click={() => resetList("installed")}>Installed</button
        >
      </div>
      <label class="cp-find">
        <span class="cp-icon find-icon" aria-hidden="true"
          ><Icon name="search" size={14} /></span
        >
        {#if view === "browse"}
          <input
            type="search"
            aria-label="Find a package"
            bind:value={shelfQuery}
            placeholder="Find"
          />
        {:else}
          <input
            type="search"
            aria-label="Find on your Shelf"
            bind:value={installedQuery}
            placeholder="Find"
          />
        {/if}
      </label>
      <button
        class="cp-icon"
        aria-label="Refresh"
        title="Refresh"
        disabled={busy}
        on:click={refresh}><Icon name="refresh" size={14} /></button
      >
    </div>

    {#if view === "browse"}
      {#if catalogueError}<p role="alert" class="cp-note">
          {catalogueError}
        </p>{/if}
      {#if catalogue}<PackageShelf
          entries={catalogue.entries}
          installed={shelf?.items ?? []}
          {busy}
          {ownerBusy}
          query={shelfQuery}
          bind:category={shelfCategory}
          onChoose={chooseShelf}
          onInstall={installFromCard}
          onInstalled={(item) => {
            choose(item);
            openedFromBrowse = selected === item.id;
          }}
        />{/if}
    {/if}

    {#if (shelf || packageError) && view === "installed"}
      <h3 class="cp-sr">Installed</h3>
      <div class="cp-chips" role="group" aria-label="Installed types">
        {#each filters as choice}<button
            class="cp-chip"
            aria-pressed={filter === choice}
            disabled={busy}
            on:click={() => (filter = choice)}>{choice}</button
          >{/each}
      </div>
      {#if packageError}<p class="cp-note" role="alert">{packageError}</p>{/if}
      {#if !skills}<p class="cp-note" role="alert">
          The skill library could not be read. Refresh to check again.
        </p>{/if}
      {#if !connections}<p class="cp-note" role="alert">
          Connections could not be read. Refresh to check again.
        </p>{/if}
      {#if filter === "Skills"}
        <fieldset class="shelf-controls" disabled={listBusy}>
          <Skills
            bind:adding={skillAdding}
            filter={installedQuery}
            owners={library.skillOwners}
            {ownerBusy}
            bind:busy={partBusy}
            onOpenItem={onOpenSkills}
            bind:saved={skillStatus}
          />
        </fieldset>
      {:else if filter === "Connections"}
        <fieldset class="shelf-controls" disabled={listBusy}>
          <Connections
            bind:adding={connectionAdding}
            filter={installedQuery}
            owners={library.connectionOwners}
            {ownerBusy}
            {active}
            bind:busy={partBusy}
            {onOpenFiles}
            onOpenItem={onOpenConnections}
            bind:saved={connections}
          />
        </fieldset>
      {:else if shelf}
        <div class="package-list cp-cards">
          {#each visibleRows as row (row.kind + ":" + row.id)}
            <ShelfCard
              title={row.title}
              state={row.state}
              summary={row.summary}
              icon={row.icon}
              category={row.category ?? ""}
              packageId={row.kind === "package" ? row.id : undefined}
              part={row.kind + ":" + row.id}
              disabled={busy}
              onOpen={() => {
                if (row.kind === "skill") onOpenSkills(row.id);
                else if (row.kind === "connection") onOpenConnections(row.id);
                else {
                  const item = shelf?.items.find((item) => item.id === row.id);
                  if (item) choose(item);
                }
              }}
            />
          {/each}
        </div>
        {#if !visibleRows.length && skills && connections && shelf.complete}
          <p class="cp-empty">
            {installedQuery.trim() || filter !== "All"
              ? "No matching items."
              : "Nothing is installed yet."}
          </p>
        {/if}
      {/if}
      {#if shelf && !shelf.complete}<p class="cp-hint waiting">
          More packages are installed than this list shows.
        </p>{/if}
    {/if}
  {/if}

  {#if adding}
    <PackageImport
      bind:directory
      {busy}
      {error}
      {doing}
      onPick={pick}
      onInspect={() => look(directory.trim(), null)}
      onClose={() => {
        if (!busy) adding = false;
      }}
    />
  {/if}
</section>

<style>
  .packages {
    min-width: 0;
  }
  .quiet :global(button:disabled) {
    opacity: 1;
    animation: none;
  }
  .shelf-controls {
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .waiting {
    margin: 12px 0 0;
  }
  .find-icon {
    width: 28px;
    min-height: 28px;
  }
  .detail-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px 16px;
  }
  .detail-heading h3 {
    flex: 1 1 12rem;
    min-width: 0;
    margin: 0;
    font: 500 var(--ke-text-sm) / 1.4 var(--ke-font-ui);
    overflow-wrap: anywhere;
  }
  p {
    max-width: 68ch;
    margin: 6px 0;
    overflow-wrap: anywhere;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin: 12px 0 2px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 8px 0;
    color: var(--ke-text-secondary);
  }
  .check input {
    accent-color: var(--ke-accent);
  }
  .attention {
    color: var(--ke-warning);
  }
  ul {
    display: grid;
    gap: 8px;
    max-width: 68ch;
    margin: 8px 0;
    padding: 0;
    list-style: none;
  }
  .parts li {
    display: grid;
    gap: 2px;
    overflow-wrap: anywhere;
  }
  .parts.installed li {
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 12px;
  }
  .parts.installed li > span {
    display: grid;
    gap: 2px;
  }
  .parts.installed li > .part-identity {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 8px;
  }
  .part-label {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .parts strong {
    font-weight: 500;
  }
  .steps {
    margin-top: 12px;
  }
  .steps li {
    color: var(--ke-text-secondary);
    overflow-wrap: anywhere;
  }
  .restored {
    margin-top: 16px;
  }
  .restored h3 {
    margin: 0 0 6px;
    font: 500 var(--ke-text-sm) / 1.4 var(--ke-font-ui);
  }
  h4 {
    margin: 14px 0 6px;
    color: var(--ke-text-secondary);
    font-size: var(--ke-text-xs);
    font-weight: 500;
  }
  .confirm {
    margin-top: 12px;
    padding-top: 8px;
    border-top: 1px solid var(--ke-border);
  }
</style>
