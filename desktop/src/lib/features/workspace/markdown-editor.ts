import type { DocumentPatch } from "./document-patches";
import type { EditTarget } from "./document-assist";
import type { ActivityAnchor } from "./document-activity";
import { markdownBundle, markdownStyles } from "./markdown-bundle";
export const MAX_MARKDOWN_BYTES = 2 * 1024 * 1024;
export type MarkdownView = {
  beginWork: () => { id: number; text: string };
  workRange: (
    id: number,
    patches: DocumentPatch[],
  ) => { from: number; to: number };
  endWork: (id: number) => void;
  setWorkPreview: (
    id: number,
    patches: DocumentPatch[] | null,
  ) => string | null;
  applyWork: (id: number, patches: DocumentPatch[]) => boolean;
  setMode?: (mode: string) => void;
  markTarget: (range?: { from: number; to: number }) => EditTarget;
  selection: () => { from: number; to: number };
  positionAt: (x: number, y: number) => number | null;
  target: () => EditTarget | null;
  targetRect?: () => ActivityAnchor | null;
  setLivePreview?: (id: number, replacement: string | null) => string | null;
  rearmTarget?: () => EditTarget | null;
  clearTarget: () => void;
  applyTarget: (id: number, replacement: string) => boolean;
  focus: () => void;
  measure: () => void;
  undo: () => void;
  redo: () => void;
  destroy: () => void;
};
export async function mountMarkdown(
  parent: HTMLElement,
  text: string,
  changed: (value: string) => void,
  save: () => void,
  targetChanged: (value: EditTarget | null) => void = () => {},
  options?: {
    richParent: HTMLElement;
    toolsParent: HTMLElement;
    onError: (message: string) => void;
    onMode: (mode: string) => void;
  },
): Promise<MarkdownView> {
  if (!document.querySelector(`link[href="${markdownStyles}"]`)) {
    await new Promise<void>((resolve, reject) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = markdownStyles;
      link.onload = () => resolve();
      link.onerror = () => {
        link.remove();
        reject(new Error("Could not load the document editor styles."));
      };
      document.head.append(link);
    });
  }
  const module = await import(/* @vite-ignore */ markdownBundle);
  return options
    ? module.createWorkbench(
        parent,
        text,
        changed,
        save,
        targetChanged,
        options,
      )
    : module.createEditor(parent, text, changed, save, targetChanged);
}
export function markdownBytes(text: string) {
  const bytes = new TextEncoder().encode(text);
  if (bytes.length > MAX_MARKDOWN_BYTES)
    throw new Error(
      "This Markdown document exceeds the 2 MB editor limit. Your text is still here.",
    );
  if (text.includes("\0"))
    throw new Error("Remove binary NUL characters before saving Markdown.");
  let binary = "";
  for (let i = 0; i < bytes.length; i += 32768)
    binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(binary);
}
