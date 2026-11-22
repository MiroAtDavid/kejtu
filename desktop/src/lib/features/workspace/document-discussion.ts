import type { Readable } from "svelte/store";
import type { DocumentReference } from "./document-assist";
import type { DocumentReply } from "./document-assist";
import type { DocumentExchange } from "./document-conversation";
import type { KejtuState } from "$lib/design/kejtu-state";

// Main chat observes the editor's existing state; the editor retains the draft,
// request snapshot and validated edit/undo owner.
export type DocumentDiscussion = {
  reference: DocumentReference;
  model: Readable<DocumentDiscussionState | null>;
  actions: DocumentDiscussionActions;
};
export type DocumentDiscussionState = {
  sessionId: string;
  focus: string;
  hasFocus: boolean;
  /** The marked passage the chat box shows as a card ("" for none). */
  marked: string;
  /** The mark is a place to insert at (after a passage), not the passage. */
  insertion: boolean;
  asking: boolean;
  sourceName: string;
  usePdf: boolean;
  expanded: boolean;
  visibleExchanges: DocumentExchange[];
  exchanges: { instruction: string; reply: string }[];
  canEarlier: boolean;
  loading: boolean;
  hasHistory: boolean;
  proposal: DocumentReply | null;
  assistError: string;
  answer: string;
  liveAnswer: string;
  result: string;
  canUndo: boolean;
  undoBlocked: boolean;
  instruction: string;
  ready: boolean;
  stopped: boolean;
  available: boolean;
  pending: boolean;
  phase: string;
  /** What the Kejtu mark shows while an edit request runs; null when idle. */
  kejtu: KejtuState | null;
};

type Action = () => void | Promise<unknown>;
export type DocumentDiscussionActions = {
  input: (value: string) => void;
  source: (value: boolean) => void;
  wholeDocument: Action;
  toggleHistory: Action;
  moreHistory: (turnId?: string) => void | Promise<unknown>;
  dismissProposal: Action;
  dismissError: Action;
  dismissResult: Action;
  undo: Action;
  instructionKeys: (event: KeyboardEvent) => void;
  ask: Action;
  stop: Action;
  resolve: Action;
};
