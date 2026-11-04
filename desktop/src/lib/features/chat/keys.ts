/**
 * Composer keyboard policy.
 *
 * The old composer had a single IME guard (`if (event.isComposing) return`) and
 * no test for composition anywhere in the old suite. This module makes the
 * policy explicit and testable, and adds the two cases that guard alone misses:
 * the legacy `keyCode === 229` signal, and the final Enter that WebKit/GTK can
 * deliver *after* `compositionend` with `isComposing` already false.
 *
 * jsdom cannot faithfully reproduce a platform IME, so this module is what is
 * unit-tested; the rendered harness exercises ordinary and composed typing in a
 * real browser. Neither claims to prove every IME behaves the same.
 */

export type ComposerKeyEvent = {
  key: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  /** Legacy signal used by some IME stacks that never set `isComposing`. */
  keyCode?: number;
};

export type ComposerAction =
  /** Part of an IME composition: never submit, never consume the key. */
  | "composing"
  /** Insert a newline via the textarea's native behaviour. */
  | "newline"
  /** Submit the draft. */
  | "submit"
  /** Unrelated key. */
  | "none";

export function composerAction(
  event: ComposerKeyEvent,
  composing = false,
): ComposerAction {
  if (composing || event.isComposing || event.keyCode === 229)
    return "composing";
  if (event.key !== "Enter") return "none";
  // Shift+Enter is an explicit newline request; let the textarea handle it.
  if (event.shiftKey) return "newline";
  return "submit";
}

/**
 * Tracks composition state around a text input and absorbs the single Enter
 * that some WebKit builds emit as the composition commits.
 */
export class CompositionGuard {
  private active = false;
  private commitPending = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  start(): void {
    this.active = true;
    this.clearCommit();
  }

  end(): void {
    this.active = false;
    // The commit Enter can arrive with `isComposing` already false, so absorb
    // exactly one key immediately after the composition ends.
    this.commitPending = true;
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.commitPending = false;
      this.timer = undefined;
    }, 0);
  }

  /** True while a composition, or its commit key, is in flight. */
  get composing(): boolean {
    return this.active || this.commitPending;
  }

  destroy(): void {
    this.clearCommit();
  }

  private clearCommit(): void {
    this.commitPending = false;
    this.clearTimer();
  }

  private clearTimer(): void {
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }
}
