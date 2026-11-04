import { webUrl } from "$lib/api/web-links";
import { safeDocumentStyle, documentAttributeAllowed } from "./document-format";
export { documentHtmlTags } from "./document-format";
export function documentUrl(
  value: string,
  context?: { type?: string; tag?: string },
): string {
  if (context?.type === "image")
    return /^data:image\/(png|jpeg|gif|webp);base64,[a-z\d+/=]+$/i.test(value)
      ? value
      : (webUrl(value) ?? "");
  return webUrl(value) ?? "";
}
export function documentAttributes(
  attributes: Record<string, string>,
  context?: { tag?: string },
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [name, value] of Object.entries(attributes)) {
    if (
      name !== "style" &&
      documentAttributeAllowed(context?.tag ?? "span", name, value)
    )
      result[name] = value;
  }
  // Presentation is bounded to colours; never accept URLs, positioning, event
  // handlers or arbitrary CSS from a document.
  if (typeof attributes.style === "string") {
    const styles = attributes.style
      .split(";")
      .filter((part) => part.trim() && safeDocumentStyle(part));
    if (styles.length) result.style = styles.join(";");
  }
  return result;
}
