/**
 * Tiny DOM helpers, adapted from the robot console's own `static/js/ui.js`.
 *
 * One deliberate difference from that original: there is no `html:` escape
 * hatch here, and nothing in this UI ever assigns `innerHTML`. Every string
 * this app renders is either a person's `display_name`, a fact, a photo
 * filename, a robot transcript or an error message from the robot's own
 * stderr — all of them stored verbatim, none of them sanitized anywhere. Text
 * nodes are the whole defense, so the escape hatch is removed rather than left
 * for someone to reach for.
 */

/** Build one element. `attrs` keys: class, style, dataset, on<Event>, anything else -> attribute. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") {
      el.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : String(value);
    } else if (key === "style" && typeof value === "object") {
      Object.assign(el.style, value);
    } else if (key === "dataset" && typeof value === "object") {
      for (const [dk, dv] of Object.entries(value)) {
        if (dv != null) el.dataset[dk] = String(dv);
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      el.setAttribute(key, String(value));
    }
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false || child === true) continue;
    if (child instanceof Node) parent.appendChild(child);
    else parent.appendChild(document.createTextNode(String(child)));
  }
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

/** A labelled section with a heading — the one layout every view is built from. */
export function panel(title, ...children) {
  return h("section", { class: "panel" }, h("h2", { class: "panel__title" }, title), ...children);
}

/** A short definition row: label on the left, value on the right. */
export function row(label, value) {
  return h("div", { class: "row" }, h("span", { class: "row__label" }, label), h("span", { class: "row__value" }, value));
}

/** A status line that views write into. `tone` is "", "ok", "warn" or "error". */
export function statusLine() {
  return h("p", { class: "status", role: "status", "aria-live": "polite" });
}

export function setStatus(element, message, tone = "") {
  element.textContent = message || "";
  element.className = tone ? `status is-${tone}` : "status";
}

/** Render an epoch-millisecond timestamp the way an operator reads one. */
export function formatTime(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "never";
  return new Date(ms).toLocaleString();
}

/** "1 photo" / "3 photos" — plural without a library. */
export function count(n, singular, plural = `${singular}s`) {
  return `${n} ${n === 1 ? singular : plural}`;
}

/** Empty-state paragraph, so every list reads the same when it has nothing in it. */
export function empty(message) {
  return h("p", { class: "muted" }, message);
}
