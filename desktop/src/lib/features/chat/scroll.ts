/**
 * Reading-position policy for the transcript.
 *
 * These helpers are deliberately pure so the follow-at-bottom and
 * history-anchor rules can be tested without a layout engine: jsdom reports
 * `scrollHeight`/`clientHeight` as 0, so the browser harness is what verifies
 * the real scrolling, while these functions pin the decision logic.
 */

export type ScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

/**
 * How close to the bottom still counts as "at the bottom". A small slack keeps
 * a reader who is a few pixels up (for example after a sub-pixel layout change)
 * following new output rather than being silently detached from it.
 */
export const FOLLOW_SLACK_PX = 24;

export function readMetrics(node: {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}): ScrollMetrics {
  return {
    scrollTop: node.scrollTop,
    scrollHeight: node.scrollHeight,
    clientHeight: node.clientHeight,
  };
}

export function distanceFromBottom(metrics: ScrollMetrics): number {
  // A container shorter than its viewport has no scrollback; treat it as flush
  // with the bottom rather than reporting a negative distance.
  return Math.max(
    0,
    metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight,
  );
}

export function isAtBottom(
  metrics: ScrollMetrics,
  slack = FOLLOW_SLACK_PX,
): boolean {
  return distanceFromBottom(metrics) <= slack;
}

/**
 * Whether new output should move the reader. Following is only ever continued
 * while the reader is already at the bottom; scrolling up takes priority and is
 * never overridden by arriving content.
 */
export function shouldFollow(
  metrics: ScrollMetrics,
  slack = FOLLOW_SLACK_PX,
): boolean {
  return isAtBottom(metrics, slack);
}
