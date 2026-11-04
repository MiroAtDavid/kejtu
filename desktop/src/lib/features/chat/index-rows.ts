/** Saved conversation metadata used by the Journal sidebar. */
export interface IndexConversation {
  id: string;
  /** Backend-owned title or first-question fallback. */
  label: string;
  /** Stored `created_at`, used in the row tooltip. */
  createdAt: string;
}
