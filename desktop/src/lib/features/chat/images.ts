/** Only explicit inline PNG/JPEG bytes. Never load a path or remote URL. */
import type { ImageAttachment, Results } from "$lib/api/wire.generated";
export type VisibleBody = Results["body.read"] &
  Partial<
    Pick<Results["body.view"], "images" | "source_sha256" | "source_bytes">
  >;
export type GalleryImage = {
  id: string;
  src?: string;
  width?: number;
  height?: number;
  load?: () => Promise<string>;
};
export type ImageReader = (
  turnId: string,
  bodyId: string,
  source: string,
  image: ImageAttachment,
) => Promise<string>;
export function savedImages(
  body: VisibleBody | undefined,
  turnId: string,
  read: ImageReader,
): GalleryImage[] {
  if (body && !body.images)
    return imageParts(body.content, body.complete)
      .filter((part) => part.kind === "image")
      .map((part, index) => ({
        id: `${body.body_id}:${index}`,
        src: part.value,
      }));
  return (body?.images ?? []).map((image) => ({
    id: `${body!.body_id}:${image.index}:${image.sha256}`,
    width: image.width,
    height: image.height,
    load: () => read(turnId, body!.body_id, body!.source_sha256!, image),
  }));
}
// Leave native request capacity for foreground controls. Image IO is read-only
// and independently bounded; a large gallery never opens 32 requests at once.
let imageReads = 0;
const imageQueue: (() => void)[] = [];
export async function queuedImage(
  read: () => Promise<string>,
): Promise<string> {
  if (imageReads >= 2)
    await new Promise<void>((resolve) => imageQueue.push(resolve));
  else imageReads++;
  try {
    return await read();
  } finally {
    const next = imageQueue.shift();
    if (next) next();
    else imageReads--;
  }
}
export type ImagePart = { kind: "text" | "image" | "pending"; value: string };
export function imageParts(content: string, complete = true): ImagePart[] {
  const pattern =
    /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+[ \t]*\r?$/gm;
  const parts: ImagePart[] = [];
  let start = 0;
  for (const match of content.matchAll(pattern)) {
    const offset = match.index!;
    if (offset > start)
      parts.push({ kind: "text", value: content.slice(start, offset) });
    const pending = !complete && offset + match[0].length === content.length;
    parts.push({
      kind: pending ? "pending" : "image",
      value: match[0].trimEnd(),
    });
    start = offset + match[0].length;
  }
  if (start < content.length)
    parts.push({ kind: "text", value: content.slice(start) });
  return parts;
}

export function draftImages(draft: string) {
  const parts = imageParts(draft);
  // Each image owns one preceding line separator, inserted by withImages.
  // Keep that delimiter out of the editable text while preserving author-entered
  // blank lines. Otherwise every keystroke moves the caret past another newline.
  for (let index = 1; index < parts.length; index += 1) {
    if (parts[index].kind === "image" && parts[index - 1].kind === "text") {
      parts[index - 1].value = parts[index - 1].value.replace(/\n$/, "");
    }
  }
  return {
    text: parts
      .filter((p) => p.kind === "text")
      .map((p) => p.value)
      .join(""),
    images: parts.filter((p) => p.kind === "image").map((p) => p.value),
  };
}

export function withImages(text: string, images: string[]) {
  return images.length ? text + (text ? "\n" : "") + images.join("\n") : text;
}

function imageMime(file: File): string {
  const mime =
    file.type ||
    (/\.png$/i.test(file.name)
      ? "image/png"
      : /\.jpe?g$/i.test(file.name)
        ? "image/jpeg"
        : "");
  if (!["image/png", "image/jpeg"].includes(mime))
    throw new Error("Choose a PNG or JPEG image.");
  if (!file.size || file.size > MAX_IMAGE_BYTES)
    throw new Error(
      "Choose an image smaller than 1.4 MB. Large document pages can use a smaller preview.",
    );
  return mime;
}

/** Exact data-URL length from file metadata, without reading its bytes. */
export function imageFileLength(file: File): number {
  return (
    `data:${imageMime(file)};base64,`.length + 4 * Math.ceil(file.size / 3)
  );
}

export async function readImage(file: File): Promise<string> {
  const mime = imageMime(file);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const url = String(reader.result);
        validateDraftImage(url);
        resolve(url);
      } catch (error) {
        reject(error);
      }
    };
    reader.onerror = () => reject(new Error("This image could not be read."));
    reader.readAsDataURL(file.type ? file : file.slice(0, file.size, mime));
  });
}

export const MAX_IMAGE_BYTES = 1_400_000;
export const MAX_IMAGES = 32;
export const MAX_IMAGE_SIDE = 4096;
export const byteSize = (bytes: number) =>
  bytes >= 1_000_000
    ? `${(bytes / 1_000_000).toFixed(2)} MB`
    : `${Math.floor(bytes / 1000)} kB`;
export function imageBytes(url: string): number {
  const payload = url.slice(url.indexOf(",") + 1);
  return (
    (payload.length * 3) / 4 -
    (payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0)
  );
}
export function imageAllowance(
  text: string,
  count: number,
  maximum: number,
): number {
  return Math.max(
    0,
    Math.floor(
      (maximum -
        new TextEncoder().encode(text).length -
        (text ? 1 : 0) -
        Math.max(0, count - 1) -
        count * 23) /
        4,
    ) * 3,
  );
}
export function checkImageCapacity(
  text: string,
  lengths: number[],
  maximum: number | null,
) {
  if (lengths.length > MAX_IMAGES)
    throw Error("Use up to 32 images per message.");
  if (maximum === null)
    throw Error("Wait for this conversation's image limit to load.");
  const bytes =
    new TextEncoder().encode(text).length +
    lengths.reduce((a, b) => a + b, 0) +
    (lengths.length ? Math.max(0, lengths.length - 1) + (text ? 1 : 0) : 0);
  if (bytes > maximum)
    throw Error(
      `These images won't fit together. With the current text, use about ${byteSize(imageAllowance(text, lengths.length, maximum))} of images in total, or send smaller batches. Your draft is unchanged.`,
    );
}

/** Inspect headers before the browser allocates decoded pixels. Never resize originals. */
export function validateDraftImage(url: string) {
  const comma = url.indexOf(",");
  const mime = url.slice(0, comma);
  if (!["data:image/png;base64", "data:image/jpeg;base64"].includes(mime))
    throw Error("Choose a PNG or JPEG image.");
  if (imageBytes(url) > MAX_IMAGE_BYTES)
    throw Error("Use images up to 1.4 MB each.");
  let raw: string;
  try {
    raw = atob(url.slice(comma + 1));
  } catch {
    throw Error("This image could not be read.");
  }
  const byte = (i: number) => raw.charCodeAt(i);
  const u16 = (i: number) => byte(i) * 256 + byte(i + 1);
  const u32 = (i: number) => u16(i) * 65536 + u16(i + 2);
  let width = 0,
    height = 0;
  if (
    mime.includes("png") &&
    raw.startsWith("\x89PNG\r\n\x1a\n") &&
    raw.slice(12, 16) === "IHDR"
  ) {
    width = u32(16);
    height = u32(20);
  } else if (mime.includes("jpeg") && raw.startsWith("\xff\xd8")) {
    let offset = 2;
    while (offset + 4 <= raw.length && byte(offset) === 255) {
      while (byte(offset) === 255) offset++;
      const marker = byte(offset++);
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 1 || (marker >= 0xd0 && marker <= 0xd8)) continue;
      const size = u16(offset);
      if (size < 2 || offset + size > raw.length) break;
      if (
        [
          0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd,
          0xce, 0xcf,
        ].includes(marker) &&
        size >= 8
      ) {
        height = u16(offset + 3);
        width = u16(offset + 5);
        break;
      }
      offset += size;
    }
  }
  if (!(
    width > 0 &&
    height > 0 &&
    width <= MAX_IMAGE_SIDE &&
    height <= MAX_IMAGE_SIDE
  ))
    throw Error("Use PNG or JPEG images up to 4096 × 4096 pixels.");
}
