import { identity, request } from "$lib/api/client";
import { laterUntil } from "./home-attention";
import {
  isNativeRequest,
  PROTOCOL,
  type Arguments,
  type ReminderMessage,
  type ReminderAttention,
  type DeliveryNotice,
} from "$lib/api/wire.generated";

export type ChatNotice = {
  id: string;
  kind: "reminder" | "check_in";
  conversationId: string;
  afterSequence: string;
  text: string;
  at: string;
  pending: boolean;
  dismissed: boolean;
  available: boolean;
  reminder?: ReminderMessage;
  delivery?: DeliveryNotice;
};
type ReminderChange = Extract<
  Arguments["reminders.message"],
  { expected_revision: number }
>;
type Change =
  | { operation: "reminders.message"; arguments: ReminderChange }
  | {
      operation: "reminders.attend";
      arguments: Exclude<Arguments["reminders.attend"], { action: "lookup" }>;
    }
  | {
      operation: "deliveries.acknowledge";
      arguments: Arguments["deliveries.acknowledge"];
    };
type Saved = Change & { digest: string };

export async function intentDigest(change: Change): Promise<string> {
  const { mutation_key: _, ...args } = change.arguments;
  const value =
    change.operation === "deliveries.acknowledge"
      ? { action: "acknowledge", ...args }
      : args;
  const ordered = Object.fromEntries(
    Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
  );
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(ordered)),
  );
  return Array.from(new Uint8Array(hash), (x) =>
    x.toString(16).padStart(2, "0"),
  ).join("");
}

/** One frozen effect at a time; refresh/reopen only looks up uncertain effects. */
export class MessageJournal {
  pending: Saved | null = null;
  absent = false;
  readonly key: string;
  constructor(
    stateId: string,
    private send = request,
  ) {
    this.key = `kejtu-next.chat-delivery.v1.${stateId}`;
  }
  async restore() {
    const raw = localStorage.getItem(this.key);
    if (!raw) return;
    const p = JSON.parse(raw) as Saved;
    if (
      !p ||
      ![
        "reminders.message",
        "reminders.attend",
        "deliveries.acknowledge",
      ].includes(p.operation) ||
      !isNativeRequest({
        protocol: PROTOCOL,
        request_id: "0".repeat(32),
        operation: p.operation,
        arguments: p.arguments,
      }) ||
      !("mutation_key" in p.arguments) ||
      (await intentDigest(p)) !== p.digest
    )
      throw new Error(
        "A saved reminder action could not be checked. It has been kept.",
      );
    this.pending = p;
    await this.resolve();
  }
  private accept(value: unknown) {
    const p = this.pending;
    const v = value as {
      mutation_key?: string;
      intent_sha256?: string;
      id?: string;
      delivery_id?: string;
    };
    if (
      !p ||
      !v ||
      v.mutation_key !== p.arguments.mutation_key ||
      v.intent_sha256 !== p.digest ||
      (p.operation !== "deliveries.acknowledge"
        ? v.id !== p.arguments.id
        : v.delivery_id !== p.arguments.delivery_id) ||
      localStorage.getItem(this.key) !== JSON.stringify(p)
    )
      throw new Error(
        "The reminder action could not be confirmed. Check its status before trying again.",
      );
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
  }
  async resolve() {
    const p = this.pending;
    if (!p) return;
    const args = {
      mutation_key: p.arguments.mutation_key,
      intent_sha256: p.digest,
    };
    const value =
      p.operation === "reminders.message"
        ? await this.send("reminders.message", { action: "lookup", ...args })
        : p.operation === "reminders.attend"
          ? await this.send("reminders.attend", { action: "lookup", ...args })
          : await this.send("deliveries.lookup", args);
    if (value.status === "absent") {
      this.absent = true;
      return;
    }
    this.accept(value);
  }
  async begin(change: Change) {
    if (this.pending || localStorage.getItem(this.key))
      throw new Error("Check the previous reminder action first.");
    const digest = await intentDigest(change);
    if (this.pending || localStorage.getItem(this.key))
      throw new Error("Another reminder action is being saved.");
    this.pending = { ...change, digest };
    localStorage.setItem(this.key, JSON.stringify(this.pending));
    const value =
      change.operation === "reminders.message"
        ? await this.send(change.operation, change.arguments)
        : change.operation === "reminders.attend"
          ? await this.send(change.operation, change.arguments)
          : await this.send(change.operation, change.arguments);
    this.accept(value);
  }
  keepUnchanged() {
    if (
      !this.pending ||
      !this.absent ||
      localStorage.getItem(this.key) !== JSON.stringify(this.pending)
    )
      throw new Error("Check the action's status first.");
    localStorage.removeItem(this.key);
    this.pending = null;
    this.absent = false;
  }
}

export class ChatMessages {
  items: ChatNotice[] = [];
  unread: string[] = [];
  attention: ReminderAttention[] = [];
  attentionMore = false;
  private attentionPages = 1;
  error = "";
  // A successful list refresh cannot validate an unreadable saved mutation.
  private recoveryError = "";
  get recoveryStatus() {
    return (
      this.recoveryError ||
      (this.journal.pending
        ? "A previous reminder action needs to be checked."
        : "")
    );
  }
  acting = false;
  readonly journal: MessageJournal;
  private selected = "";
  private busy = false;
  private closed = false;
  private timer: ReturnType<typeof setInterval> | undefined;
  private pendingDeliveries: DeliveryNotice[] = [];
  private reminderCursor: string | null = null;
  private deliveryCursor: string | null = null;
  private recentIds = new Set<string>();
  private olderLoaded = false;
  more = false;
  constructor(
    stateId: string,
    private changed: () => void,
    private send = request,
  ) {
    this.journal = new MessageJournal(stateId, send);
  }
  async start() {
    try {
      await this.journal.restore();
    } catch (e) {
      this.error = this.recoveryError = String(e);
    }
    if (this.closed) return;
    await this.refresh();
    if (this.closed) return;
    this.timer = setInterval(() => void this.refresh(), 10000);
  }
  stop() {
    this.closed = true;
    clearInterval(this.timer);
  }
  select(cid: string) {
    if (this.selected === cid) return;
    this.selected = cid;
    this.items = [];
    this.recentIds.clear();
    this.olderLoaded = false;
    this.reminderCursor = this.deliveryCursor = null;
    this.changed();
    void this.refresh();
  }
  private reminder(m: ReminderMessage): ChatNotice {
    return {
      id: m.id,
      kind: "reminder",
      conversationId: m.conversation_id,
      afterSequence: m.after_sequence,
      text: m.text,
      at: m.due_at,
      pending: m.status === "pending" || m.status === "snoozed",
      dismissed: m.status === "dismissed",
      available: m.available,
      reminder: m,
    };
  }
  async refresh(older = false) {
    if (this.busy || this.closed) return;
    this.busy = true;
    const cid = this.selected;
    try {
      if (this.journal.pending && !this.journal.absent) {
        await this.journal.resolve();
        this.recoveryError = "";
      }
      const unread = new Set<string>();
      let cursor: string | null = null;
      const attention: ReminderAttention[] = [];
      let pages = 0;
      do {
        const page = await this.send("reminders.attention", {
          cursor,
          page_size: 20,
        });
        attention.push(...page.items);
        cursor = page.next_cursor;
        pages++;
      } while (
        cursor &&
        !this.closed &&
        (pages < this.attentionPages || !attention.length)
      );
      if (!this.closed) {
        this.attention = attention;
        this.attentionMore = !!cursor;
      }
      const pending = await this.send("deliveries.pending", { page_size: 5 });
      this.pendingDeliveries = pending.items.filter(
        (m) => m.kind === "check_in",
      );
      this.pendingDeliveries.forEach((m) => unread.add(m.conversation_id));
      if (!this.closed) this.unread = [...unread];
      if (!cid) return;
      const reminders =
        older && !this.reminderCursor
          ? { items: [], next_cursor: null }
          : await this.send("reminders.messages", {
              conversation_id: cid,
              cursor: older ? this.reminderCursor : null,
              page_size: 50,
            });
      const deliveries =
        older && !this.deliveryCursor
          ? { items: [], next_cursor: null }
          : await this.send("deliveries.messages", {
              conversation_id: cid,
              cursor: older ? this.deliveryCursor : null,
              page_size: 50,
            });
      const items: ChatNotice[] = reminders.items
        .filter((m) => !!m.presented_at || m.status === "dismissed")
        .map((m) => ({
          ...this.reminder(m),
          pending: false,
          available: m.available && m.status !== "snoozed",
          at: m.presented_at ?? m.due_at,
        }));
      // New reminders use conversation; check-ins keep their existing surface.
      for (const d of this.pendingDeliveries.filter(
        (d) => d.conversation_id === cid,
      ))
        items.push({
          id: d.id,
          kind: d.kind === "check_in" ? "check_in" : "reminder",
          conversationId: cid,
          afterSequence: d.after_sequence,
          text: d.text,
          at: d.created_at,
          pending: true,
          dismissed: false,
          available: true,
          delivery: d,
        });
      for (const d of deliveries.items.filter(
        (d) => d.status === "presented",
      )) {
        items.push({
          id: d.id,
          kind: d.kind === "check_in" ? "check_in" : "reminder",
          conversationId: cid,
          afterSequence: d.after_sequence,
          text: d.text,
          at: d.finished_at ?? d.created_at,
          pending: false,
          dismissed: false,
          available: false,
        });
      }
      if (this.closed || cid !== this.selected) return;
      const merged = new Map(
        (older
          ? this.items
          : this.items.filter((m) => !m.pending && !this.recentIds.has(m.id))
        ).map((m) => [m.id, m]),
      );
      items.forEach((m) => merged.set(m.id, m));
      this.items = [...merged.values()];
      if (!older) this.recentIds = new Set(items.map((m) => m.id));
      if (older || !this.olderLoaded) {
        this.reminderCursor = reminders.next_cursor;
        this.deliveryCursor = deliveries.next_cursor;
      }
      if (older) this.olderLoaded = true;
      this.more = !!this.reminderCursor || !!this.deliveryCursor;
      this.error =
        this.recoveryError ||
        (this.journal.pending
          ? "A previous reminder action needs to be checked."
          : "");
    } catch (e) {
      if (!this.closed) this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
      if (!this.closed) {
        this.changed();
        if (cid !== this.selected) void this.refresh();
      }
    }
  }
  async moreAttention() {
    this.attentionPages++;
    await this.refresh();
  }
  async attend(
    item: ReminderAttention,
    action: "acknowledge" | "later",
    until?: string,
  ) {
    if (this.journal.pending || this.acting)
      throw new Error("Check the previous reminder action first.");
    this.acting = true;
    this.changed();
    try {
      const common = {
        id: item.id,
        receipt: item.receipt,
        mutation_key: identity(),
      };
      await this.journal.begin({
        operation: "reminders.attend",
        arguments:
          action === "later"
            ? {
                action,
                ...common,
                until: until ?? laterUntil(new Date(), item.timezone),
              }
            : { action, ...common },
      });
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.acting = false;
      await this.refresh();
      this.changed();
    }
  }
  async act(item: ChatNotice, action: "present" | "later" | "dismiss") {
    if (this.journal.pending || this.acting)
      throw new Error("Check the previous reminder action first.");
    this.acting = true;
    this.changed();
    try {
      if (item.reminder) {
        const m = item.reminder;
        if (!m.receipt) return;
        await this.send("reminders.message", {
          action: "validate",
          id: m.id,
          receipt: m.receipt,
        });
        const common = {
          id: m.id,
          expected_revision: m.revision,
          receipt: m.receipt,
          mutation_key: identity(),
        };
        const args: ReminderChange =
          action === "later"
            ? {
                action,
                ...common,
                until: new Date(Date.now() + 30 * 60000).toISOString(),
              }
            : { action, ...common };
        await this.journal.begin({
          operation: "reminders.message",
          arguments: args,
        });
        // A snoozed message is absent from the next page until it is due again,
        // including when the action came from an older, already loaded page.
        if (action === "later")
          this.items = this.items.filter((m) => m.id !== item.id);
        if (action === "dismiss")
          this.items = this.items.map((m) =>
            m.id === item.id
              ? { ...m, dismissed: true, available: false, pending: false }
              : m,
          );
      } else if (item.delivery && action !== "later") {
        const d = item.delivery;
        await this.send("deliveries.validate", {
          delivery_id: d.id,
          receipt: d.receipt,
        });
        await this.journal.begin({
          operation: "deliveries.acknowledge",
          arguments: {
            delivery_id: d.id,
            receipt: d.receipt,
            channel: action === "present" ? "presented" : "dismissed",
            mutation_key: identity(),
          },
        });
      }
      await this.refresh();
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
      this.changed();
      throw e;
    } finally {
      this.acting = false;
      this.changed();
    }
  }
  async recover() {
    try {
      await this.journal.restore();
      this.recoveryError = "";
      await this.refresh();
    } catch (e) {
      this.error = this.recoveryError =
        e instanceof Error ? e.message : String(e);
    }
    this.changed();
  }
  keepUnchanged() {
    this.journal.keepUnchanged();
    this.recoveryError = "";
    this.error = "";
    this.changed();
    void this.refresh();
  }
}
