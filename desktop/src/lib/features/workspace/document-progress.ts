/** Decode only the string fields of a partial document response. This is a
 * presentation parser, never edit authority: the complete saved response still
 * passes readDocumentReply and the exact target check before anything applies. */
export function documentProgress(
  text: string,
  requestId: string,
): { action: "edit" | "reply"; text: string } | null {
  if (!requestId || text.length > 4 * 1024 * 1024) return null;
  let at = 0;
  const fields: Record<string, string> = {};
  const keys = new Set<string>();
  const allowed = new Set([
    "request_id",
    "action",
    "replacement",
    "explanation",
  ]);
  function space() {
    while (/\s/.test(text[at] ?? "") && at < text.length) at++;
  }
  function string(): { value: string; complete: boolean } | null {
    if (text[at++] !== '"') return null;
    let value = "";
    while (at < text.length) {
      const c = text[at++];
      if (c === '"') return { value, complete: true };
      if (c.charCodeAt(0) < 32) return null;
      if (c !== "\\") {
        value += c;
        continue;
      }
      if (at === text.length) break;
      const escape = text[at++];
      if (escape === "u") {
        const hex = text.slice(at, at + 4);
        if (!/^[a-fA-F0-9]*$/.test(hex)) return null;
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
        if (!(escape in escapes)) return null;
        value += escapes[escape];
      }
    }
    // A streamed Unicode escape may stop between the two surrogate halves.
    if (/[\uD800-\uDBFF]$/.test(value)) value = value.slice(0, -1);
    return { value, complete: false };
  }
  space();
  if (text[at++] !== "{") return null;
  for (;;) {
    space();
    if (at === text.length) break;
    if (text[at] === "}") {
      at++;
      space();
      if (at !== text.length) return null;
      break;
    }
    const key = string();
    if (!key) return null;
    if (!key.complete) break;
    if (!allowed.has(key.value) || keys.has(key.value)) return null;
    keys.add(key.value);
    space();
    if (at === text.length) break;
    if (text[at++] !== ":") return null;
    space();
    if (at === text.length) break;
    const value = string();
    if (!value || value.value.includes("\0")) return null;
    if (value.complete || ["replacement", "explanation"].includes(key.value))
      fields[key.value] = value.value;
    if (!value.complete) break;
    space();
    if (at === text.length) break;
    if (text[at] === "}") {
      at++;
      space();
      if (at !== text.length) return null;
      break;
    }
    if (text[at++] !== ",") return null;
    space();
    if (text[at] === "}") return null;
  }
  if (fields.request_id !== requestId) return null;
  if (fields.action === "edit" && "replacement" in fields)
    return { action: "edit", text: fields.replacement };
  if (
    fields.action === "reply" &&
    !keys.has("replacement") &&
    "explanation" in fields
  )
    return { action: "reply", text: fields.explanation };
  return null;
}
