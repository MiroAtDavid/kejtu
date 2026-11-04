import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type ImageDrop = {
  kind: "over" | "drop" | "leave";
  x: number;
  y: number;
  token: string | null;
  count: number;
  error: string | null;
};

export function nativeImageInput(): boolean {
  return isTauri();
}

/** Clipboard access is requested only by an explicit paste in the composer. */
export function clipboardImage(): Promise<string | null> {
  return invoke("read_clipboard_image");
}

/** Tokens originate in an actual OS drop; callers cannot supply a file path. */
export function droppedImage(token: string, index: number): Promise<string> {
  return invoke("read_dropped_image", { token, index });
}

export function listenImageDrops(
  handler: (drop: ImageDrop) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(() => {});
  return listen<ImageDrop>("kejtu-image-input", ({ payload }) =>
    handler(payload),
  );
}
