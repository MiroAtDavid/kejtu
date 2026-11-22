import { draftImages, withImages } from "./images";

/**
 * Presentation of the app's existing, exact Study request templates.
 * The stored/submitted text remains the authority. Unknown or partial text stays
 * visible; matching a template grants no source access or provenance claim.
 */
export type StudyReference = {
  courseId: string;
  artifactId: string;
  path: string;
  page: number;
};
/** A paragraph run marked on a rendered PDF page, quoted on one line. */
export type StudyPassage = StudyReference & { text: string };
export type StudyRequest = {
  reference: StudyReference;
  label: string;
  raw: string;
  start: number;
  end: number;
  /** The quoted words of a marked passage request. */
  passage?: string;
};
const id = "[0-9a-f]{32}";
const page = "(?:0|[1-9][0-9]{0,5})";
const explain = new RegExp(
  `^(?:Explain this course material\\. Course|Explain this project resource\\. Project|Explain this section resource\\. Section|Explain this module resource\\. Module) (${id}); retained source (${id}), original ([^\\r\\n]+), page (${page})\\. Read the relevant exact source through university materials\\.snapshot before explaining\\.(?=$|\\n\\n)`,
);
// Built only by passageRequest(); the quote is one line, so it never spans a
// paragraph boundary of the message.
const passage = new RegExp(
  `^Question about a marked passage\\. Module (${id}); retained source (${id}), original ([^\\r\\n]+), page (${page})\\. Marked passage: "([^\\r\\n]+)"\\. Read the retained source through university materials\\.snapshot only if the passage is not enough\\.(?=$|\\n\\n)`,
);
export const MAX_PASSAGE_CHARACTERS = 6000;
/** Collapse all whitespace, including line breaks, to single spaces. */
export function oneLine(text: string) {
  return text.split(/\s+/).filter(Boolean).join(" ");
}
export function passageRequest(value: StudyPassage): string {
  const text = oneLine(value.text);
  return `Question about a marked passage. Module ${value.courseId}; retained source ${value.artifactId}, original ${value.path}, page ${value.page}. Marked passage: "${text}". Read the retained source through university materials.snapshot only if the passage is not enough.`;
}
const practiceIntents = [
  [
    "Give me one source-linked practice question. Let me try before giving the answer.",
    "Practise this page",
  ],
  ["Give me one small hint, without the full answer.", "One hint"],
  [
    "Explain the solution from this source, and assess my answer only against the actual question.",
    "Discuss my answer",
  ],
] as const;
/** A practice question about one exact retained source, in the same template
 * the chat shows as a "Practise this page" card. */
export function practiceRequest(value: StudyReference): string {
  return `${practiceIntents[0][0]}\nModule ${value.courseId}. Topic: the selected material. Question: Choose a suitable question from this source. My attempt: No answer yet. Read exact retained source ${value.artifactId} (original ${value.path}, page ${value.page}) with university materials.snapshot first. Preserve source references. Keep reported confidence separate from observed answers.`;
}
const practiceSource = new RegExp(
  `^(?:Course|Project|Section|Module) (${id})\\. Topic: [\\s\\S]*?\\. Question: [\\s\\S]*?\\. My attempt: [\\s\\S]*?\\. Read exact retained source (${id}) \\(original ([^\\r\\n]+), page (${page})\\) with university materials\\.snapshot first\\. Preserve source references\\. Keep reported confidence separate from observed answers\\.(?=$|\\n\\n)`,
);

export function studyRequests(text: string, complete = true): StudyRequest[] {
  if (!complete) return [];
  const result: StudyRequest[] = [];
  // Only whole template blocks at paragraph boundaries. Never hide a quotation
  // or a random UUID/link, and never reconstruct missing paged message bytes.
  let start = 0;
  while (start < text.length) {
    const tail = text.slice(start);
    let match = explain.exec(tail);
    let label = "Explain this page";
    let prefix = "";
    let quoted: string | undefined;
    if (!match) {
      match = passage.exec(tail);
      label = "Marked passage";
      quoted = match?.[5];
    }
    if (!match) {
      for (const [intent, name] of practiceIntents) {
        if (!tail.startsWith(intent + "\n")) continue;
        prefix = intent + "\n";
        match = practiceSource.exec(tail.slice(prefix.length));
        label = name;
        break;
      }
    }
    if (
      match &&
      (match[3].startsWith("/") || /^[A-Za-z]:[\\/]/.test(match[3]))
    ) {
      const raw = prefix + match[0];
      const end = start + raw.length;
      result.push({
        reference: {
          courseId: match[1],
          artifactId: match[2],
          path: match[3],
          page: Number(match[4]),
        },
        label,
        raw,
        start,
        end,
        ...(quoted ? { passage: quoted } : {}),
      });
      start = end;
    }
    const next = text.indexOf("\n\n", start);
    if (next < 0) break;
    start = next + 2;
  }
  return result;
}
export type StudyPart =
  { kind: "text"; text: string } | { kind: "study"; request: StudyRequest };
export function studyParts(text: string, complete = true): StudyPart[] {
  const parts: StudyPart[] = [];
  let start = 0;
  for (const request of studyRequests(text, complete)) {
    if (request.start > start)
      parts.push({ kind: "text", text: text.slice(start, request.start) });
    parts.push({ kind: "study", request });
    start = request.end;
  }
  if (start < text.length)
    parts.push({ kind: "text", text: text.slice(start) });
  return parts;
}
export function withStudyRequests(
  text: string,
  requests: StudyRequest[],
): string {
  return (
    text +
    (text && requests.length ? "\n\n" : "") +
    requests.map((item) => item.raw).join("\n\n")
  );
}
export function draftStudyRequests(text: string): {
  text: string;
  requests: StudyRequest[];
} {
  const found = studyRequests(text);
  const requests: StudyRequest[] = [];
  let end = text.length;
  for (const item of found.slice().reverse()) {
    if (item.end !== end) break;
    requests.unshift(item);
    end = item.start;
    if (text.slice(Math.max(0, end - 2), end) === "\n\n") end -= 2;
  }
  const prefix = text.slice(0, end);
  // Only expose a separate editable field when recomposition is lossless.
  return requests.length && withStudyRequests(prefix, requests) === text
    ? { text: prefix, requests }
    : { text, requests: [] };
}
/** The marked passage carried by a draft's trailing request cards, if any. */
export function draftPassage(draft: string): StudyPassage | null {
  const found = draftStudyRequests(draftImages(draft).text).requests.find(
    (item) => item.passage,
  );
  return found ? { ...found.reference, text: found.passage! } : null;
}
/** Replace the draft's marked passage card, keeping prose, other cards and images. */
export function withPassage(draft: string, value: StudyPassage | null) {
  const visible = draftImages(draft);
  const study = draftStudyRequests(visible.text);
  const kept = study.requests.filter((item) => !item.passage);
  if (!value && kept.length === study.requests.length) return draft;
  const extra = value ? passageRequest(value) : "";
  // An edited full request is ordinary text and stays as the author left it.
  const text = withStudyRequests(study.text, kept);
  return withImages(
    extra ? text + (text ? "\n\n" : "") + extra : text,
    visible.images,
  );
}
