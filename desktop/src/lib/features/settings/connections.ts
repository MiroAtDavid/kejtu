import { request } from "$lib/api/client";
import type { McpServer, McpTool } from "$lib/api/wire.generated";

export const includedConnections = [
  "playwright",
  "gridcraft",
  "pdfcraft",
  "wordcraft",
] as const;

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

export function connectionName(row: Pick<McpServer, "id">): string {
  return (
    (
      {
        playwright: "Browser",
        gridcraft: "Spreadsheets",
        pdfcraft: "PDFs",
        wordcraft: "Documents",
        craft_files: "Document files",
      } as Record<string, string>
    )[row.id] ?? row.id
  );
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
