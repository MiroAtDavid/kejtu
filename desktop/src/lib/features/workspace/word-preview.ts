import DOMPurify from "dompurify";
import { parseAsync, renderDocument } from "docx-preview";

// The preview frame has its own resource policy and cannot run scripts.
// Admit formatted text, tables and embedded images to its document.
export async function wordPreview(bytes: ArrayBuffer) {
  const options = {
    ignoreWidth: true,
    ignoreHeight: true,
    ignoreFonts: true,
    inWrapper: false,
    useBase64URL: true,
    renderAltChunks: false,
  };
  const parsed = await parseAsync(bytes, options);
  const nodes = await renderDocument(parsed, options);
  const body = document.createDocumentFragment();
  const styles: string[] = [];
  for (const node of nodes) {
    if (node.nodeName === "STYLE") {
      styles.push(node.textContent ?? "");
    } else if (node instanceof Element) {
      body.append(
        DOMPurify.sanitize(node, {
          RETURN_DOM_FRAGMENT: true,
          ALLOWED_TAGS: [
            "section",
            "article",
            "header",
            "footer",
            "div",
            "span",
            "p",
            "br",
            "a",
            "table",
            "colgroup",
            "col",
            "tbody",
            "thead",
            "tfoot",
            "tr",
            "td",
            "th",
            "ul",
            "ol",
            "li",
            "sup",
            "sub",
            "strong",
            "em",
            "b",
            "i",
            "u",
            "s",
            "img",
          ],
          ALLOWED_ATTR: [
            "class",
            "style",
            "src",
            "alt",
            "width",
            "height",
            "colspan",
            "rowspan",
            "start",
            "dir",
            "lang",
          ],
          ALLOW_DATA_ATTR: false,
        }),
      );
    }
  }
  for (const image of body.querySelectorAll("img")) {
    if (
      !/^data:image\/(?:png|jpeg|gif|webp|bmp);base64,/i.test(
        image.getAttribute("src") ?? "",
      )
    )
      image.removeAttribute("src");
  }
  return { body, styles: styles.join("\n") };
}
