import { request } from "$lib/api/client";
import type { Arguments } from "$lib/api/wire.generated";

type Context = {
  conversation_id: string | null;
  draft: boolean;
  dialog: boolean;
};

/** One disposable native-session observer. Never persist idle state or authority. */
export class ActivityBridge {
  private observer = "";
  private sequence = 0;
  private stopped = false;
  private sending = false;
  private queued = false;
  private interacted = false;
  private timer: ReturnType<typeof setInterval> | undefined;
  private interaction = () => this.changed(true);
  private visibility = () => this.changed();

  constructor(
    private process: string,
    private context: () => Context,
    private report: (error: string) => void,
    private send = request,
  ) {}

  async start() {
    try {
      const attached = await this.send("background.attach", {
        process_instance_id: this.process,
      });
      this.observer = attached.observer_id;
      if (this.stopped) {
        this.detach();
        return;
      }
      for (const name of [
        "keydown",
        "pointerdown",
        "wheel",
        "touchstart",
        "kejtu-reader-interaction",
      ])
        window.addEventListener(name, this.interaction, {
          passive: true,
          capture: true,
        });
      window.addEventListener("focus", this.visibility);
      window.addEventListener("blur", this.visibility);
      document.addEventListener("visibilitychange", this.visibility);
      this.timer = setInterval(this.visibility, 5000);
      this.changed();
    } catch (caught) {
      if (!this.stopped) this.report(String(caught));
    }
  }

  changed(interacting = false) {
    if (this.stopped) return;
    this.queued = true;
    this.interacted ||= interacting;
    if (this.observer && !this.sending) void this.flush();
  }

  private async flush() {
    this.sending = true;
    let unacknowledgedInteraction = false;
    try {
      while (this.queued && !this.stopped) {
        this.queued = false;
        const values: Arguments["background.observe"] = {
          process_instance_id: this.process,
          observer_id: this.observer,
          sequence: ++this.sequence,
          ...this.context(),
          visible: document.visibilityState === "visible",
          focused: document.hasFocus(),
          interacting: this.interacted,
        };
        this.interacted = false;
        unacknowledgedInteraction = values.interacting;
        const result = await this.send("background.observe", values);
        if (
          result.observer_id !== this.observer ||
          result.sequence !== values.sequence ||
          !result.accepted
        )
          throw new Error(
            "Activity acknowledgement does not match this session.",
          );
        unacknowledgedInteraction = false;
        if (!this.stopped) this.report("");
      }
    } catch (caught) {
      this.interacted ||= unacknowledgedInteraction;
      // No speculative idle renewal. A stopped/failed bridge expires in the
      // backend; the next heartbeat may recover transport, never a stale token.
      if (!this.stopped) this.report(String(caught));
    } finally {
      this.sending = false;
    }
  }

  private detach() {
    if (!this.observer) return;
    void this.send("background.detach", {
      process_instance_id: this.process,
      observer_id: this.observer,
      sequence: ++this.sequence,
    }).catch(() => {});
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    clearInterval(this.timer);
    for (const name of [
      "keydown",
      "pointerdown",
      "wheel",
      "touchstart",
      "kejtu-reader-interaction",
    ])
      window.removeEventListener(name, this.interaction, true);
    window.removeEventListener("focus", this.visibility);
    window.removeEventListener("blur", this.visibility);
    document.removeEventListener("visibilitychange", this.visibility);
    this.detach();
  }
}
