/**
 * REST client for this backend's own API (same origin, no auth — see README).
 *
 * Two shapes from the server drive everything here:
 *
 * * **One error envelope.** Every failure is `{"error": …, "kind": …}` with a
 *   status code, so one `ApiError` carrying `kind` is enough for callers to
 *   branch on, and `describeError` is the only place copy is chosen.
 *
 * * **A blocked push is not an error.** `POST /api/sync/push` answers 409 with
 *   the *push result* (`pushed:false` plus `blocked_by`) when the robot holds
 *   content the backend does not know. That is data the Sync view renders, so
 *   `pushSync` returns it instead of throwing — while a 409 that really is an
 *   error envelope (`sync_busy`) still throws. The two are told apart by
 *   whether the body carries `pushed`.
 */

export class ApiError extends Error {
  constructor(message, kind, status) {
    super(message || kind || `HTTP ${status}`);
    this.kind = kind || "unknown";
    this.status = status;
  }
}

/** Parse a response body as JSON, tolerating a server that answered with something else. */
async function readBody(response) {
  const raw = await response.text();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return { error: raw, kind: "unparseable_response" };
  }
}

async function send(method, path, { json, formData, signal } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      signal,
      headers: json === undefined ? undefined : { "content-type": "application/json" },
      body: json === undefined ? formData : JSON.stringify(json),
    });
  } catch (error) {
    // The backend is on this Mac; a fetch that never lands means it is not
    // running, which is worth saying plainly rather than as "Failed to fetch".
    if (error?.name === "AbortError") throw error;
    throw new ApiError(`Could not reach the backend at ${location.origin}. Is run.sh still running?`, "offline", 0);
  }
  return { status: response.status, ok: response.ok, body: await readBody(response) };
}

async function request(method, path, options = {}) {
  const { ok, status, body } = await send(method, path, options);
  if (!ok) throw new ApiError(body?.error, body?.kind, status);
  return body;
}

// -- config ---------------------------------------------------------------

export const getConfig = (signal) => request("GET", "/api/config", { signal });

// -- people ---------------------------------------------------------------

/** Every person. NOTE: the server answers with a bare array, not an envelope. */
export const listPeople = (signal) => request("GET", "/api/people", { signal });

export const createPerson = (name) => request("POST", "/api/people", { json: { name } });
export const renamePerson = (id, name) => request("PATCH", `/api/people/${encodeURIComponent(id)}`, { json: { name } });
export const deletePerson = (id) => request("DELETE", `/api/people/${encodeURIComponent(id)}`);

/**
 * Fold one person into another; `id` is the survivor and answers with them.
 *
 * 404 for an unknown id, 400 for merging someone into themselves, 409 when a
 * name the survivor would answer to already reaches somebody else.
 */
export const mergePerson = (id, sourceId) =>
  request("POST", `/api/people/${encodeURIComponent(id)}/merge`, { json: { source_id: sourceId } });

export const addFact = (id, text) =>
  request("POST", `/api/people/${encodeURIComponent(id)}/facts`, { json: { text } });
export const deleteFact = (id, factId) =>
  request("DELETE", `/api/people/${encodeURIComponent(id)}/facts/${encodeURIComponent(factId)}`);

export function uploadPhoto(id, file) {
  const form = new FormData();
  form.append("file", file, file.name || "photo.jpg");
  return request("POST", `/api/people/${encodeURIComponent(id)}/photos`, { formData: form });
}

export const deletePhoto = (id, photoId) =>
  request("DELETE", `/api/people/${encodeURIComponent(id)}/photos/${encodeURIComponent(photoId)}`);

/** The thumbnail URL. Synthetic photos have no bytes and 404 — never build one for those. */
export const photoFileUrl = (id, photoId) =>
  `/api/people/${encodeURIComponent(id)}/photos/${encodeURIComponent(photoId)}/file`;

// -- sync -----------------------------------------------------------------

export const getSyncStatus = (signal) => request("GET", "/api/sync/status", { signal });

/**
 * Push, returning the push result for both the 200 and the refused 409.
 *
 * A body carrying `pushed` is a push result whatever its status; anything else
 * at a non-2xx is the error envelope (`sync_busy`, `robot_unreachable`,
 * `robot_not_verified`) and throws like every other call.
 */
export async function pushSync() {
  const { ok, status, body } = await send("POST", "/api/sync/push");
  if (body && typeof body === "object" && "pushed" in body) return body;
  if (!ok) throw new ApiError(body?.error, body?.kind, status);
  return body;
}

export const previewImport = (signal) => request("GET", "/api/sync/import", { signal });
export const applyImport = () => request("POST", "/api/sync/import");

// -- the robot's app lifecycle --------------------------------------------

export const getRobotStatus = (signal) => request("GET", "/api/robot/status", { signal });
export const startRobotApp = () => request("POST", "/api/robot/start");
export const stopRobotApp = () => request("POST", "/api/robot/stop");
export const restartRobotApp = () => request("POST", "/api/robot/restart");

// -- copy -----------------------------------------------------------------

/** Kinds whose server message is not something to put in front of an operator. */
const MESSAGE_BY_KIND = Object.freeze({
  duplicate_name: "Someone with that name already exists.",
  empty_value: "That value is empty once normalized — try something with letters in it.",
  not_found: "That is gone; reload the page.",
  invalid_request: "The backend rejected that request as malformed.",
  invalid_merge: "A person cannot be merged into themselves.",
  sync_busy: "Another push or import is still running. Wait for it to finish.",
  offline: null, // its own message is already the right one
});

export function describeError(error) {
  if (!(error instanceof ApiError)) return error?.message || String(error);
  return MESSAGE_BY_KIND[error.kind] || error.message;
}

/** The per-photo status an operator reads: embedded, the failure, or why there is neither.
 *
 * `display_only` comes first: an imported enrollment snapshot has a picture and
 * no embedding, which without the label reads as an upload that failed silently.
 */
export function photoStatus(photo) {
  if (photo.display_only) return "robot snapshot — display only";
  if (photo.synthetic) return "synthetic — no image";
  if (photo.error) return photo.error;
  if (photo.has_embedding) return "embedded";
  return "not embedded";
}

/** Which of the three status tones a photo is in. */
export function photoTone(photo) {
  if (photo.display_only || photo.synthetic) return "muted";
  if (photo.error) return "error";
  return photo.has_embedding ? "ok" : "warn";
}
