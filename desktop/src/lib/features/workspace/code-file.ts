import { identity } from "$lib/api/client";
import { codeBundle } from "./markdown-bundle";
import { workspaceRequest } from "./workspace";

export type CodeView = {
  selection(): { text: string; first: number; last: number } | null;
  measure(): void;
  destroy(): void;
};

export async function loadCodeEditor(): Promise<
  (
    parent: HTMLElement,
    text: string,
    name: string,
    writable: boolean,
    changed: (text: string) => void,
    selected: (value: boolean) => void,
    save: () => void,
    discuss: () => void,
  ) => CodeView
> {
  return (await import(/* @vite-ignore */ codeBundle)).createCodeEditor;
}

export async function saveCodeFile(
  courseId: string,
  file: string,
  opened: string,
  expected: string,
  text: string,
) {
  const body = new TextEncoder().encode(text);
  if (body.byteLength > 2 * 1024 * 1024 || /[\0\uD800-\uDFFF]/u.test(text))
    throw new Error("Save valid UTF-8 text within the 2 MiB file limit.");
  const digest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", body)),
    (n) => n.toString(16).padStart(2, "0"),
  ).join("");
  const upload = identity();
  let offset = 0;
  // Native workspace frames are bounded. Only the final verified frame can publish.
  do {
    const end = Math.min(offset + 32768, body.length);
    const result = await workspaceRequest<{
      next_offset: number | null;
      sha256?: string;
    }>("materials.save_code", {
      course_id: courseId,
      path: file,
      opened_path: opened,
      sha256: expected,
      draft_sha256: digest,
      upload_id: upload,
      offset,
      total: body.length,
      chunk_b64: btoa(String.fromCharCode(...body.subarray(offset, end))),
    });
    if (
      result.next_offset !== (end === body.length ? null : end) ||
      (end === body.length && result.sha256 !== digest)
    )
      throw new Error(
        "The file save could not be confirmed. Your draft is still open.",
      );
    offset = end;
  } while (offset < body.length);
  return digest;
}
