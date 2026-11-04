import { invoke } from "@tauri-apps/api/core";
import {
  isNativeRequest,
  isNativeResponse,
  PROTOCOL,
  type Arguments,
  type Results,
  type Lookup,
  type Operation,
} from "./wire.generated";

export class NativeFailure extends Error {
  constructor(
    readonly code: string,
    detail: string,
  ) {
    super(detail);
  }
}

export function identity(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

export async function request<O extends Operation>(
  operation: O,
  args: Arguments[O],
): Promise<Results[O]> {
  const requestId = identity();
  const nativeRequest = {
    protocol: PROTOCOL,
    request_id: requestId,
    operation,
    arguments: args,
  };
  if (!isNativeRequest(nativeRequest)) {
    throw new NativeFailure(
      "invalid_request",
      "Native request failed the generated boundary check.",
    );
  }
  const value: unknown = await invoke("native_request", {
    request: nativeRequest,
  });
  if (!isNativeResponse(operation, value) || value.request_id !== requestId) {
    throw new NativeFailure(
      "incompatible_response",
      "Native response failed the generated boundary check.",
    );
  }
  if (value.kind === "error")
    throw new NativeFailure(value.error.code, value.error.detail);
  return value.data;
}

export async function reconcilePending(): Promise<Lookup | { status: "none" }> {
  const value: unknown = await invoke("native_pending");
  if (value === null) return { status: "none" };
  if (!isNativeResponse("mutation.lookup", value))
    throw new NativeFailure(
      "incompatible_response",
      "Recovery response is invalid.",
    );
  if (value.kind === "error")
    throw new NativeFailure(value.error.code, value.error.detail);
  return value.data;
}

/**
 * Renderer-readiness marker: the contract between this frontend and the native
 * `native_rendered` command (`desktop/src-tauri/src/backend.rs`, `record_rendered`).
 *
 * It is deliberately NOT read from a presentational heading. An earlier version
 * scraped `header strong`, which made the startup contract depend on layout text:
 * the fe003 visual port removed that heading and the app rendered "Unavailable".
 * The marker is an explicit data attribute instead, so restyling the shell cannot
 * silently break startup. Both sides must be changed together — the frontend test
 * and the Rust test each fail if this value and its counterpart diverge — and the
 * window title stays owned by `routes/+page.svelte`.
 */
export const NATIVE_READINESS_MARKER = "kejtu-native-rendered";

export async function recordRendered(phase = "ready"): Promise<void> {
  const marker =
    document
      .querySelector("[data-native-readiness]")
      ?.getAttribute("data-native-readiness") ?? "";
  await invoke("native_rendered", {
    evidence: {
      title: document.title,
      marker,
      width: window.innerWidth,
      height: window.innerHeight,
      phase,
    },
  });
}

/** Reveal only a mounted, themed surface; error screens use the same path. */
export async function presentWindow(): Promise<void> {
  // Hidden WebViews may throttle animation callbacks. The bounded fallback
  // still yields a paint opportunity without hiding an error screen forever.
  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, 150);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        clearTimeout(timer);
        resolve();
      }),
    );
  });
  const marker =
    document
      .querySelector("[data-native-readiness]")
      ?.getAttribute("data-native-readiness") ?? "";
  await invoke("native_present", { marker });
}

/** A native picker returns a location; saving Files settings grants access. */
export async function chooseFolder(initial: string): Promise<string | null> {
  const value: unknown = await invoke("choose_folder", { initial });
  if (value !== null && (typeof value !== "string" || !value.startsWith("/")))
    throw new Error("The folder chooser returned an invalid location.");
  return value as string | null;
}
