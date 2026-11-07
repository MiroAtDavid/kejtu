/**
 * What the Kejtu mark shows. Each working state names the kind of work so the
 * mark can show it instead of spinning: eyes and a few small props move, the
 * outline never rotates.
 */
export type KejtuState =
  | "idle"
  | "preparing"
  | "breathing"
  | "thinking"
  | "looking"
  | "reading"
  | "writing"
  | "remembering"
  | "desk"
  | "done";

export const WORKING_STATES: ReadonlySet<KejtuState> = new Set([
  "preparing",
  "breathing",
  "thinking",
  "looking",
  "reading",
  "writing",
  "remembering",
  "desk",
]);

/**
 * Map the reply phase the native stream already reports to a mark state.
 * Preparing a response comes first and can last a while, so it has its own
 * calm state; tools and connected tools are work at the laptop. Only an
 * unknown phase breathes. A finished reply blinks once; a
 * stopped, failed or interrupted one stays still.
 */
export function replyState(phase?: string | null): KejtuState {
  switch (phase) {
    case undefined:
    case null:
    case "preparing":
      return "preparing";
    case "thinking":
      return "thinking";
    case "tools":
    case "connecting":
      return "desk";
    case "searching":
      return "looking";
    case "reading":
      return "reading";
    case "writing":
    case "finishing":
      return "writing";
    case "complete":
      return "done";
    case "stopped":
    case "failed":
    case "interrupted":
      return "idle";
    default:
      return "breathing";
  }
}
