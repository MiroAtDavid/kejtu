import type {
  McpServer,
  ShelfComponentRequest,
  ShelfDescription,
  ShelfInstallation,
  ShelfStep,
  SkillItem,
} from "$lib/api/wire.generated";
import { partTitle } from "./shelf-names";

/**
 * Words for the Packages panel. Everything here is read from what the owners
 * report: the Shelf record, the skill library and the connection settings. A
 * package has no on/off state of its own; it is on when its parts are.
 */

export const formatNames: Record<ShelfDescription["format"], string> = {
  "agent-plugins": "Agent Plugins package",
  "claude-plugin": "Claude plugin",
  "codex-plugin": "Codex plugin",
  mcpb: "MCPB bundle",
  skill: "Skill folder",
};

export type PackageOverlap = {
  kind: "package" | "connection" | "skill";
  key: string;
  name: string;
  message: string;
  blocking: boolean;
};

/** Compare known identities and public service hosts only. This is a preview
 * warning, never permission to share, replace or adopt another installation. */
export function overlapsFor(
  preview: ShelfDescription,
  installations: ShelfInstallation[],
  servers: McpServer[] | null,
  skills: SkillItem[] | null,
  target: ShelfInstallation | null,
): PackageOverlap[] {
  const overlaps = new Map<string, PackageOverlap>();
  const add = (value: PackageOverlap) => {
    const key = value.kind + ":" + value.key;
    if (!overlaps.has(key) || value.blocking) overlaps.set(key, value);
  };
  const others = installations.filter((item) => item.id !== target?.id);
  const named = others.find((item) => item.identity === preview.name);
  if (named)
    add({
      kind: "package",
      key: named.id,
      name: named.identity,
      message:
        "This package name is already installed. Open the existing package to choose an update, or remove it before installing a different package with this name.",
      blocking: true,
    });
  const address = (value: string) => {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" && url.protocol !== "http:") return null;
      return url.hostname.toLowerCase();
    } catch {
      // mcp.status deliberately exposes only the hostname, not paths or query
      // values. A shared host suggests overlap; it cannot establish identity.
      return value.toLowerCase();
    }
  };
  for (const part of preview.components) {
    const kind = part.kind === "skill" ? "skill" : "connection";
    const owner = others.find((item) =>
      item.components.some((c) => c.kind === kind && c.key === part.name),
    );
    if (owner) {
      if (owner.id !== named?.id)
        add({
          kind: "package",
          key: owner.id,
          name: owner.identity,
          message:
            "This package already provides a part with the same name. Remove the existing package first; switching it off does not free that name.",
          blocking: true,
        });
    } else if (kind === "skill") {
      const ours = target?.components.some(
        (c) => c.kind === "skill" && c.key === part.name,
      );
      if (!ours && skills?.some((row) => row.name === part.name))
        add({
          kind: "skill",
          key: part.name,
          name: part.name,
          message:
            "A skill with this name is already installed. Remove it in Skills first; switching it off does not free that name.",
          blocking: true,
        });
    } else {
      const row = servers?.find((server) => server.id === part.name);
      if (row && !(target && row.installation_id === target.id))
        add({
          kind: "connection",
          key: row.id,
          name: row.id,
          message: row.installation_id
            ? "This connection name belongs to another package. Remove its package first; switching it off does not free that name."
            : "A connection with this name already exists. Remove it in Connections first; switching it off does not free that name. Its login will not transfer to this package.",
          blocking: true,
        });
    }
    if (part.kind !== "connection") continue;
    const wanted = address(part.url);
    if (!wanted) continue;
    for (const row of servers ?? []) {
      if (
        row.id === part.name ||
        row.transport !== "http" ||
        (target && row.installation_id === target.id) ||
        address(row.address) !== wanted
      )
        continue;
      add({
        kind: "connection",
        key: row.id,
        name: row.id,
        message: row.enabled
          ? "Another connection to this service is on and may provide similar tools. Review it before enabling this package; switch it off or remove it if you are replacing it. The two integrations stay separate."
          : "Another connection to this service is already off. It will stay separate; its settings and login will not be reused.",
        blocking: false,
      });
    }
  }
  return [...overlaps.values()];
}

/** The host of an address, which is what a person can judge. */
export function host(address: string): string {
  try {
    return new URL(address).host || address;
  } catch {
    return address;
  }
}

/** A backup's or a restore's time, as it is written in their records: `20261111T184114.555034Z`. */
export function stamp(value: string): string {
  const found = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)/.exec(value);
  return found
    ? `${found[1]}-${found[2]}-${found[3]} ${found[4]}:${found[5]} UTC`
    : value;
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3)
    return `${(bytes / 1024 ** 2).toFixed(bytes < 10 * 1024 ** 2 ? 1 : 0)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** What Kejtu does with one part of a package: enforced facts, not a publisher's claims. */
export function partFacts(part: ShelfComponentRequest): string {
  if (part.kind === "skill")
    return "Skill. Instructions and files only; it grants no access.";
  if (part.kind === "connection")
    return `Online tool at ${host(part.url)}. It runs on its provider’s server and follows the Web switch.`;
  const extra =
    part.artifact.dependencies === "npm"
      ? " Its npm packages come from the public registry, exactly as its lock file lists them."
      : part.artifact.dependencies === "wheels"
        ? " Its Python packages come from the Python package index as exact pinned files."
        : "";
  return (
    `Local tool. Its program is downloaded from ${host(part.artifact.url)} (${size(part.artifact.bytes)}) ` +
    `and checked against its pinned hash.${extra} ${part.network ? "It uses the network" : "It starts without network"} and reads only its own program folder.${part.inputs?.length ? " Enter account details during setup; they stay in this computer’s keyring." : ""}`
  );
}

export type PartState =
  "on" | "off" | "missing" | "changed" | "unavailable" | "unknown";
export type Part = {
  kind: "skill" | "connection";
  name: string;
  state: PartState;
  revision: string;
};

/**
 * The skills and tools of an installed package, each with the state its owner
 * reports. `null` means that owner could not be read: the state is unknown, never
 * guessed.
 */
export function partsOf(
  item: ShelfInstallation,
  servers: McpServer[] | null,
  skills: SkillItem[] | null,
): Part[] {
  const found: Part[] = [];
  for (const component of item.components) {
    if (component.kind === "connection") {
      const row = servers?.find((server) => server.id === component.key);
      found.push({
        kind: "connection",
        name: component.key,
        revision: "",
        state: !servers
          ? "unknown"
          : !row
            ? "missing"
            : row.installation_id !== item.id
              ? "changed"
              : !row.available
                ? "unavailable"
                : row.enabled
                  ? "on"
                  : "off",
      });
    } else if (component.kind === "skill") {
      const row = skills?.find((skill) => skill.name === component.key);
      const pinned = component.detail.revision ?? "";
      found.push({
        kind: "skill",
        name: component.key,
        revision: pinned,
        state: !skills
          ? "unknown"
          : !row
            ? "missing"
            : row.revision !== pinned
              ? "changed"
              : row.enabled
                ? "on"
                : "off",
      });
    }
  }
  return found;
}

export const partStateNames: Record<PartState, string> = {
  on: "On",
  off: "Off",
  missing: "Missing: it was removed outside this package",
  changed: "Replaced outside this package",
  unavailable: "Needs attention: its program is missing",
  unknown: "State unknown",
};

export type PackageState = "attention" | "working" | "on" | "partly" | "off";

export function packageState(
  item: ShelfInstallation,
  parts: Part[],
): PackageState {
  if (item.state === "installing" || item.state === "removing")
    return "working";
  if (
    item.state === "interrupted" ||
    item.unresolved_total > 0 ||
    parts.some((part) =>
      ["missing", "unavailable", "unknown", "changed"].includes(part.state),
    )
  )
    return "attention";
  const on = parts.filter((part) => part.state === "on").length;
  return on === 0 ? "off" : on === parts.length ? "on" : "partly";
}

export const packageStateNames: Record<PackageState, string> = {
  attention: "Needs attention",
  working: "Working",
  on: "On",
  partly: "Partly on",
  off: "Off",
};

/** Whether the record itself, not just a part, asks for a repair. */
export function unsettled(item: ShelfInstallation): boolean {
  return item.state === "interrupted" || item.unresolved_total > 0;
}

/** What happened and what can be done, for an installation whose record is unsettled. */
export function attention(item: ShelfInstallation): string {
  const kind = item.last_operation?.kind;
  if (kind === "install")
    return "Its install did not finish. Repair checks what is there. To start over, remove it and install it again.";
  if (kind === "update")
    return "Its update stopped between two versions. Look for the update again and apply it, or remove the package.";
  if (kind === "remove")
    return "Its removal did not finish. Repair checks what is left; then remove it again.";
  return "Something about it is unsettled. Repair checks what is there.";
}

export function summary(item: ShelfInstallation): string {
  const count = (kind: string) =>
    item.components.filter((component) => component.kind === kind).length;
  const plural = (n: number, word: string) =>
    `${n} ${word}${n === 1 ? "" : "s"}`;
  const skills = count("skill");
  const tools = count("connection");
  return [
    included(item) ? "Included with Kejtu" : item.version,
    ...(skills ? [plural(skills, "skill")] : []),
    ...(tools ? [plural(tools, "tool")] : []),
  ].join(" · ");
}

/** A package that ships with Kejtu, by the origin its record carries: its
 *  label and actions follow that origin, not a list of names kept here. */
export function included(item: ShelfInstallation): boolean {
  return item.origin.kind === "builtin";
}

export function origin(item: ShelfInstallation): string {
  if (included(item)) return "Kejtu";
  return item.origin.kind === "folder"
    ? (item.origin.directory ?? "a folder")
    : `${item.origin.repository ?? "a repository"} at ${(item.origin.commit ?? "").slice(0, 12)}`;
}

const reasons: Record<string, string> = {
  credential_cleanup:
    "the change was saved, but unused keys could not be deleted; unlock the system keyring and remove the unused Kejtu connection items there",
  shelf_download: "the download could not be fetched or is not the pinned file",
  shelf_tree: "the downloaded program is not the pinned one",
  shelf_tree_differs: "the program on disk is not the pinned one",
  shelf_program_missing: "its program file is not in the download",
  shelf_dependency: "its dependencies could not be prepared",
  shelf_web: "Web access is off",
  shelf_time: "it took longer than its time allowance",
  skill_import: "the skill could not be read",
  skill_name: "the skill declares another name",
  skill_web: "Web access is off",
  mcp_configuration: "the connection settings refused it",
  not_attempted: "not attempted, because an earlier step failed",
  already_absent: "it was already gone",
  shared: "another package still uses it",
  used_by_connection: "a connection still uses it",
  kept_by_choice: "kept, as you chose",
};

const stepKinds: Record<ShelfStep["component_kind"], string> = {
  skill: "Skill",
  connection: "Tool",
  package_tree: "Program files",
  plugin_data: "Its own data",
  cache: "Its cache",
  secret: "Key",
};

const done: Record<ShelfStep["action"], string> = {
  download: "downloaded",
  materialize: "downloaded and verified",
  register: "added",
  enable: "switched on",
  disable: "switched off",
  unregister: "removed",
  delete: "deleted",
  keep: "kept",
};

/** One recorded step in plain words. Unknown codes are shown as they are. */
export function stepLine(step: ShelfStep): string {
  const named =
    step.component_kind === "skill" || step.component_kind === "connection"
      ? `${stepKinds[step.component_kind]} ${partTitle(step.component_kind, step.component_key)}`
      : stepKinds[step.component_kind];
  const reason = step.code
    ? (reasons[step.code] ?? step.code.replaceAll("_", " "))
    : "";
  if (step.outcome === "applied")
    return `${named}: ${done[step.action]}${reason ? ` (${reason})` : ""}.`;
  if (step.outcome === "skipped")
    return `${named}: ${reason || "nothing to do"}.`;
  if (step.outcome === "failed")
    return `${named}: failed${reason ? `: ${reason}` : ""}.`;
  return `${named}: result unknown.`;
}
