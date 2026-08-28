/**
 * Control view: the robot's app lifecycle (over this backend's REST proxy) and
 * a live panel spoken straight to the robot's own console (over `rpc.js`).
 *
 * The split is deliberate. Start/stop/restart go through the Mac backend
 * because they are the daemon's HTTP API and the backend already holds the
 * robot's address; say/interrupt/mic go direct over the WebSocket because they
 * are a live stream and proxying that through a REST hop would buy nothing.
 *
 * Both halves are expected to fail. The robot is asleep, or the app is not
 * running, or it is on another network — so every panel renders its own
 * unreachable state and neither one blanks the page.
 */

import { describeError, getRobotStatus, restartRobotApp, startRobotApp, stopRobotApp } from "../api.js";
import {
  conversationStatus,
  describeRpcError,
  disconnect,
  getMic,
  interrupt,
  onConnectionChange,
  robotRpcUrl,
  say,
  setMicMuted,
  subscribe,
} from "../rpc.js";
import { h, panel, row, setStatus, statusLine } from "../ui.js";

const MAX_TRANSCRIPT_LINES = 100;

const CONNECTION_COPY = Object.freeze({
  idle: ["not connected", "muted"],
  connecting: ["connecting…", "warn"],
  connected: ["connected", "ok"],
  disconnected: ["disconnected — retrying while this page is open", "error"],
  unconfigured: ["no REACHY_HOST configured", "error"],
});

export async function mountControlView({ outlet, signal }) {
  const appState = h("div", { class: "stack" }, h("p", { class: "muted" }, "Asking the daemon…"));
  const appStatus = statusLine();
  const liveStatus = statusLine();
  const connectionBadge = h("span", { class: "badge badge--muted" }, "not connected");
  const turnBadge = h("span", { class: "badge badge--muted" }, "—");
  const transcript = h("div", { class: "transcript" }, h("p", { class: "muted" }, "Nothing heard yet."));
  let transcriptEmpty = true;

  const sayInput = h("input", {
    type: "text",
    class: "input",
    placeholder: "Make the robot say this…",
    "aria-label": "Text for the robot to say",
    autocomplete: "off",
  });
  const micButton = h("button", { type: "button", class: "button", onClick: onToggleMic }, "Mic: unknown");
  let muted = null;

  outlet.replaceChildren(
    h(
      "section",
      { class: "view" },
      h("h1", { class: "view__title" }, "Control"),
      h(
        "p",
        { class: "view__subtitle" },
        "Lifecycle through this backend; the live panel talks to the robot's own console directly."
      ),
      panel(
        "App on the robot",
        appState,
        h(
          "div",
          { class: "inline-form" },
          lifecycleButton("Start", startRobotApp),
          lifecycleButton("Stop", stopRobotApp),
          lifecycleButton("Restart", restartRobotApp),
          h("button", { type: "button", class: "button", onClick: refreshAppStatus }, "Refresh")
        ),
        h("p", { class: "muted small" }, "A restart is how the robot picks up a freshly pushed store."),
        appStatus
      ),
      panel(
        "Live conversation",
        h("div", { class: "row" }, h("span", { class: "row__label" }, "Console"), connectionBadge),
        h("div", { class: "row" }, h("span", { class: "row__label" }, "Turn"), turnBadge),
        h(
          "div",
          { class: "inline-form" },
          micButton,
          h("button", { type: "button", class: "button", onClick: onInterrupt }, "Interrupt"),
          h("button", { type: "button", class: "button", onClick: onCheckStatus }, "Check status")
        ),
        h(
          "form",
          { class: "inline-form", onSubmit: onSay },
          sayInput,
          h("button", { type: "submit", class: "button button--primary" }, "Say")
        ),
        liveStatus,
        transcript
      )
    )
  );

  // -- app lifecycle ------------------------------------------------------

  function lifecycleButton(label, action) {
    return h(
      "button",
      {
        type: "button",
        class: "button",
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          setStatus(appStatus, `${label}…`);
          try {
            const answer = await action();
            if (signal.aborted) return;
            setStatus(appStatus, `${label}: ${summarize(answer)}`, "ok");
          } catch (error) {
            if (signal.aborted) return;
            setStatus(appStatus, describeError(error), "error");
            return;
          } finally {
            button.disabled = false;
          }
          await refreshAppStatus();
        },
      },
      label
    );
  }

  async function refreshAppStatus() {
    let answer;
    try {
      answer = await getRobotStatus(signal);
    } catch (error) {
      if (signal.aborted) return;
      appState.replaceChildren(
        h("span", { class: "badge badge--warn" }, "daemon unreachable"),
        h("pre", { class: "pre" }, describeError(error))
      );
      return;
    }
    if (signal.aborted) return;
    // The daemon's payload is its own shape and may gain fields; render whatever
    // came back rather than picking three keys that might not be there.
    const entries = Object.entries(answer || {});
    appState.replaceChildren(
      ...(entries.length === 0
        ? [h("p", { class: "muted" }, "The daemon answered with nothing.")]
        : entries.map(([key, value]) => row(key, format(value))))
    );
  }

  // Not awaited: an unreachable daemon takes the full ssh/HTTP timeout to say
  // so, and the live panel below must not sit unwired for twenty seconds
  // because of a lifecycle call that is independent of it.
  void refreshAppStatus();

  // -- the live panel -----------------------------------------------------

  // main.js points rpc.js at the robot before the router mounts anything, so by
  // here the URL either exists or there is no REACHY_HOST to build one from.
  const url = robotRpcUrl();
  if (url) liveStatus.title = url;
  else setStatus(liveStatus, "No REACHY_HOST in the repo .env, so there is no console to connect to.", "error");

  const unsubscribers = [
    onConnectionChange((state) => {
      const [text, tone] = CONNECTION_COPY[state] || [state, "muted"];
      connectionBadge.textContent = text;
      connectionBadge.className = `badge badge--${tone}`;
      // Now that a dead socket really does reconnect on its own, the things
      // read once at mount have to be re-read — otherwise a panel that has
      // recovered still shows "Mic: unknown" and the failure message from
      // whenever the robot was last down, which reads as "still dead".
      if (state === "connected") {
        setStatus(liveStatus, "");
        void syncMic();
      }
    }),
    subscribe("conversation.transcript", (params) => {
      const text = String(params.text || "").trim();
      if (!text) return;
      appendTranscript(params.role === "assistant" ? "robot" : String(params.role || "user"), text, params.final);
    }),
    subscribe("conversation.turn", (params) => {
      turnBadge.textContent = String(params.state || "—");
      turnBadge.className = "badge";
    }),
    subscribe("conversation.phase", (params) => {
      const phase = String(params.phase || "");
      const reason = String(params.reason || "");
      setStatus(liveStatus, reason ? `${phase} (${reason})` : phase);
    }),
  ];

  signal.addEventListener(
    "abort",
    () => {
      for (const off of unsubscribers) off();
      // Nothing else in this UI uses the socket, so leaving the view closes it
      // rather than leaving a reconnect loop running against an absent robot.
      disconnect();
    },
    { once: true }
  );

  void syncMic();

  function appendTranscript(role, text, final) {
    if (transcriptEmpty) {
      transcript.replaceChildren();
      transcriptEmpty = false;
    }
    // Text nodes only: this string came off the robot's microphone.
    transcript.appendChild(
      h(
        "p",
        { class: `line line--${role === "robot" ? "robot" : "user"}${final === false ? " is-partial" : ""}` },
        h("span", { class: "line__role" }, role),
        h("span", { class: "line__text" }, text)
      )
    );
    while (transcript.childElementCount > MAX_TRANSCRIPT_LINES) transcript.removeChild(transcript.firstElementChild);
    transcript.scrollTop = transcript.scrollHeight;
  }

  async function syncMic() {
    try {
      const state = await getMic();
      if (signal.aborted) return;
      muted = Boolean(state?.muted);
      micButton.textContent = muted ? "Mic: muted — unmute" : "Mic: live — mute";
    } catch (error) {
      if (signal.aborted) return;
      micButton.textContent = "Mic: unknown";
      setStatus(liveStatus, describeRpcError(error), "warn");
    }
  }

  async function onToggleMic() {
    micButton.disabled = true;
    try {
      const state = await setMicMuted(!(muted === true));
      if (signal.aborted) return;
      muted = Boolean(state?.muted);
      micButton.textContent = muted ? "Mic: muted — unmute" : "Mic: live — mute";
      setStatus(liveStatus, muted ? "Microphone muted." : "Microphone live.", "ok");
    } catch (error) {
      if (!signal.aborted) setStatus(liveStatus, describeRpcError(error), "error");
    } finally {
      micButton.disabled = false;
    }
  }

  async function onInterrupt() {
    try {
      await interrupt();
      if (!signal.aborted) setStatus(liveStatus, "Interrupted.", "ok");
    } catch (error) {
      if (!signal.aborted) setStatus(liveStatus, describeRpcError(error), "error");
    }
  }

  async function onSay(event) {
    event.preventDefault();
    const text = sayInput.value.trim();
    if (!text) {
      setStatus(liveStatus, "Type something for the robot to say.", "warn");
      return;
    }
    setStatus(liveStatus, "Sending…");
    try {
      await say(text);
      if (signal.aborted) return;
      sayInput.value = "";
      setStatus(liveStatus, "Said.", "ok");
    } catch (error) {
      if (!signal.aborted) setStatus(liveStatus, describeRpcError(error), "error");
    }
  }

  async function onCheckStatus() {
    setStatus(liveStatus, "Asking the console…");
    try {
      const answer = await conversationStatus();
      if (signal.aborted) return;
      setStatus(liveStatus, summarize(answer), "ok");
      void syncMic();
    } catch (error) {
      if (!signal.aborted) setStatus(liveStatus, describeRpcError(error), "error");
    }
  }
}

/** One-line rendering of a daemon or console payload, for a status line. */
function summarize(payload) {
  if (payload == null) return "no answer";
  if (typeof payload !== "object") return String(payload);
  const entries = Object.entries(payload);
  if (entries.length === 0) return "ok";
  return entries.map(([key, value]) => `${key}=${format(value)}`).join(", ");
}

function format(value) {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
