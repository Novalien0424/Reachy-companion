/**
 * JSON-RPC-over-WebSocket client for the *robot's* console (`/rpc` on :7860).
 *
 * Adapted from `reachy_companion/src/reachy_companion/static/js/api.js`, which
 * is the same client served from the robot itself. Two things differ, both
 * because this copy runs on the Mac and talks to a machine that is often not
 * there:
 *
 * * **The URL is not `location.host`.** It is built from `reachy_host` in
 *   `GET /api/config`, which arrives after this module loads — so the host is
 *   injected by `setRobotHost()` at boot and `connect()` refuses clearly until
 *   it has one, instead of dialling `ws://undefined`.
 *
 * * **Connection state is observable.** The robot is expected to be offline
 *   half the time (its app is not running, it is asleep, it is on another
 *   network). The Control view renders that state, so every transition is
 *   published through `onConnectionChange` rather than only surfacing as a
 *   rejected call.
 *
 * Reconnection deliberately diverges from the original. The console retries
 * only a socket that had once been open, at a flat 1 Hz; here a socket that
 * *never* opened is the normal case — the robot's app is usually not running —
 * and not retrying it means the live panel stays dead after the operator clicks
 * Start. So every close schedules a retry while anything is subscribed, with
 * the delay doubling from 1 s to a 15 s ceiling and resetting on a successful
 * open. Nothing here retries a *call*: a call that failed because the robot
 * went away should be re-clicked, not replayed.
 */

const DEFAULT_TIMEOUT_MS = 8000;
const ROBOT_RPC_PORT = 7860;

// Reconnect backoff. The console's original retried at a flat 1 Hz and only
// after a socket that had *once* been open — fine when the server is
// same-origin and effectively always up. Here the robot is off, asleep or
// running no app most of the time, so the never-opened case is the normal one
// and must retry too. It backs off to a 15 s ceiling so an absent robot is not
// dialled sixty times a minute for as long as the page is left open, and the
// delay resets on a successful open so a robot that comes back is picked up
// promptly the next time it drops.
const RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 15000;

export class RpcError extends Error {
  constructor(message, reason) {
    super(message || reason || "rpc error");
    this.reason = reason;
  }
}

let robotHost = "";
let socket = null;
let connecting = null;
let rpcCounter = 0;
let connectionState = "idle"; // idle | connecting | connected | disconnected | unconfigured
let reconnectDelay = RECONNECT_DELAY_MS;
let reconnectTimer = null;
const pending = new Map(); // id -> { resolve, reject, timer }
const subscribers = new Map(); // method -> Set<cb>
const connectionListeners = new Set();

/** Queue the next reconnect attempt, if anything is still listening for one. */
function scheduleReconnect() {
  // Subscribers are the liveness signal: a view that has gone away calls
  // `disconnect()`, which clears them, and no timer outlives it.
  if (reconnectTimer !== null || subscribers.size === 0) return;
  const delay = reconnectDelay;
  reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (subscribers.size === 0) return;
    // `connect()` reuses an in-flight attempt, so a manual call that raced this
    // timer does not open a second socket.
    connect().catch(() => {});
  }, delay);
}

/** Drop any queued reconnect and start the backoff over. */
function cancelReconnect() {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  reconnectDelay = RECONNECT_DELAY_MS;
}

/** Point the client at a robot. Called once at boot with `/api/config`'s host. */
export function setRobotHost(host) {
  robotHost = (host || "").trim();
  // A retry queued against the old address would dial the wrong machine.
  cancelReconnect();
  setConnectionState(robotHost ? "idle" : "unconfigured");
}

export function robotRpcUrl() {
  if (!robotHost) return null;
  // Always ws: — the robot's console is plain HTTP on the LAN, and this page is
  // served over http from localhost, so there is no mixed-content upgrade to make.
  return `ws://${robotHost}:${ROBOT_RPC_PORT}/rpc`;
}

export function connectionStatus() {
  return connectionState;
}

/** Observe connection transitions. Returns an unsubscribe fn; fires once immediately. */
export function onConnectionChange(cb) {
  connectionListeners.add(cb);
  try {
    cb(connectionState);
  } catch (error) {
    console.error("onConnectionChange callback threw:", error);
  }
  return () => connectionListeners.delete(cb);
}

function setConnectionState(next) {
  if (next === connectionState) return;
  connectionState = next;
  for (const cb of connectionListeners) {
    try {
      cb(next);
    } catch (error) {
      console.error("onConnectionChange callback threw:", error);
    }
  }
}

/** Open (or reuse) the shared socket. Resolves once OPEN, rejects on failure. */
function connect() {
  if (socket && socket.readyState === WebSocket.OPEN) return Promise.resolve();
  if (connecting) return connecting;

  const url = robotRpcUrl();
  if (!url) {
    setConnectionState("unconfigured");
    return Promise.reject(new RpcError("No robot host is configured; set REACHY_HOST in the repo .env.", "unconfigured"));
  }

  connecting = new Promise((resolve, reject) => {
    let opened = false;
    setConnectionState("connecting");
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (error) {
      connecting = null;
      setConnectionState("disconnected");
      reject(new RpcError(`Could not open ${url}: ${error?.message || error}`, "disconnected"));
      return;
    }
    socket = ws;
    ws.onopen = () => {
      opened = true;
      connecting = null;
      // A reachable robot starts the backoff over, so the *next* drop is
      // retried in a second rather than inheriting a ceiling-length delay.
      cancelReconnect();
      setConnectionState("connected");
      resolve();
    };
    ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        console.warn("Ignoring an unparseable /rpc frame:", error);
        return;
      }
      handleMessage(message);
    };
    ws.onclose = () => {
      // Only tear down module state if this is still *the* socket: a
      // `disconnect()` mid-connect can be followed by a fresh `connect()`, and
      // the old socket's close must not null out the new one's handle.
      if (socket !== ws) return;
      socket = null;
      connecting = null;
      setConnectionState("disconnected");
      for (const entry of pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(new RpcError("The connection to the robot closed.", "disconnected"));
      }
      pending.clear();
      // Fail this attempt's caller fast — an awaiting `rpcCall` must not hang
      // for the length of the backoff — and then retry anyway. The two are
      // independent: rejecting is about the call, retrying is about the
      // notification stream, and a socket that never opened needs the retry
      // most (the robot's app being down is the normal state here).
      if (!opened) reject(new RpcError(`Cannot reach the robot console at ${url}.`, "disconnected"));
      scheduleReconnect();
    };
  });
  return connecting;
}

function handleMessage(message) {
  if (message.id != null && ("result" in message || "error" in message)) {
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    clearTimeout(entry.timer);
    if (message.error) entry.reject(new RpcError(message.error.message, message.error.data?.reason));
    else entry.resolve(message.result);
    return;
  }
  if (typeof message.method === "string") {
    const callbacks = subscribers.get(message.method);
    if (!callbacks) return;
    for (const cb of callbacks) {
      try {
        cb(message.params || {});
      } catch (error) {
        console.error(`subscribe(${message.method}) callback threw:`, error);
      }
    }
  }
}

/** Call a JSON-RPC method on the robot and await its result. Rejects with RpcError. */
export async function rpcCall(method, params = {}, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  await connect();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new RpcError("Not connected to the robot.", "disconnected");
  }
  const id = `backend-${++rpcCounter}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new RpcError(`The robot did not answer ${method} in time.`, "timeout"));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
  });
}

/** Subscribe to a one-way notification. Returns an unsubscribe fn. */
export function subscribe(method, cb) {
  let set = subscribers.get(method);
  if (!set) {
    set = new Set();
    subscribers.set(method, set);
  }
  set.add(cb);
  connect().catch(() => {});
  return () => {
    const current = subscribers.get(method);
    if (!current) return;
    current.delete(cb);
    if (current.size === 0) subscribers.delete(method);
  };
}

/** Close the socket and forget every subscriber — used when the Control view unmounts. */
export function disconnect() {
  // Subscribers first: `scheduleReconnect` reads them, so clearing them here is
  // what guarantees the close below cannot start a loop nobody is watching.
  subscribers.clear();
  cancelReconnect();
  const open = socket;
  // Final review, F4: the `onclose` teardown below rejects `pending`, but its
  // `socket !== ws` guard sees the null this function is about to write and
  // bails — so every in-flight call used to sit on its 8 s timer after the view
  // had already gone away. Reject them here, where the intent is known, and the
  // guard stays correct for the case it exists for (a stale socket's close).
  for (const entry of pending.values()) {
    clearTimeout(entry.timer);
    entry.reject(new RpcError("The connection to the robot closed.", "disconnected"));
  }
  pending.clear();
  socket = null;
  connecting = null;
  if (open) open.close();
  setConnectionState(robotHost ? "idle" : "unconfigured");
}

// -- the robot's conversation methods -------------------------------------

export const conversationStatus = () => rpcCall("conversation.status");
export const say = (text) => rpcCall("conversation.say", { text });
export const interrupt = () => rpcCall("conversation.interrupt");
export const getMic = () => rpcCall("conversation.mic", {});
export const setMicMuted = (muted) => rpcCall("conversation.mic", { muted });

/** Copy for the reasons the robot's console reports; anything else is shown raw. */
const RPC_MESSAGES = Object.freeze({
  not_running: "The conversation app is not running on the robot. Start it above.",
  disconnected: "Not connected to the robot console.",
  timeout: "The robot did not answer in time.",
  unconfigured: "No robot host is configured; set REACHY_HOST in the repo .env.",
  invalid_params: "The robot rejected that request.",
});

export function describeRpcError(error) {
  return RPC_MESSAGES[error?.reason] || error?.message || String(error);
}
