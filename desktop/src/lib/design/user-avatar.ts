import { readonly, writable } from "svelte/store";

const KEY = "kejtu-next.appearance.user-avatar.v1";
const MAX_STORED_LENGTH = 128_000;
const avatar = writable<string | null>(null);
export const userAvatar = readonly(avatar);

function isThumbnail(value: string): boolean {
  return (
    value.length <= MAX_STORED_LENGTH &&
    /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value)
  );
}

export function restoreUserAvatar(): void {
  let source: string | null = null;
  try {
    const saved = localStorage.getItem(KEY);
    // A fresh installation has no personal picture; explicit removal survives restart.
    if (saved === "none") source = null;
    else if (saved && isThumbnail(saved)) source = saved;
  } catch {
    // A missing appearance preference must not prevent opening conversations.
  }
  avatar.set(source);
}

export function saveUserAvatar(source: string | null): void {
  if (source !== null && !isThumbnail(source))
    throw new Error("This thumbnail could not be saved. Choose another image.");
  try {
    localStorage.setItem(KEY, source ?? "none");
  } catch {
    // Keep the old picture when persistence fails; do not claim a durable change.
    throw new Error(
      "Your thumbnail could not be saved. The previous picture is unchanged.",
    );
  }
  avatar.set(source);
}

/** Keep only a small decoded image, not the source photo or its metadata. */
export async function readUserThumbnail(file: File): Promise<string> {
  const mime =
    file.type ||
    (/\.png$/i.test(file.name)
      ? "image/png"
      : /\.jpe?g$/i.test(file.name)
        ? "image/jpeg"
        : /\.webp$/i.test(file.name)
          ? "image/webp"
          : "");
  if (!["image/png", "image/jpeg", "image/webp"].includes(mime))
    throw new Error("Choose a PNG, JPEG or WebP image.");
  if (!file.size || file.size > 8_000_000)
    throw new Error("Choose an image smaller than 8 MB.");
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () =>
        reject(
          new Error("This image could not be opened. Choose another image."),
        );
      image.src = url;
    });
    if (
      !image.naturalWidth ||
      !image.naturalHeight ||
      image.naturalWidth * image.naturalHeight > 40_000_000
    )
      throw new Error("Choose an image no larger than 40 megapixels.");
    const size = Math.min(image.naturalWidth, image.naturalHeight);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = Math.min(128, size);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("This image could not be resized.");
    context.drawImage(
      image,
      (image.naturalWidth - size) / 2,
      (image.naturalHeight - size) / 2,
      size,
      size,
      0,
      0,
      canvas.width,
      canvas.height,
    );
    const result = canvas.toDataURL("image/png");
    if (!isThumbnail(result))
      throw new Error("This image could not be resized. Choose another image.");
    return result;
  } finally {
    URL.revokeObjectURL(url);
  }
}
