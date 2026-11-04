import { workspaceRequest, type Source, type Settings } from "./workspace";
import type { MaterialPage } from "./material-reader";

/** Identity of the last page actually displayed, including in a hidden source tab. */
export type OpenDocument = {
  file: string;
  path: string;
  sha256: string;
  page: number;
  pages: number;
};
export type DocumentSource = {
  course_id: string;
  file: string;
  source: Source;
  page_count: number;
  representation: "pdf_text" | "pdf_image";
};

export async function prepareDocumentSource(
  courseId: string,
  current: () => OpenDocument,
  signal: AbortSignal,
  send = workspaceRequest,
): Promise<DocumentSource> {
  const expected = { ...current() };
  const check = () => {
    if (signal.aborted) throw new Error("Stopped. Your document is unchanged.");
    const now = current();
    if (
      (["file", "path", "sha256", "page", "pages"] as const).some(
        (key) => now[key] !== expected[key],
      )
    )
      throw new Error(
        "The source PDF or page changed. Send your instruction again when ready.",
      );
  };
  check();
  // Local reader access never enables model access. The model's own reads still
  // recheck today's conversation, Study and Files permissions through university.
  const access = await send<{ settings: Settings }>("settings.read", {});
  check();
  if (!access.settings.university_enabled || !access.settings.model_read)
    throw new Error(
      "Allow Kejtu to read Study in Settings → Privacy & access → Study & workspace, or turn off the PDF source for this edit.",
    );
  const read = async (view: "text" | "image") => {
    const value = await send<MaterialPage>("materials.read", {
      course_id: courseId,
      path: expected.file,
      page: expected.page,
      view,
      offset: 0,
      maximum: 16384,
    });
    check();
    if (
      value.source.path !== expected.path ||
      value.source.sha256 !== expected.sha256 ||
      value.source.page !== expected.page ||
      value.page_count !== expected.pages ||
      value.representation !== `pdf_${view}`
    )
      throw new Error(
        "The PDF changed since you opened it. Reopen it before using it as a source.",
      );
    return value;
  };
  let page = await read("text");
  // Scans need their actual image, not an empty text projection presented as evidence.
  if (!page.content?.trim() && page.next_offset == null)
    page = await read("image");
  check();
  return {
    course_id: courseId,
    file: expected.file,
    source: page.source,
    page_count: expected.pages,
    representation: page.representation as DocumentSource["representation"],
  };
}
