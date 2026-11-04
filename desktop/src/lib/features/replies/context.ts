import type { WebSourceSummary } from "$lib/api/wire.generated";
export const VISUALS = Symbol("reply-visuals");
export type VisualContext = {
  turnId: () => string;
  complete: () => boolean;
  charts: () => Map<string, string | null>;
  sources: () => WebSourceSummary[];
};
