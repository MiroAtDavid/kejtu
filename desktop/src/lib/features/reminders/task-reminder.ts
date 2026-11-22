import { request } from "$lib/api/client";
import {
  isNativeRequest,
  PROTOCOL,
  type Arguments,
  type ReminderReceipt,
} from "$lib/api/wire.generated";

type Pending = { args: Arguments["reminders.save"]; digest: string };
function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, sorted(v)]),
    );
  return value;
}
export async function saveDigest(
  args: Arguments["reminders.save"],
): Promise<string> {
  const intent = {
    action: "save",
    reminder_id: "reminder_id" in args ? args.reminder_id : null,
    expected_revision:
      "expected_revision" in args ? args.expected_revision : null,
    reactivate: args.reactivate,
    definition: args.definition,
  };
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(sorted(intent))),
  );
  return [...new Uint8Array(hash)]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}

/** Save once; an uncertain result is resolved only by its frozen exact intent. */
export class ReminderSave {
  pending: Pending | null = null;
  absent = false;
  private preparing = false;
  constructor(
    readonly key: string,
    private send = request,
  ) {}
  async restore() {
    const raw = localStorage.getItem(this.key);
    if (!raw) return null;
    const p: Pending = JSON.parse(raw);
    if (
      !p ||
      !isNativeRequest({
        protocol: PROTOCOL,
        request_id: "0".repeat(32),
        operation: "reminders.save",
        arguments: p.args,
      }) ||
      (await saveDigest(p.args)) !== p.digest
    )
      throw new Error(
        "The saved reminder change could not be checked; it has been kept.",
      );
    this.pending = p;
    return this.resolve();
  }
  private accept(value: ReminderReceipt) {
    const p = this.pending;
    if (
      !p ||
      value.mutation_key !== p.args.mutation_key ||
      value.intent_sha256 !== p.digest ||
      localStorage.getItem(this.key) !== JSON.stringify(p)
    )
      throw new Error(
        "The reminder result could not be confirmed. Check its status.",
      );
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
    return value;
  }
  async begin(args: Arguments["reminders.save"]) {
    if (this.pending || this.preparing || localStorage.getItem(this.key))
      throw new Error("Check the previous reminder change first.");
    const exact = structuredClone(args);
    this.preparing = true;
    try {
      const pending = { args: exact, digest: await saveDigest(exact) };
      if (localStorage.getItem(this.key))
        throw new Error("Another reminder change is pending.");
      localStorage.setItem(this.key, JSON.stringify(pending));
      this.pending = pending;
      return this.accept(await this.send("reminders.save", exact));
    } finally {
      this.preparing = false;
    }
  }
  async resolve() {
    if (!this.pending) return null;
    const value = await this.send("reminders.lookup", {
      mutation_key: this.pending.args.mutation_key,
      intent_sha256: this.pending.digest,
    });
    if (value.status === "absent") {
      this.absent = true;
      return null;
    }
    return this.accept(value);
  }
  keep() {
    if (
      !this.pending ||
      !this.absent ||
      localStorage.getItem(this.key) !== JSON.stringify(this.pending)
    )
      throw new Error("Check status first.");
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
  }
}
