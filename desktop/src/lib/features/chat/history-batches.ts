/** Stable boundaries: prepending history or appending a reply never rekeys a
 * mounted answer. Sequences are decimal strings and can exceed Number precision.
 * Unknown legacy sequences stay individually mounted rather than being dropped.
 */
export function historyBatches<
  T extends { turn: { id: string; sequence: string } },
>(views: T[]): { key: string; entries: T[] }[] {
  const batches: { key: string; entries: T[] }[] = [];
  for (const view of views) {
    const sequence = view.turn.sequence;
    const key = /^[1-9]\d*$/.test(sequence)
      ? `sequence-${(BigInt(sequence) - 1n) / 20n}`
      : `entry-${view.turn.id}`;
    const last = batches.at(-1);
    if (last?.key === key) last.entries.push(view);
    else batches.push({ key, entries: [view] });
  }
  return batches;
}
