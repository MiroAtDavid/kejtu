import { request } from "$lib/api/client";
import {
  isNativeRequest,
  PROTOCOL,
  type Arguments,
  type ReminderAccessResult,
} from "$lib/api/wire.generated";

export type Change = Extract<Arguments["reminders.access"], { action: "set" }>;
export type Status = Extract<ReminderAccessResult, { readable: boolean }>;
export type Receipt = Extract<ReminderAccessResult, { mutation_key: string }>;
type Pending = { arguments: Change; intent_sha256: string };

export async function digest(change: Change): Promise<string> {
  // Exactly the flat, ASCII-valued permission intent used by ReminderAccess.
  const intent = {
    action: change.action,
    conversation_id: change.conversation_id,
    enabled: change.enabled,
    expected_revision: change.expected_revision,
  };
  const bytes = new TextEncoder().encode(JSON.stringify(intent));
  const value = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(value)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Frozen conversation target; reopen looks up, never repeats, an uncertain grant. */
export class ReminderAccessJournal {
  pending: Pending | null = null;
  absent = false;
  private preparing = false;
  readonly key: string;

  constructor(
    stateId: string,
    readonly conversationId: string,
    private send = request,
  ) {
    this.key = `kejtu-next.reminder-access.v1.${stateId}.${conversationId}`;
  }

  private valid(args: unknown): args is Change {
    return (
      isNativeRequest({
        protocol: PROTOCOL,
        request_id: "0".repeat(32),
        operation: "reminders.access",
        arguments: args,
      }) &&
      (args as Change).action === "set" &&
      (args as Change).conversation_id === this.conversationId
    );
  }

  async read(): Promise<Status> {
    const value = await this.send("reminders.access", {
      action: "read",
      conversation_id: this.conversationId,
    });
    if (!("readable" in value) || value.conversation_id !== this.conversationId)
      throw new Error(
        "Reminder access response does not match this conversation.",
      );
    return value;
  }

  async restore(): Promise<Receipt | null> {
    const raw = localStorage.getItem(this.key);
    if (raw === null) return null;
    const saved: Pending = JSON.parse(raw);
    if (
      !saved ||
      !this.valid(saved.arguments) ||
      (await digest(saved.arguments)) !== saved.intent_sha256
    )
      throw new Error(
        "Reminder access recovery record is invalid. It was retained; no change was sent.",
      );
    this.pending = saved;
    return this.resolve();
  }

  private exactStored() {
    if (
      !this.pending ||
      localStorage.getItem(this.key) !== JSON.stringify(this.pending)
    )
      throw new Error(
        "Reminder access recovery record changed. Nothing was cleared or resent.",
      );
  }

  private accepted(value: ReminderAccessResult): Receipt {
    const p = this.pending;
    if (
      !p ||
      !("mutation_key" in value) ||
      value.mutation_key !== p.arguments.mutation_key ||
      value.intent_sha256 !== p.intent_sha256 ||
      value.conversation_id !== this.conversationId ||
      value.enabled !== p.arguments.enabled ||
      value.revision !==
        p.arguments.expected_revision + (value.status === "committed" ? 1 : 0)
    )
      throw new Error(
        "Reminder access receipt does not match the exact pending change. Nothing was cleared or resent.",
      );
    this.exactStored();
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
    return value;
  }

  async resolve(): Promise<Receipt | null> {
    if (!this.pending) return null;
    this.exactStored();
    const value = await this.send("reminders.access", {
      action: "lookup",
      conversation_id: this.conversationId,
      mutation_key: this.pending.arguments.mutation_key,
      intent_sha256: this.pending.intent_sha256,
    });
    if ("status" in value && value.status === "absent") {
      this.absent = true;
      return null;
    }
    return this.accepted(value);
  }

  async begin(args: Change): Promise<Receipt> {
    if (
      this.preparing ||
      this.pending ||
      localStorage.getItem(this.key) !== null
    )
      throw new Error("Resolve the previous reminder access change first.");
    if (!this.valid(args)) throw new Error("Invalid reminder access choice.");
    const exact = { ...args };
    this.preparing = true;
    try {
      const intent_sha256 = await digest(exact);
      if (localStorage.getItem(this.key) !== null)
        throw new Error("Another reminder access choice is pending.");
      const pending = { arguments: exact, intent_sha256 };
      localStorage.setItem(this.key, JSON.stringify(pending));
      this.pending = pending;
      return await this.transmit();
    } finally {
      this.preparing = false;
    }
  }

  private async transmit(): Promise<Receipt> {
    this.exactStored();
    this.absent = false;
    return this.accepted(
      await this.send("reminders.access", this.pending!.arguments),
    );
  }

  async retry(): Promise<Receipt> {
    if (!this.absent)
      throw new Error(
        "Look up the uncertain change before explicitly retrying.",
      );
    return this.transmit();
  }

  release() {
    if (!this.absent)
      throw new Error("Only a looked-up absent change can be released.");
    this.exactStored();
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
  }
}
