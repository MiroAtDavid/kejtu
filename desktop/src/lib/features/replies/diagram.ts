import { diagramTheme } from "./palette";

// Mermaid has global configuration. Serialize rendering across replies/themes.
let queue: Promise<unknown> = Promise.resolve();
let serial = 0;
export function renderDiagram(text: string, theme: string): Promise<string> {
  const task = queue.then(async () => {
    // Mermaid lays out in temporary DOM before SVG sanitization. Reject authored
    // styling, resources and configuration before that stage, not only after it.
    if (
      text.length > 12000 ||
      /%%\s*\{|^---\s*$/m.test(text) ||
      /(?:^|[;\n])\s*(?:style|classDef|linkStyle|click)\b/i.test(text) ||
      /url\s*\(|@import|\b(?:img|icon)\s*:|::icon\s*\(/i.test(text) ||
      // Mermaid's Venn text nodes require foreignObject even with HTML labels
      // disabled. Refuse them instead of silently losing text to sanitization.
      (/(?:^|\n)\s*venn-beta\b/i.test(text) && /(?:^|\n)\s*text\s+/i.test(text))
    )
      throw new Error("Unsupported diagram configuration");
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      look: "classic",
      // Mind maps use the bundled radial layout; the global default is Dagre.
      layout: /^\s*(?:%%[^\n]*\n\s*)*mindmap\b/.test(text)
        ? "cose-bilkent"
        : "dagre",
      themeVariables: diagramTheme(theme === "dark"),
      // Venn's built-in CSS overrides its scaled title attribute and clips it.
      themeCSS: ".venn-title { font-size: 18px; }",
      maxTextSize: 12000,
      maxEdges: 150,
      suppressErrorRendering: true,
      htmlLabels: false,
      flowchart: { htmlLabels: false },
      mindmap: { useMaxWidth: false },
      treeView: { useMaxWidth: false },
      // The bundled treemap renderer accepts this config, but Mermaid 11.14's
      // top-level MermaidConfig declaration omits the treemap property.
      ...{ treemap: { useMaxWidth: false, nodeWidth: 70, nodeHeight: 40 } },
      // Keep timeline labels at their authored size. The chat wrapper scrolls
      // narrow windows instead of scaling an entire desktop-width chart down.
      gantt: {
        useWidth: 700,
        useMaxWidth: false,
        fontSize: 14,
        sectionFontSize: 14,
        barHeight: 28,
        barGap: 8,
      },
    });
    return (await mermaid.render("kejtu-diagram-" + ++serial, text)).svg;
  });
  queue = task.catch(() => undefined);
  return task;
}
