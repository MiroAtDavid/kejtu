import { workspaceRequest, type Source } from "./workspace";

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
