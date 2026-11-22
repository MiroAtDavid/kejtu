import { workspaceRequest, type Source } from "./workspace";
import { oneLine } from "$lib/features/chat/study-requests";

export type MaterialPage = {
  source: Source;
  representation: string;
  content?: string;
  next_offset?: number | null;
  offset?: number;
  page_count?: number;
  image?: { mime_type: string; data: string; width: number; height: number };
  current_file_status?: string;
  notice?: string;
};

/** Keep only a small image window. Revisits read the exact saved projection,
 * while unseen pages must still belong to the original document version. */
export function pdfPages(courseId: string, path: string, first: MaterialPage) {
  const cache = new Map<number, MaterialPage>([[first.source.page, first]]);
  const sources = new Map<number, Source>([[first.source.page, first.source]]);
  let disposed = false;
  return {
    peek: (page: number) => cache.get(page),
    dispose() {
      disposed = true;
      cache.clear();
      sources.clear();
    },
    async read(page: number): Promise<MaterialPage | null> {
      if (disposed) return null;
      if (!Number.isInteger(page) || page < 1 || page > (first.page_count || 1))
        throw new Error("Choose a page within this document.");
      const cached = cache.get(page);
      if (cached) {
        cache.delete(page);
        cache.set(page, cached);
        return cached;
      }
      const source = sources.get(page);
      const result = source
        ? await workspaceRequest<MaterialPage>("materials.snapshot", {
            course_id: courseId,
            artifact_id: source.artifact_id,
            offset: 0,
          })
        : await workspaceRequest<MaterialPage>("materials.read", {
            course_id: courseId,
            path,
            page,
            view: "image",
            offset: 0,
          });
      if (disposed) return null;
      if (
        result.source.sha256 !== first.source.sha256 ||
        result.source.path !== first.source.path ||
        result.source.page !== page ||
        result.representation !== "pdf_image" ||
        !result.image ||
        (source && result.source.artifact_id !== source.artifact_id) ||
        (result.page_count !== undefined &&
          result.page_count !== first.page_count)
      )
        throw new Error(
          "This document changed. Reopen the file to read its current version; pages from different versions have not been combined.",
        );
      sources.set(page, result.source);
      cache.set(page, result);
      while (cache.size > 7) cache.delete(cache.keys().next().value!);
      return result;
    },
  };
}

export type PdfBlock = { box: [number, number, number, number]; text: string };
/** Paragraph boxes of one page in PDF points, top-left origin, as displayed. */
export type PdfLayout = { width: number; height: number; blocks: PdfBlock[] };

/** Read one page's paragraph boxes from the version already on screen. */
export async function pdfLayout(
  courseId: string,
  path: string,
  page: Source,
): Promise<PdfLayout> {
  const same = (value: MaterialPage) =>
    value.source.sha256 === page.sha256 &&
    value.source.path === page.path &&
    value.source.page === page.page &&
    value.representation === "pdf_layout";
  let result = await workspaceRequest<MaterialPage>("materials.read", {
    course_id: courseId,
    path,
    page: page.page,
    view: "layout",
    offset: 0,
    maximum: 32768,
  });
  if (!same(result))
    throw new Error("Paragraph positions belong to another document version.");
  const artifact = result.source.artifact_id;
  let content = result.content ?? "";
  while (result.next_offset != null) {
    const offset = result.next_offset;
    result = await workspaceRequest<MaterialPage>("materials.snapshot", {
      course_id: courseId,
      artifact_id: artifact,
      offset,
      maximum: 32768,
    });
    if (
      !same(result) ||
      result.source.artifact_id !== artifact ||
      result.offset !== offset
    )
      throw new Error("Paragraph positions changed while reading.");
    content += result.content ?? "";
  }
  return readLayout(JSON.parse(content));
}
function readLayout(value: unknown): PdfLayout {
  const finite = (n: unknown): n is number =>
    typeof n === "number" && Number.isFinite(n);
  const v = value as {
    page_width?: unknown;
    page_height?: unknown;
    blocks?: unknown;
  };
  if (
    !v ||
    !finite(v.page_width) ||
    !finite(v.page_height) ||
    v.page_width <= 0 ||
    v.page_height <= 0 ||
    !Array.isArray(v.blocks)
  )
    throw new Error("Paragraph positions are incomplete.");
  const blocks = v.blocks.map((block: { box?: unknown; text?: unknown }) => {
    const box = block?.box;
    if (
      !Array.isArray(box) ||
      box.length !== 4 ||
      !box.every(finite) ||
      box[2] < box[0] ||
      box[3] < box[1] ||
      typeof block.text !== "string" ||
      !block.text
    )
      throw new Error("Paragraph positions are incomplete.");
    return { box: box as PdfBlock["box"], text: block.text };
  });
  return { width: v.page_width, height: v.page_height, blocks };
}
/** The smallest paragraph under a page point, within `slack` page units. */
export function blockAt(layout: PdfLayout, x: number, y: number, slack = 0) {
  let found = -1,
    area = Infinity;
  layout.blocks.forEach(({ box: [x0, y0, x1, y1] }, index) => {
    const size = (x1 - x0) * (y1 - y0);
    if (
      x >= x0 - slack &&
      x <= x1 + slack &&
      y >= y0 - slack &&
      y <= y1 + slack &&
      size < area
    ) {
      found = index;
      area = size;
    }
  });
  return found;
}
/** The words of a block run, spaced exactly as its chat card quotes them. */
export function passageText(layout: PdfLayout, from: number, to: number) {
  return oneLine(
    layout.blocks
      .slice(from, to + 1)
      .map((block) => block.text)
      .join(" "),
  );
}
/** The first block run that quotes exactly this passage, if any. */
export function passageRange(
  layout: PdfLayout,
  text: string,
): [number, number] | null {
  for (let from = 0; from < layout.blocks.length; from++) {
    for (let to = from; to < layout.blocks.length; to++) {
      const joined = passageText(layout, from, to);
      if (joined === text) return [from, to];
      if (joined.length >= text.length) break;
    }
  }
  return null;
}
