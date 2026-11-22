import { get, writable } from "svelte/store";
import type { KejtuGesture } from "./kejtu-state";
import { chatAppearance } from "./theme";

/**
 * Kejtu beside the wordmark is the one that is always there. It says hello on
 * the first Home visit of a day; Home owns what "today" is (its timezone).
 */
const GREETED_KEY = "kejtu-next.companion.greeted.v1";
let greeted = "";

/** The latest move for the sidebar mark; `at` tells repeats apart. */
export const companionGesture = writable<{
  gesture: KejtuGesture;
  at: number;
} | null>(null);

/** Hello once per `day`. Returns whether it greeted. */
export function greetDay(day: string): boolean {
  if (!day || !get(chatAppearance).rotateKejtu) return false;
  // This window's memory counts on its own: a save that failed must not
  // make the next visit greet again.
  if (greeted === day) return false;
  try {
    if (window.localStorage.getItem(GREETED_KEY) === day) {
      greeted = day;
      return false;
    }
  } catch {
    // Without storage the day is still remembered for this window.
  }
  greeted = day;
  try {
    window.localStorage.setItem(GREETED_KEY, day);
  } catch {
    // As above: once per day for this window at least.
  }
  companionGesture.set({ gesture: "hello", at: Date.now() });
  return true;
}

/**
 * A double click spins Kejtu. Single clicks do nothing. Pointer only and
 * purely for fun, so it adds no focus stop; `null` turns it off.
 */
export function spinOnDoubleClick(
  node: HTMLElement,
  play: ((gesture: KejtuGesture) => unknown) | null,
) {
  let current = play;
  const twice = () => current?.("spin");
  // A double click would also select the nearest word (the wordmark).
  const press = (event: MouseEvent) => {
    if (current && event.detail > 1) event.preventDefault();
  };
  node.addEventListener("dblclick", twice);
  node.addEventListener("mousedown", press);
  return {
    update(next: typeof play) {
      current = next;
    },
    destroy() {
      node.removeEventListener("dblclick", twice);
      node.removeEventListener("mousedown", press);
    },
  };
}
