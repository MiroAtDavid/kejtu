import { identity, request } from "$lib/api/client";
import {
  isNativeRequest,
  PROTOCOL,
  type Arguments,
  type JobReceipt,
} from "$lib/api/wire.generated";

export type Change =
  | { operation: "jobs.save"; arguments: Arguments["jobs.save"] }
  | { operation: "jobs.cancel"; arguments: Arguments["jobs.cancel"] }
  | { operation: "jobs.cancel_run"; arguments: Arguments["jobs.cancel_run"] };
export type Pending = Change & { intent_sha256: string };

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, item]) => [key, sorted(item)]),
    );
  return value;
}

export async function digest(change: Change): Promise<string> {
  const args = change.arguments;
  const intent =
    change.operation === "jobs.save"
      ? {
          action: "save",
          definition: change.arguments.definition,
          job_id: "job_id" in args ? args.job_id : null,
          expected_revision:
            "expected_revision" in args ? args.expected_revision : null,
        }
      : change.operation === "jobs.cancel"
        ? {
            action: "cancel",
            job_id: change.arguments.job_id,
            expected_revision: change.arguments.expected_revision,
          }
        : { action: "cancel_run", run_id: change.arguments.run_id };
  const bytes = new TextEncoder().encode(JSON.stringify(sorted(intent)));
  const result = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(result)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function valid(change: unknown): change is Change {
  if (!change || typeof change !== "object") return false;
  const c = change as Change;
  return (
    ["jobs.save", "jobs.cancel", "jobs.cancel_run"].includes(c.operation) &&
    isNativeRequest({
      protocol: PROTOCOL,
      request_id: "0".repeat(32),
      operation: c.operation,
      arguments: c.arguments,
    })
  );
}

/** One state-scoped pending intent. Reopening performs lookup only. */
export class JobJournal {
  pending: Pending | null = null;
  absent = false;
  private preparing = false;
  readonly key: string;

  constructor(
    stateId: string,
    private send = request,
    private remember?: (receipt: JobReceipt) => void,
  ) {
    this.key = `kejtu-next.jobs.v1.${stateId}`;
  }

  async restore(): Promise<JobReceipt | null> {
    const raw = localStorage.getItem(this.key);
    if (raw === null) return null;
    const saved: Pending = JSON.parse(raw);
    if (
      !valid(saved) ||
      !/^[0-9a-f]{64}$/.test(saved.intent_sha256) ||
      (await digest(saved)) !== saved.intent_sha256
    )
      throw new Error(
        "Job recovery record is invalid. It was retained; no action was sent.",
      );
    this.pending = saved;
    return this.resolve();
  }

  private accepted(receipt: JobReceipt) {
    const p = this.pending;
    if (
      !p ||
      receipt.mutation_key !== p.arguments.mutation_key ||
      receipt.intent_sha256 !== p.intent_sha256 ||
      ("job_id" in p.arguments && receipt.job_id !== p.arguments.job_id) ||
      ("run_id" in p.arguments && receipt.run_id !== p.arguments.run_id)
    )
      throw new Error(
        "Job receipt does not match this exact pending intent. Nothing was cleared or resent.",
      );
    // A view may close between the commit and its subsequent read. Persist
    // that read obligation before releasing the exact mutation journal.
    this.remember?.(receipt);
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
    return receipt;
  }

  async resolve(): Promise<JobReceipt | null> {
    if (!this.pending) return null;
    const receipt = await this.send("jobs.lookup", {
      mutation_key: this.pending.arguments.mutation_key,
      intent_sha256: this.pending.intent_sha256,
    });
    if (receipt.status === "absent") {
      this.absent = true;
      return null;
    }
    return this.accepted(receipt);
  }

  async begin(change: Change) {
    if (
      this.preparing ||
      this.pending ||
      localStorage.getItem(this.key) !== null
    )
      throw new Error("Resolve the previous job intent first.");
    if (!valid(change))
      throw new Error("Job fields do not satisfy the control contract.");
    // Freeze caller-owned nested editor state before asynchronous hashing.
    const exact: Change = JSON.parse(JSON.stringify(change));
    this.preparing = true;
    try {
      this.pending = { ...exact, intent_sha256: await digest(exact) };
      localStorage.setItem(this.key, JSON.stringify(this.pending));
      return await this.transmit();
    } finally {
      this.preparing = false;
    }
  }

  private async transmit() {
    const p = this.pending;
    if (!p) throw new Error("No pending job intent.");
    this.absent = false;
    return this.accepted(await this.send(p.operation, p.arguments as never));
  }

  async retry() {
    if (!this.absent)
      throw new Error(
        "Inspect the uncertain outcome before explicitly retrying.",
      );
    return this.transmit();
  }

  release() {
    if (!this.absent)
      throw new Error("Only a looked-up absent intent can be released.");
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
  }
}

export { identity };
