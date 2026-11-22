import { NativeFailure, request } from "$lib/api/client";
import type {
  ShelfInstallation,
  SkillItem,
  McpInput,
} from "$lib/api/wire.generated";
import { readSkillLibrary } from "./skill-library";
import { inspectConnection } from "./connections";
import { partsOf, unsettled } from "./packages";

export type SetupResult =
  | { kind: "ready" }
  | { kind: "account"; server: string; revision: number; message: string }
  | {
      kind: "inputs";
      server: string;
      revision: number;
      message: string;
      fields: McpInput[];
    };

/** One setup action, using the existing owners and their revision checks.
 * Installation alone still grants nothing at the backend boundary. This is
 * called only by Install & enable or the explicit Finish setup action.
 */
export async function setupPackage(
  selected: ShelfInstallation,
  canContinue: () => boolean = () => true,
): Promise<SetupResult> {
  const check = () => {
    if (!canContinue())
      throw new Error("Setup paused. Finish setup when you are ready.");
  };
  check();
  const shelf = await request("shelf.status", {});
  const item = shelf.items.find((entry) => entry.id === selected.id);
  if (
    !item ||
    item.state !== "installed" ||
    unsettled(item) ||
    JSON.stringify(item.components) !== JSON.stringify(selected.components)
  )
    throw new Error(
      "The package changed. Check its current state before continuing.",
    );

  const members = item.components;
  let skillRevision = 0;
  let skills: SkillItem[] = [];
  if (members.some((part) => part.kind === "skill")) {
    const library = await readSkillLibrary();
    skillRevision = library.revision;
    skills = library.items;
  }
  let connections = members.some((part) => part.kind === "connection")
    ? await request("mcp.status", {})
    : null;
  const parts = partsOf(item, connections?.servers ?? [], skills);
  if (parts.some((part) => !["on", "off"].includes(part.state)))
    throw new Error(
      "Some package parts are missing or changed. Check the package before enabling it.",
    );

  for (const part of parts.filter((part) => part.kind === "skill")) {
    if (part.state === "on") continue;
    check();
    const value = await request("skills.enable", {
      name: part.name,
      revision: part.revision,
      enabled: true,
      expected_revision: skillRevision,
    });
    skillRevision = value.revision;
  }
  for (const part of parts.filter((part) => part.kind === "connection")) {
    const row = connections!.servers.find((server) => server.id === part.name)!;
    const account = () => ({
      kind: "inputs" as const,
      server: row.id,
      revision: connections!.revision,
      fields: row.inputs!,
      message: "Enter the account details below to finish setup.",
    });
    if (row.inputs?.length && !row.inputs_ready) return account();
    // A previously enabled, limited selection belongs to its user.
    if (row.enabled && row.allowed_tools.length) continue;
    check();
    let tools;
    try {
      tools = await inspectConnection(row.id, connections!.revision);
    } catch (error) {
      if (
        row.inputs?.length &&
        error instanceof NativeFailure &&
        ["mcp_credentials", "credential_store"].includes(error.code)
      )
        return account();
      // Discovery runs no advertised actions. A failed remote discovery may
      // need sign-in; don't mislabel a generic transport failure as an auth fact.
      if (
        row.transport === "http" &&
        error instanceof NativeFailure &&
        ["mcp_sign_in_required", "mcp_request_failed"].includes(error.code)
      )
        return {
          kind: "account",
          server: row.id,
          revision: connections!.revision,
          message:
            error.code === "mcp_sign_in_required"
              ? "Sign in below to finish setting up this package."
              : "The connection could not be checked. If it needs an account, sign in below; otherwise try again when the service is available.",
        };
      throw error;
    }
    if (!tools.length)
      throw new Error(
        `${row.id} did not provide any tools. Setup is not complete.`,
      );
    check();
    const grants = tools.map((tool) => tool.name);
    connections = await request("mcp.update", {
      action: "permissions",
      server: row.id,
      expected_revision: connections!.revision,
      enabled: true,
      allowed_tools: grants,
      // Enabling tools does not authorize retrying uncertain effects.
      repeatable_tools: row.repeatable_tools.filter((name) =>
        grants.includes(name),
      ),
    });
  }
  return { kind: "ready" };
}
