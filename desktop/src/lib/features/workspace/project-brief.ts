/** A starting document from saved project metadata, never an invented summary. */
export function projectBrief(
  title: string,
  folder: string | null,
  period = "",
) {
  const clean = (value: string) =>
    value.replace(/[\r\n]+/g, " ").replace(/[\\`*_{}\[\]()#+.!<>|]/g, "\\$&");
  const name =
    (title
      .normalize("NFKC")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 100) || "project") + ".md";
  const context = [
    `- Project: ${clean(title || "Untitled project")}`,
    ...(period ? [`- Period: ${clean(period)}`] : []),
    ...(folder ? [`- Resources folder: ${clean(folder)}`] : []),
  ].join("\n");
  return {
    name,
    text: `# ${clean(title || "Section brief")}

A starting point for this project. Replace the prompts below with what you know.

## Project context

${context}

## Purpose and outcome

What are we trying to accomplish? What would a useful finished result look like?

## Background and useful material

Add the context someone joining this project would need. Point to important files, links and people, and explain why they matter.

## Decisions and constraints

Record confirmed decisions, requirements, deadlines and things to avoid. Mark anything uncertain as an open question.

## Next steps

- [ ] Add the most important missing context.
- [ ] Identify the next useful piece of work.

## Develop this with Kejtu

Use the document's Ask Kejtu control: “Help me turn this starter into a useful project brief. Use the information I give you and the project material you can actually read. Ask about missing details instead of inventing them.”

Review the result, add your own knowledge and save this document in the project's Resources folder. Keep it updated when the purpose, decisions or next steps change.
`,
  };
}
