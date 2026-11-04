import type { DocumentPatch } from "./document-patches";

type Node = {
  complete: boolean;
  value: string | Record<string, Node> | Node[];
};
/** Strict partial JSON for presentation only. Complete saved responses still
 * pass the response/identity reader and atomic patch validation before applying. */
export function workProgress(
  text: string,
  id: string,
):
  | { action: "reply"; text: string }
  | { action: "edit"; edits: DocumentPatch[] }
  | null {
  if (!id || text.length > 4 * 1024 * 1024) return null;
  let at = 0;
  const space = () => {
    while (at < text.length && /\s/.test(text[at])) at++;
  };
  function parse(depth = 0): Node {
    if (depth > 4) throw Error();
    space();
    const first = text[at++];
    if (first === '"') {
      let value = "";
      while (at < text.length) {
        const c = text[at++];
        if (c === '"') return { value, complete: true };
        if (c.charCodeAt(0) < 32) throw Error();
        if (c !== "\\") {
          value += c;
          continue;
        }
        if (at === text.length) break;
        const esc = text[at++];
        if (esc === "u") {
          const hex = text.slice(at, at + 4);
          if (!/^[a-fA-F0-9]*$/.test(hex)) throw Error();
          if (hex.length < 4) break;
          value += String.fromCharCode(parseInt(hex, 16));
          at += 4;
        } else {
          const escapes: Record<string, string> = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            b: "\b",
            f: "\f",
            n: "\n",
            r: "\r",
            t: "\t",
          };
          if (!(esc in escapes)) throw Error();
          value += escapes[esc];
        }
      }
      if (/[\uD800-\uDBFF]$/.test(value)) value = value.slice(0, -1);
      return { value, complete: false };
    }
    if (first !== "{" && first !== "[") throw Error();
    const array = first === "[",
      end = array ? "]" : "}";
    const value: Record<string, Node> | Node[] = array
      ? []
      : Object.create(null);
    let afterComma = false;
    for (;;) {
      space();
      if (at === text.length) return { value, complete: false };
      if (text[at] === end) {
        if (afterComma) throw Error();
        at++;
        return { value, complete: true };
      }
      let key = "";
      if (!array) {
        const node = parse(depth + 1);
        if (typeof node.value !== "string") throw Error();
        if (!node.complete) return { value, complete: false };
        key = node.value;
        if (key in value) throw Error();
        space();
        if (at === text.length) return { value, complete: false };
        if (text[at++] !== ":") throw Error();
        space();
        if (at === text.length) return { value, complete: false };
      }
      const node = parse(depth + 1);
      if (array) (value as Node[]).push(node);
      else (value as Record<string, Node>)[key] = node;
      if (!node.complete) return { value, complete: false };
      space();
      if (at === text.length) return { value, complete: false };
      if (text[at] === end) {
        at++;
        return { value, complete: true };
      }
      if (text[at++] !== ",") throw Error();
      afterComma = true;
    }
  }
  try {
    const root = parse();
    space();
    if (
      at !== text.length ||
      typeof root.value === "string" ||
      Array.isArray(root.value)
    )
      return null;
    const f = root.value;
    if (
      Object.keys(f).some(
        (k) => !["request_id", "action", "edits", "explanation"].includes(k),
      )
    )
      return null;
    if (
      !f.request_id?.complete ||
      f.request_id.value !== id ||
      !f.action?.complete
    )
      return null;
    if (f.action.value === "reply") {
      return !f.edits &&
        typeof f.explanation?.value === "string" &&
        !f.explanation.value.includes("\0")
        ? { action: "reply", text: f.explanation.value }
        : null;
    }
    if (f.action.value !== "edit" || !Array.isArray(f.edits?.value))
      return null;
    const edits: DocumentPatch[] = [];
    for (const n of f.edits.value) {
      const e = n.value;
      if (
        typeof e === "string" ||
        Array.isArray(e) ||
        Object.keys(e).some((k) => !["old_text", "new_text"].includes(k))
      )
        return null;
      if (
        !e.old_text?.complete ||
        typeof e.old_text.value !== "string" ||
        typeof e.new_text?.value !== "string"
      ) {
        if (n.complete) return null;
        break;
      }
      if (e.old_text.value.includes("\0") || e.new_text.value.includes("\0"))
        return null;
      edits.push({ old_text: e.old_text.value, new_text: e.new_text.value });
    }
    return edits.length ? { action: "edit", edits } : null;
  } catch {
    return null;
  }
}
