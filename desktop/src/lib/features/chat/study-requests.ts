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
export type StudyRequest = {
  reference: StudyReference;
  label: string;
  raw: string;
  start: number;
  end: number;
};
const id = "[0-9a-f]{32}";
const page = "(?:0|[1-9][0-9]{0,5})";
const explain = new RegExp(
  `^(?:Explain this course material\\. Course|Explain this project resource\\. Project) (${id}); retained source (${id}), original ([^\\r\\n]+), page (${page})\\. Read the relevant exact source through university materials\\.snapshot before explaining\\.(?=$|\\n\\n)`,
);
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
const practiceSource = new RegExp(
  `^(?:Course|Project) (${id})\\. Topic: [\\s\\S]*?\\. Question: [\\s\\S]*?\\. My attempt: [\\s\\S]*?\\. Read exact retained source (${id}) \\(original ([^\\r\\n]+), page (${page})\\) with university materials\\.snapshot first\\. Preserve source references\\. Keep reported confidence separate from observed answers\\.(?=$|\\n\\n)`,
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
