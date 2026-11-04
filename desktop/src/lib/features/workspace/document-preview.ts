import { Marked, type Token } from "marked";
import { markedKatex } from "@humanspeak/svelte-markdown/extensions/katex";
import { markedMermaid } from "@humanspeak/svelte-markdown/extensions/mermaid";

const callouts = {
  extensions: [
    {
      name: "documentCallout",
      level: "block" as const,
      start(src: string) {
        return src.match(/^:::(note|tip|info|caution|danger)\s*$/m)?.index;
      },
      tokenizer(src: string) {
        const match =
          /^:::(note|tip|info|caution|danger)[ \t]*\n([\s\S]*?)\n:::[ \t]*(?:\n|$)/.exec(
            src,
          );
        if (!match) return undefined;
        const text = `**${match[1][0].toUpperCase() + match[1].slice(1)}**\n\n${match[2]}`;
        return {
          type: "blockquote",
          raw: match[0],
          text,
          tokens: new Marked().lexer(text),
        };
      },
    },
  ],
};
export const documentExtensions = [markedKatex(), markedMermaid(), callouts];
const parser = new Marked(...documentExtensions);
export type PreviewBlock = {
  token: Token;
  renderSource: string;
  from: number;
  to: number;
  label: string;
};
export function previewBlocks(source: string): PreviewBlock[] {
  // CodeMirror positions count every line separator as one character. The
  // original separator is still retained by the editor and the save owner.
  const normalized = source.replace(/\r\n?/g, "\n");
  // Frontmatter is document metadata, not a horizontal rule and body paragraph.
  const frontmatter =
    normalized.match(/^---[ \t]*\n[\s\S]*?\n---[ \t]*(?:\n|$)/)?.[0] ?? "";
  const tokens = parser.lexer(normalized.slice(frontmatter.length));
  // The renderer preprocesses HTML only for string input. Retain reference
  // definitions when rendering individual blocks, without changing edit spans.
  const definitions = tokens
    .filter((t) => t.type === "def")
    .map((t) => t.raw)
    .join("\n");
  let offset = frontmatter.length;
  let mapped = true;
  const blocks = tokens.map((token) => {
    const from = offset;
    mapped &&= normalized.slice(from, from + token.raw.length) === token.raw;
    offset += token.raw.length;
    return {
      token,
      renderSource: token.raw + (definitions ? "\n\n" + definitions : ""),
      from,
      to: offset - (token.raw.match(/\n+$/)?.[0].length ?? 0),
      label:
        (
          {
            paragraph: "paragraph",
            heading: "heading",
            list: "list",
            blockquote: "quotation",
            table: "table",
            code: "code block",
            mermaid: "diagram",
            blockKatex: "equation",
            hr: "divider",
          } as Record<string, string>
        )[token.type] ?? "block",
    };
  });
  // Never infer positions from a text search: repeated passages and syntax
  // transformations can otherwise point an edit at the wrong paragraph.
  if (offset !== normalized.length) mapped = false;
  return blocks
    .filter(({ token }) => !["space", "def"].includes(token.type))
    .map((block) => (mapped ? block : { ...block, from: -1, to: -1 }));
}
