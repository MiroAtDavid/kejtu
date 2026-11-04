// Chart schema and bounds ported from the original rich reply implementation.
import { Lexer, walkTokens } from "marked";
export type ChartSpec = {
  version: 1;
  id: string;
  title: string;
  xLabel: string;
  yLabel: string;
  values: { x: number | string; y: number }[];
  provenance: "illustrative" | "reported";
  note: string;
  sourceRefs?: string[];
};
export type ChartState = {
  kind: "line" | "bar" | "scatter";
  scale: "linear" | "log";
  point: number;
  table: boolean;
};
const identifier = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
export const record = (v: unknown): v is Record<string, any> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown, max = 1000): v is string =>
  typeof v === "string" && !!v.trim() && v.length <= max;
const id = (v: unknown): v is string =>
  typeof v === "string" &&
  identifier.test(v) &&
  !["__proto__", "constructor", "prototype"].includes(v);
const componentBase = (v: unknown): v is Record<string, any> =>
  record(v) && v.version === 1 && id(v.id);
export function isChart(v: unknown): v is ChartSpec {
  if (
    !componentBase(v) ||
    // Attribution resolves against actual receipts. An unresolvable reference
    // must not hide otherwise valid numbers or become a fabricated source link.
    !(
      v.sourceRefs === undefined ||
      (Array.isArray(v.sourceRefs) &&
        v.sourceRefs.length <= 8 &&
        v.sourceRefs.every((ref: unknown) => str(ref, 300)))
    ) ||
    ![v.title, v.xLabel, v.yLabel, v.note].every((t) => str(t)) ||
    !["illustrative", "reported"].includes(v.provenance) ||
    !Array.isArray(v.values) ||
    v.values.length < 2 ||
    v.values.length > 200
  )
    return false;
  const numeric = typeof v.values[0]?.x === "number";
  return (
    v.values.every(
      (p: unknown, i: number) =>
        record(p) &&
        typeof p.y === "number" &&
        Number.isFinite(p.y) &&
        (numeric
          ? typeof p.x === "number" &&
            Number.isFinite(p.x) &&
            (i === 0 || p.x > v.values[i - 1].x)
          : str(p.x, 80)),
    ) && new Set(v.values.map((p: any) => p.x)).size === v.values.length
  );
}
export const freshChart = (spec: ChartSpec): ChartState => ({
  kind: typeof spec.values[0].x === "number" ? "line" : "bar",
  scale: "linear",
  point: 0,
  table: false,
});

export function decodeChart(lang: string, source: string): ChartSpec | null {
  if (lang !== "kejtu-chart" || source.length > 32000) return null;
  try {
    const value: unknown = JSON.parse(source);
    return isChart(value) ? value : null;
  } catch {
    return null;
  }
}
/** Duplicate identities and normalized quoted bodies stay readable code. */
export function chartBlocks(source: string): Map<string, string | null> {
  const result = new Map<string, string | null>();
  walkTokens(Lexer.lex(source), (token) => {
    if (token.type !== "code") return;
    const spec = decodeChart(token.lang ?? "", token.text);
    const fence = token.raw.match(/^ {0,3}(`{3,}|~{3,})[^\n]*\n/);
    const closed =
      fence &&
      new RegExp(
        `\\n {0,3}${fence[1][0]}{${fence[1].length},}[ \\t]*\\n*$`,
      ).test(token.raw);
    if (!spec || !closed || !source.includes(token.text)) return;
    result.set(spec.id, result.has(spec.id) ? null : token.text);
  });
  return result.size <= 12 ? result : new Map();
}
