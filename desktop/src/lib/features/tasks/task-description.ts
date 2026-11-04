import { marked, Renderer } from "marked";
import DOMPurify from "dompurify";
import { webUrl } from "$lib/api/web-links";

export const DESCRIPTION_BYTES = 32768;

/** Render only task prose. Raw HTML and remote images never become active content. */
export function taskDescription(source: string): {
  html: string;
  checks: number[];
} {
  const tokens = marked.lexer(source);
  const checks: number[] = [];
  let cursor = 0;
  marked.walkTokens(tokens, (token) => {
    if (token.type !== "list_item" || typeof token.checked !== "boolean")
      return;
    const start = source.indexOf(token.raw, cursor);
    const marker = /^(\s*(?:[-+*]|\d+[.)])\s+\[)[ xX]\]/.exec(token.raw);
    checks.push(start >= 0 && marker ? start + marker[1].length : -1);
    // Move past the marker, not the whole parent item, so nested items keep their identity.
    if (start >= 0) cursor = start + (marker?.[0].length ?? 1);
  });
  let ordinal = 0;
  const renderer = new Renderer();
  renderer.html = ({ text }) =>
    text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  renderer.image = ({ text }) =>
    text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  renderer.checkbox = ({ checked }) => {
    const index = ordinal++;
    return `<input type="checkbox" data-task-check="${index}" aria-label="Checklist item ${index + 1}" ${checked ? "checked" : ""} ${checks[index] < 0 ? "disabled" : ""}>`;
  };
  renderer.link = function ({ href, tokens }) {
    const text = this.parser.parseInline(tokens);
    const safe = webUrl(href);
    return safe
      ? `<a href="${safe.replace(/&/g, "&amp;").replace(/"/g, "&quot;")}">${text}</a>`
      : text;
  };
  return {
    html: DOMPurify.sanitize(marked.parser(tokens, { renderer }) as string, {
      ALLOWED_TAGS: [
        "p",
        "br",
        "strong",
        "em",
        "del",
        "h1",
        "h2",
        "h3",
        "h4",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "a",
        "input",
        "hr",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
      ],
      ALLOWED_ATTR: [
        "href",
        "type",
        "checked",
        "disabled",
        "data-task-check",
        "aria-label",
        "start",
      ],
    }),
    checks,
  };
}

export function toggleTaskCheck(
  source: string,
  position: number,
  checked: boolean,
): string {
  if (
    position < 1 ||
    source[position - 1] !== "[" ||
    source[position + 1] !== "]" ||
    !/[ xX]/.test(source[position])
  )
    return source;
  return (
    source.slice(0, position) +
    (checked ? "x" : " ") +
    source.slice(position + 1)
  );
}
