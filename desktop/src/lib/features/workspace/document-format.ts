/** Shared rich-editor/preview policy. Unsupported markup stays in source. */
export const documentHtmlTags = [
  "span",
  "img",
  "u",
  "sub",
  "sup",
  "mark",
  "br",
  "div",
  "p",
  "section",
  "details",
  "summary",
  "figure",
  "figcaption",
  "kbd",
  "small",
  "strong",
  "em",
  "b",
  "i",
  "s",
  "del",
];
export function safeDocumentStyle(style: unknown): style is string {
  if (typeof style !== "string") return false;
  return style
    .split(";")
    .filter((s) => s.trim())
    .every((part) => {
      const colon = part.indexOf(":"),
        name = part.slice(0, colon).trim().toLowerCase(),
        value = part.slice(colon + 1).trim();
      return (
        ["color", "background-color"].includes(name) &&
        /^(#[0-9a-f]{3,8}|[a-z]+|rgba?\([\d.,%\s]+\)|hsla?\([\d.,%\s]+\))$/i.test(
          value,
        )
      );
    });
}

export function documentImageUrl(value: string): boolean {
  if (/^data:image\/(png|jpeg|gif|webp);base64,[a-z\d+/=]+$/i.test(value))
    return true;
  if (!/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f\\<>"']/.test(value))
    return false;
  try {
    const u = new URL(value);
    return !!u.hostname && !u.username && !u.password;
  } catch {
    return false;
  }
}
export function documentAttributeAllowed(
  tag: string,
  name: string,
  value: unknown,
): boolean {
  if (typeof value !== "string") return false;
  if (name === "title") return true;
  if (name === "style") return safeDocumentStyle(value);
  if (tag !== "img") return false;
  if (name === "src") return documentImageUrl(value);
  if (name === "alt") return true;
  return ["width", "height"].includes(name) && /^\d{1,5}$/.test(value);
}
