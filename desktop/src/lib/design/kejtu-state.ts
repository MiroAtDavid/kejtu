/**
 * What the Kejtu mark shows. Each working state names the kind of work so the
 * mark can show it instead of spinning: eyes and a few small props move, the
 * outline never rotates while work goes on. "Chasing" is the one playful
 * working loop: the eyes chase each other round the inside of the outline.
 * "Sleeping" is a quiet idle: eyes closed, slow breaths.
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
  | "chasing"
  | "sleeping"
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
  "chasing",
]);

/**
 * One-shot moves the mark can play on top of any state, each once, then back
 * to the state's own loop. Small places keep every gesture inside the outline.
 */
export type KejtuGesture =
  | "blink"
  | "wink"
  | "glance"
  | "nod"
  | "hop"
  | "hello"
  | "squish"
  | "love"
  | "wake"
  | "peek"
  | "spin"
  | "roll";

/** How long each gesture plays. The CSS keyframes use the same durations. */
export const GESTURE_MS: Readonly<Record<KejtuGesture, number>> = {
  blink: 380,
  wink: 800,
  glance: 1600,
  nod: 1000,
  hop: 1100,
  hello: 1600,
  squish: 900,
  love: 1700,
  wake: 900,
  peek: 2200,
  spin: 1700,
  roll: 2800,
};

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
