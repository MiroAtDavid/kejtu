import type { DocumentReply } from "./document-assist";
import type { DocumentExchange } from "./document-conversation";

// Both surfaces observe the same editor. Neither owns a request or a draft.
export type DocumentDiscussionState = {
  sessionId: string;
  focus: string;
  hasFocus: boolean;
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
