import { workspaceRequest } from "./workspace";
export const MAX_PDF_BYTES = 32 * 1024 * 1024;
export type DocumentBytes = {
  path: string;
  sha256: string;
  bytes: ArrayBuffer;
};
type Chunk = {
  path: string;
  sha256: string;
  total_bytes: number;
  offset: number;
  next_offset: number | null;
  data: string;
};
export async function readCourseDocument(
  courseId: string,
  file: string,
  current: () => boolean,
): Promise<DocumentBytes> {
  const markdown = /\.(md|markdown)$/i.test(file);
  let offset = 0,
    first: Chunk | undefined,
    buffer: Uint8Array | undefined;
  while (current()) {
    const chunk = await workspaceRequest<Chunk>("materials.document", {
      course_id: courseId,
      path: file,
      offset,
      ...(first ? { sha256: first.sha256 } : {}),
    });
    if (!current()) throw new Error("Reader closed.");
    if (
      !Number.isInteger(chunk.total_bytes) ||
      chunk.total_bytes < (markdown ? 0 : 5) ||
      chunk.total_bytes > (markdown ? 2 * 1024 * 1024 : MAX_PDF_BYTES) ||
      chunk.offset !== offset ||
      !/^[0-9a-f]{64}$/.test(chunk.sha256) ||
      typeof chunk.path !== "string"
    )
      throw new Error("Invalid document response.");
    if (
      first &&
      (chunk.sha256 !== first.sha256 ||
        chunk.path !== first.path ||
        chunk.total_bytes !== first.total_bytes)
    )
      throw new Error("The document changed during reading. Reopen it.");
    if (!first) {
      first = chunk;
      buffer = new Uint8Array(chunk.total_bytes);
    }
    const bytes = Uint8Array.from(atob(chunk.data), (c) => c.charCodeAt(0));
    const end = offset + bytes.length;
    if (
      (!bytes.length && !(markdown && chunk.total_bytes === 0)) ||
      bytes.length > 1024 * 1024 ||
      end > buffer!.length ||
      chunk.next_offset !== (end === buffer!.length ? null : end)
    )
      throw new Error("Incomplete document response.");
    buffer!.set(bytes, offset);
    if (chunk.next_offset === null) {
      const digest = Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", buffer!.buffer as ArrayBuffer),
        ),
      )
        .map((v) => v.toString(16).padStart(2, "0"))
        .join("");
      if (digest !== first.sha256)
        throw new Error("Document verification failed. Reopen it.");
      return {
        path: first.path,
        sha256: first.sha256,
        bytes: buffer!.buffer as ArrayBuffer,
      };
    }
    offset = chunk.next_offset;
  }
  throw new Error("Reader closed.");
}
export type ReaderState = {
  token: string | null;
  page: number;
  pages: number;
  modified: boolean;
  dirty: boolean;
  name: string;
  request: string;
};
export function readerState(value: unknown): ReaderState | null {
  if (!value || typeof value !== "object") return null;
  const s = value as ReaderState;
  if (
    !(
      s.token === null ||
      (typeof s.token === "string" && /^[0-9a-f]{32}$/.test(s.token))
    ) ||
    !Number.isInteger(s.page) ||
    !Number.isInteger(s.pages) ||
    s.page < 0 ||
    s.pages < s.page ||
    s.pages > 100000 ||
    typeof s.modified !== "boolean" ||
    typeof s.dirty !== "boolean" ||
    typeof s.name !== "string" ||
    typeof s.request !== "string"
  )
    return null;
  return s;
}

// Compatibility name for the existing PDF-only caller.
export const readCoursePdf = readCourseDocument;
