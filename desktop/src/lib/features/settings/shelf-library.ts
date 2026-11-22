import type {
  McpServer,
  ShelfCatalogueEntry,
  ShelfInstallation,
  SkillItem,
} from "$lib/api/wire.generated";
import { partTitle, readableName } from "./shelf-names";
export { partTitle, readableName } from "./shelf-names";
import {
  included,
  packageState,
  packageStateNames,
  partsOf,
  summary,
} from "./packages";

export type ShelfFilter = "All" | "Packages" | "Skills" | "Connections";
const skillIcons: Record<string, string> = {
  "web-fetch-escalation": "download",
  "topic-research": "search",
};
const connectionIcons: Record<string, string> = {
  "terminal-use": "terminal",
  "quick-terminal": "quick-terminal",
  playwright: "globe",
  pdfcraft: "pdf",
  gridcraft: "spreadsheet",
  wordcraft: "file",
  craft_files: "folder-open",
};

export type ShelfOwner = {
  id: string;
  /** The package's own name, as its parts may repeat it. */
  name: string;
  title: string;
  icon: string;
  category?: string;
  version: string;
  publisher?: string;
  license?: string;
  /** The key of its connection when it has exactly one. */
  soleConnection?: string;
};

export function packageIdentity(
  item: ShelfInstallation,
  entry?: ShelfCatalogueEntry,
): ShelfOwner {
  const connections = item.components.filter(
    (component) => component.kind === "connection",
  );
  return {
    id: item.id,
    name: item.identity,
    title: entry?.title ?? readableName(item.identity),
    icon:
      entry?.icon ?? (included(item) ? item.origin.icon : null) ?? "package",
    category: entry?.category,
    version: included(item) ? "With Kejtu" : item.version,
    publisher: entry?.publisher,
    license: entry?.license,
    soleConnection: connections.length === 1 ? connections[0].key : undefined,
  };
}

export function packageFacts(
  owner?: ShelfOwner,
  versionLabel = "Package version",
) {
  if (!owner) return [];
  return [
    { label: versionLabel, value: owner.version },
    ...(owner.publisher
      ? [{ label: "Publisher", value: owner.publisher }]
      : []),
    ...(owner.license ? [{ label: "Licence", value: owner.license }] : []),
  ];
}

export function partIcon(
  kind: "skill" | "connection",
  name: string,
  owner?: ShelfOwner,
): string {
  if (owner) return owner.icon;
  const icons = kind === "skill" ? skillIcons : connectionIcons;
  return Object.hasOwn(icons, name)
    ? icons[name]
    : kind === "skill"
      ? "book-open"
      : "route";
}

type Row = {
  kind: "package" | "skill" | "connection";
  id: string;
  title: string;
  state: string;
  summary: string;
  icon: string;
  category?: string;
  search: string;
};

export function shelfLibrary(
  packages: ShelfInstallation[],
  catalogue: ShelfCatalogueEntry[],
  skills: SkillItem[] | null,
  connections: McpServer[] | null,
) {
  const skillOwners = new Map<string, ShelfOwner[]>();
  const connectionOwners = new Map<string, ShelfOwner[]>();
  const rows: Row[] = [];
  const skillsByName = new Map(skills?.map((skill) => [skill.name, skill]));
  const connectionsById = new Map(
    connections?.map((server) => [server.id, server]),
  );
  for (const item of packages) {
    const entry = catalogue.find(
      (candidate) => candidate.id === item.origin.catalogue_id,
    );
    const owner = packageIdentity(item, entry);
    const terms = [
      owner.title,
      item.identity,
      item.origin.description ?? "",
      entry?.summary ?? "",
    ];
    const parts = partsOf(item, connections, skills);
    // A replacement with the same name is independently managed by its current owner.
    for (const part of parts) {
      terms.push(part.name, partTitle(part.kind, part.name, owner));
      if (part.kind === "skill") {
        const skill = skillsByName.get(part.name);
        if (skill) terms.push(skill.description);
        if (part.state === "changed" || !skill) continue;
      } else {
        const server = connectionsById.get(part.name);
        if (server) terms.push(server.description);
        if (part.state === "changed" || !server) continue;
      }
      const owners = part.kind === "skill" ? skillOwners : connectionOwners;
      owners.set(part.name, [...(owners.get(part.name) ?? []), owner]);
    }
    rows.push({
      kind: "package",
      ...owner,
      state: packageStateNames[packageState(item, parts)],
      summary: summary(item),
      search: terms.join(" ").toLowerCase(),
    });
  }
  for (const skill of skills ?? []) {
    if (skillOwners.has(skill.name)) continue;
    rows.push({
      kind: "skill",
      id: skill.name,
      title: partTitle("skill", skill.name),
      state: skill.error ? "Needs attention" : skill.enabled ? "On" : "Off",
      summary: "Skill",
      icon: partIcon("skill", skill.name),
      search: [partTitle("skill", skill.name), skill.name, skill.description]
        .join(" ")
        .toLowerCase(),
    });
  }
  for (const server of connections ?? []) {
    if (connectionOwners.has(server.id)) continue;
    const title = partTitle("connection", server.id);
    rows.push({
      kind: "connection",
      id: server.id,
      title,
      state: !server.available
        ? "Needs attention"
        : server.enabled
          ? "On"
          : "Off",
      summary: "Connection",
      icon: partIcon("connection", server.id),
      search: [title, server.id, server.description].join(" ").toLowerCase(),
    });
  }
  return { rows, skillOwners, connectionOwners };
}
