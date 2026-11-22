import { workspaceRequest } from "./workspace";
export const MAX_DOCUMENT_BYTES = 32 * 1024 * 1024;
export type DocumentBytes = {
  path: string;
  sha256: string;
  bytes: ArrayBuffer;
  writable?: boolean;
};
type Chunk = {
  path: string;
  sha256: string;
  total_bytes: number;
  offset: number;
  next_offset: number | null;
  data: string;
  writable?: boolean;
};
export async function readCourseDocument(
  courseId: string,
  file: string,
  current: () => boolean,
  code = false,
): Promise<DocumentBytes> {
  const text = code || /\.(md|markdown)$/i.test(file);
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
      chunk.total_bytes < (text ? 0 : 5) ||
      chunk.total_bytes > (text ? 2 * 1024 * 1024 : MAX_DOCUMENT_BYTES) ||
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
      (!bytes.length && !(text && chunk.total_bytes === 0)) ||
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
        ...(code
          ? { writable: first.writable === true && chunk.writable === true }
          : {}),
      };
    }
    offset = chunk.next_offset;
  }
  throw new Error("Reader closed.");
}
