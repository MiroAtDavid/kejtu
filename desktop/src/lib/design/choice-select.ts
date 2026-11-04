import { mount, tick, unmount } from "svelte";
import { writable } from "svelte/store";
import ChoiceSelect from "./ChoiceSelect.svelte";

export type SelectPresentation = {
  id: string;
  label: string;
  value: string;
  options: {
    value: string;
    label: string;
    detail?: string;
    disabled: boolean;
  }[];
  disabled: boolean;
  required: boolean;
  description: string;
  validation: string;
};

/** Enhance an existing single select with the shared picker. The original
 * select remains the form/value owner, so Svelte's typed option bindings,
 * change handlers, fieldset locks and validation retain their native behavior.
 * Pass its value as the action parameter to resync after owner-driven changes. */
export function choiceSelect(node: HTMLSelectElement, _value: unknown) {
  if (node.multiple || node.size > 1) return {};
  const id = `choice-select-${crypto.randomUUID()}`;
  const host = document.createElement("span");
  host.className = "choice-select-host";
  host.style.display = "block";
  host.style.minWidth = "0";
  node.after(host);
  const original = {
    display: node.style.display,
    displayPriority: node.style.getPropertyPriority("display"),
    hidden: node.getAttribute("aria-hidden"),
    tabIndex: node.getAttribute("tabindex"),
  };
  let destroyed = false;
  let queued = false;
  let validation = "";
  function read(): SelectPresentation {
    const label =
      node.getAttribute("aria-label") ||
      Array.from(node.labels || [])
        .map((label) => {
          const copy = label.cloneNode(true) as HTMLElement;
          copy
            .querySelectorAll("select, .choice-select-host")
            .forEach((child) => child.remove());
          return copy.textContent?.trim() || "";
        })
        .join(" ");
    if (node.validity.valid) validation = "";
    return {
      id,
      label,
      value: node.value,
      options: Array.from(node.options).map((option) => {
        const group =
          option.parentElement instanceof HTMLOptGroupElement
            ? option.parentElement
            : null;
        return {
          value: option.value,
          label: option.label,
          detail: group?.label,
          disabled: option.disabled || !!group?.disabled,
        };
      }),
      disabled: node.matches(":disabled"),
      required: node.required,
      description: node.getAttribute("aria-describedby") || "",
      validation,
    };
  }
  // Svelte installs the select's initial bindings/disabled state during mount.
  // Keep the replacement locked until those native properties are settled.
  const state = writable({ ...read(), disabled: true });
  function sync() {
    if (!destroyed) state.set(read());
  }
  function schedule() {
    if (queued || destroyed) return;
    queued = true;
    void tick().then(() => {
      queued = false;
      sync();
    });
  }
  function focus() {
    host
      .querySelector<HTMLButtonElement>(".choice-trigger")
      ?.focus({ preventScroll: true });
  }
  function invalid(event: Event) {
    // Hidden native controls cannot receive the browser's validation focus.
    // Show the same message at the visible control and focus it instead.
    event.preventDefault();
    validation = node.validationMessage;
    sync();
    void tick().then(() => {
      if (!destroyed) focus();
    });
  }
  function choose(value: string) {
    if (node.matches(":disabled")) return;
    const option = Array.from(node.options).find(
      (option) => option.value === value,
    );
    if (
      !option ||
      option.disabled ||
      (option.parentElement instanceof HTMLOptGroupElement &&
        option.parentElement.disabled)
    )
      return;
    node.value = value;
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
    // Handlers may reject a choice (e.g. a failed exact-revision read). Re-read
    // the actual control; never retain the picker's optimistic value as truth.
    schedule();
  }
  const component = mount(ChoiceSelect, {
    target: host,
    props: { state, choose },
  });
  node.style.setProperty("display", "none", "important");
  node.setAttribute("aria-hidden", "true");
  node.tabIndex = -1;
  node.addEventListener("change", schedule);
  node.addEventListener("input", schedule);
  node.addEventListener("invalid", invalid);
  node.addEventListener("focus", focus);
  node.form?.addEventListener("reset", schedule);
  const labels = Array.from(node.labels || []);
  const labelClick = (event: MouseEvent) => {
    if (
      event.target instanceof Element &&
      !host.contains(event.target) &&
      !event.target.closest("button, a, input, select, textarea")
    ) {
      event.preventDefault();
      focus();
    }
  };
  labels.forEach((label) => label.addEventListener("click", labelClick));
  const observer = new MutationObserver(schedule);
  observer.observe(node, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [
      "disabled",
      "required",
      "value",
      "selected",
      "label",
      "aria-label",
      "aria-describedby",
    ],
  });
  // A fieldset's disabled state is inherited, not reflected in select.disabled.
  for (let parent = node.parentElement; parent; parent = parent.parentElement)
    if (parent instanceof HTMLFieldSetElement)
      observer.observe(parent, {
        attributes: true,
        attributeFilter: ["disabled"],
      });
  schedule();
  return {
    update(_value: unknown) {
      schedule();
    },
    destroy() {
      destroyed = true;
      observer.disconnect();
      node.removeEventListener("change", schedule);
      node.removeEventListener("input", schedule);
      node.removeEventListener("invalid", invalid);
      node.removeEventListener("focus", focus);
      node.form?.removeEventListener("reset", schedule);
      labels.forEach((label) => label.removeEventListener("click", labelClick));
      void unmount(component);
      host.remove();
      node.style.setProperty(
        "display",
        original.display,
        original.displayPriority,
      );
      for (const [name, value] of [
        ["aria-hidden", original.hidden],
        ["tabindex", original.tabIndex],
      ]) {
        if (value === null) node.removeAttribute(name!);
        else node.setAttribute(name!, value!);
      }
    },
  };
}
