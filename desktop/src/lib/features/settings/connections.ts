import { request } from "$lib/api/client";
import type { McpServer, McpStatus, McpTool } from "$lib/api/wire.generated";

export const includedConnections = [
  "playwright",
  "gridcraft",
  "pdfcraft",
  "wordcraft",
] as const;

export const bundledTerminals = ["terminal-use", "quick-terminal"] as const;

export type IncludedTool =
  (typeof includedConnections)[number] | (typeof bundledTerminals)[number];

// Used only after mcp.setup creates a verified installed document preset.
// These read-only operations need to observe edits made later in the same
// message. Never infer repeatability from an imported tool's name/description.
const presetReads: Record<string, readonly string[]> = {
  gridcraft: [
    "list_commands",
    "list_functions",
    "inspect_workbook",
    "read_range",
    "get_cell",
  ],
  pdfcraft: [
    "doc_list",
    "doc_info",
    "text_extract",
    "text_find",
    "page_render",
    "object_list",
    "bookmark_list",
    "form_fields",
    "link_list",
    "comment_list",
    "text_lines",
    "page_images",
    "doc_inspect",
    "render_preview",
  ],
  wordcraft: [
    "list_commands",
    "get_text",
    "inspect_document",
    "render_page",
    "parity",
    "ui_inspect",
  ],
};

export function presetRepeatable(name: string, tools: McpTool[]): string[] {
  const names = new Set(tools.map((tool) => tool.name));
  return (presetReads[name] ?? []).filter((tool) => names.has(tool));
}

const knownNames: Record<string, string> = {
  playwright: "Browser",
  gridcraft: "Spreadsheets",
  pdfcraft: "PDFs",
  wordcraft: "Documents",
  craft_files: "Document files",
  "terminal-use": "Terminal Use",
  "quick-terminal": "Quick Terminal",
};

/** The fixed name of a tool that comes with Kejtu, if this is one. */
export function knownConnectionName(id: string): string | undefined {
  return Object.hasOwn(knownNames, id) ? knownNames[id] : undefined;
}

export function connectionName(row: Pick<McpServer, "id">): string {
  return knownConnectionName(row.id) ?? row.id;
}

/**
 * Sets up the named tools that come with Kejtu and turns each on with its
 * complete current tool set, one at a time. Shelf → Connections and the welcome
 * window both use this. A name already configured is left as it is. A setup whose
 * answer is lost is read back once; nothing is retried, and after an unconfirmed
 * permission save nothing more is changed. `status` receives every saved state,
 * or null when the saved state is unknown.
 */
export async function setUpIncludedTools(options: {
  names: readonly IncludedTool[];
  saved: McpStatus;
  /** New document presets use the folders already enabled in Files. */
  sharedFolders: boolean;
  status: (value: McpStatus | null) => void;
  checked?: (name: string, ok: boolean) => void;
}): Promise<{ name: string; message: string }[]> {
  const failures: { name: string; message: string }[] = [];
  let saved: McpStatus | null = options.saved;
  const terminal = (name: string) =>
    (bundledTerminals as readonly string[]).includes(name);
  for (const name of [...options.names]) {
    if (!saved) break;
    // A conflict read-back can reveal a newly imported connection under a
    // later preset's name. It is no longer ours to set up or grant tools.
    if (saved.servers.some((row) => row.id === name)) continue;
    try {
      saved = await request("mcp.setup", {
        server: name,
        expected_revision: saved.revision,
        use_shared_folders: !terminal(name) && options.sharedFolders,
      });
    } catch (caught) {
      // Setup may have committed before a lost reply. Read back once;
      // never retry a mutation or continue with an unknown revision.
      saved = await request("mcp.status", {});
      options.status(saved);
      failures.push({
        name,
        message: caught instanceof Error ? caught.message : "Setup failed.",
      });
      continue;
    }
    options.status(saved);
    const row = saved.servers.find((item) => item.id === name)!;
    // Browser's fixed safe grant is owned by its existing setup endpoint.
    // New document presets are inspected once, then receive exact names.
    let catalogue: McpTool[];
    try {
      catalogue = await inspectConnection(row.id, saved.revision);
      options.checked?.(row.id, true);
    } catch (caught) {
      options.checked?.(row.id, false);
      failures.push({
        name,
        message:
          caught instanceof Error ? caught.message : "Connection check failed.",
      });
      continue;
    }
    if (row.enabled) continue;
    try {
      saved = await request("mcp.update", {
        action: "permissions",
        server: name,
        expected_revision: saved.revision,
        enabled: true,
        allowed_tools: catalogue.map((tool) => tool.name),
        repeatable_tools: presetRepeatable(name, catalogue),
      });
      options.status(saved);
    } catch (caught) {
      saved = null;
      options.status(null);
      failures.push({
        name,
        message:
          (caught instanceof Error
            ? caught.message
            : "Save was not confirmed.") +
          " Refresh to check the saved state before trying again.",
      });
    }
  }
  return failures;
}

/** Collect one revision-bound discovery, without calling any advertised tool. */
export async function inspectConnection(
  server: string,
  revision: number,
): Promise<McpTool[]> {
  const tools: McpTool[] = [];
  let offset = 0;
  let total: number | null = null;
  do {
    const page = await request("mcp.inspect", { server, offset });
    if (
      page.server !== server ||
      page.revision !== revision ||
      (total !== null && total !== page.total_tools)
    )
      throw new Error("Connections changed. Refresh before checking again.");
    total = page.total_tools;
    tools.push(...page.tools);
    if (
      tools.length > 4096 ||
      tools.length > total ||
      new Set(tools.map((tool) => tool.name)).size !== tools.length
    )
      throw new Error(
        "The connection returned an inconsistent tool list. No permissions were changed.",
      );
    if (page.next_offset === null) {
      if (tools.length !== total)
        throw new Error(
          "The connection returned an incomplete tool list. No permissions were changed.",
        );
      return tools;
    }
    if (page.next_offset !== tools.length || page.next_offset <= offset)
      throw new Error(
        "The connection returned an invalid next page. No permissions were changed.",
      );
    offset = page.next_offset;
  } while (true);
}
