import { invoke } from "@tauri-apps/api/core";

/** Match the native boundary; preserve the exact destination, including query/fragment. */
export function webUrl(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    value.length > 8192 ||
    new TextEncoder().encode(value).length > 8192 ||
    !/^https?:\/\/[^/]/i.test(value) ||
    /[\u0000-\u0020\u007f\\]/.test(value)
  )
    return null;
  try {
    const url = new URL(value);
    return url.hostname && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}

export async function openWebLink(url: string): Promise<void> {
  if (!webUrl(url)) throw new Error("This is not a supported web address.");
  await invoke("open_web_link", { url });
}
