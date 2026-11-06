import { invoke, isTauri } from "@tauri-apps/api/core";

export type DocumentPopout = { close: () => void; focus: () => void };
type Options = {
  title: string;
  width: number;
  height: number;
  left: number;
  top: number;
  render: (target: HTMLElement) => () => void;
  returned: () => void;
  signal?: AbortSignal;
  failed?: (message: string) => void;
};

export function releasedOutside(
  event: Pick<PointerEvent, "clientX" | "clientY">,
  width: number,
  height: number,
): boolean {
  return (
    event.clientX < 0 ||
    event.clientY < 0 ||
    event.clientX > width ||
    event.clientY > height
  );
}

export async function openDocumentPopout(
  options: Options,
): Promise<DocumentPopout> {
  const token = [...crypto.getRandomValues(new Uint8Array(16))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  // Open synchronously within the pointer/key gesture: WebKit's popup admission
  // is lost if we await native IPC first. The host admits only this fixed page.
  options.signal?.throwIfAborted();
  const url = new URL("/document-chat.html", window.location.href);
  url.hash = token;
  const child = window.open(
    url.href,
    "_blank",
    [
      "popup",
      `width=${Math.round(options.width)}`,
      `height=${Math.round(options.height)}`,
      `left=${Math.round(options.left)}`,
      `top=${Math.round(options.top)}`,
    ].join(","),
  );
  if (!child)
    throw new Error(
      "Could not open a separate chat window. Try the pop-out button again.",
    );

  if (isTauri()) {
    try {
      await invoke("position_document_chat", {
        token,
        left: options.left,
        top: options.top,
        width: options.width,
        height: options.height,
      });
      options.signal?.throwIfAborted();
    } catch (error) {
      await invoke("close_document_chat", { token }).catch(() => {});
      throw error;
    }
  }

  let ended = false;
  let dispose: (() => void) | undefined;
  let headObserver: MutationObserver | undefined;
  let themeObserver: MutationObserver | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let ready = false;
  const end = (restore: boolean) => {
    if (ended) return;
    ended = true;
    clearInterval(poll);
    clearTimeout(timeout);
    headObserver?.disconnect();
    themeObserver?.disconnect();
    options.signal?.removeEventListener("abort", aborted);
    child.removeEventListener("pagehide", returned);
    child.removeEventListener("load", attach);
    dispose?.();
    if (isTauri())
      void invoke("close_document_chat", { token }).catch((error) =>
        options.failed?.(
          `Could not close the separate chat window: ${String(error)}`,
        ),
      );
    else if (!child.closed) child.close();
    if (restore && ready) options.returned();
  };
  const returned = () => end(true);
  const syncTheme = () => {
    const root = child.document.documentElement;
    for (const attribute of [
      "data-theme",
      "data-palette",
      "data-density",
      "lang",
      "dir",
      "style",
    ])
      if (document.documentElement.hasAttribute(attribute))
        root.setAttribute(
          attribute,
          document.documentElement.getAttribute(attribute)!,
        );
      else root.removeAttribute(attribute);
  };
  const syncStyles = () => {
    child.document.head
      .querySelectorAll("[data-parent-style]")
      .forEach((node) => node.remove());
    for (const style of document.head.querySelectorAll(
      "style, link[rel='stylesheet']",
    )) {
      const copy = style.cloneNode(true) as HTMLElement;
      copy.setAttribute("data-parent-style", "");
      if (style instanceof HTMLLinkElement)
        (copy as HTMLLinkElement).href = style.href;
      child.document.head.append(copy);
    }
  };
  let resolveReady: () => void;
  let rejectReady: (error: Error) => void;
  const loaded = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  const attach = () => {
    if (ended || ready) return;
    try {
      if (child.location.href !== url.href) return;
      const target = child.document.getElementById("document-chat-body");
      const back = child.document.getElementById("document-chat-return");
      if (!target || !back) return;
      child.document.title = options.title;
      syncStyles();
      syncTheme();
      headObserver = new MutationObserver(syncStyles);
      headObserver.observe(document.head, {
        childList: true,
        subtree: true,
        characterData: true,
      });
      themeObserver = new MutationObserver(syncTheme);
      themeObserver.observe(document.documentElement, { attributes: true });
      target.replaceChildren();
      dispose = options.render(target);
      back.addEventListener("click", returned);
      child.addEventListener("pagehide", returned);
      child.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !event.defaultPrevented) returned();
      });
      ready = true;
      clearTimeout(timeout);
      child.focus();
      target.querySelector("textarea")?.focus();
      resolveReady();
    } catch (error) {
      end(false);
      rejectReady(error instanceof Error ? error : new Error(String(error)));
    }
  };
  child.addEventListener("load", attach);
  const aborted = () => {
    end(false);
    rejectReady(new Error("The document chat was closed."));
  };
  options.signal?.addEventListener("abort", aborted, { once: true });
  poll = setInterval(() => {
    if (child.closed) {
      end(true);
      if (!ready)
        rejectReady(new Error("The chat window closed before it was ready."));
    } else attach();
  }, 100);
  timeout = setTimeout(() => {
    end(false);
    rejectReady(
      new Error("The chat window did not open. Your chat is still here."),
    );
  }, 10000);
  attach();
  await loaded;
  return { close: () => end(false), focus: () => child.focus() };
}
