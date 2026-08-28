/**
 * Sync view: drift, the guarded push, and the import that unblocks it.
 *
 * The one thing this page has to get across is *why* a push was refused and
 * what the operator does next. So a 409 does not render as "blocked" — it
 * renders the diff the server sent, item by item, with removals called out as
 * loudly as additions (a fact the robot was told to forget is the one category
 * where pushing anyway would silently undo a person's instruction), and an
 * "Import first" button wired to the very import that clears it.
 */

import { applyImport, describeError, getSyncStatus, previewImport, pushSync } from "../api.js";
import { count, empty, formatTime, h, panel, row, setStatus, statusLine } from "../ui.js";

export async function mountSyncView({ outlet, signal }) {
  const statusPanel = h("div", { class: "stack" }, h("p", { class: "muted" }, "Checking the robot…"));
  const pushStatus = statusLine();
  const pushResult = h("div", { class: "stack" });
  const importStatus = statusLine();
  const importResult = h("div", { class: "stack" });

  const pushButton = h("button", { type: "button", class: "button button--primary", onClick: onPush }, "Push to robot");
  const previewButton = h("button", { type: "button", class: "button", onClick: onPreview }, "Preview import");
  const applyButton = h("button", { type: "button", class: "button button--primary", onClick: onApply }, "Apply import");

  const importSection = panel(
    "Import from robot",
    h(
      "p",
      { class: "muted" },
      "Brings the robot's own writes back — faces it enrolled by voice, facts it was told, and facts it was " +
        "told to forget. This is always the way out of a refused push."
    ),
    h("div", { class: "inline-form" }, previewButton, applyButton),
    importStatus,
    importResult
  );

  outlet.replaceChildren(
    h(
      "section",
      { class: "view" },
      h("h1", { class: "view__title" }, "Sync"),
      h(
        "p",
        { class: "view__subtitle" },
        "This Mac is the source of truth; the robot's two store files are a projection of it. " +
          "A push overwrites them wholesale, so it is refused whenever the robot holds something this store " +
          "does not already know."
      ),
      panel("Robot state", statusPanel),
      panel(
        "Push",
        h(
          "p",
          { class: "muted" },
          "Writes faces.v1.json and people.v1.json to the robot, then reads them back to verify. " +
            "Restart the app on the Control page for the robot to pick them up."
        ),
        h("div", { class: "inline-form" }, pushButton),
        pushStatus,
        pushResult
      ),
      importSection
    )
  );

  await refreshStatus();

  async function refreshStatus() {
    let state;
    try {
      state = await getSyncStatus(signal);
    } catch (error) {
      if (signal.aborted) return;
      statusPanel.replaceChildren(h("p", { class: "status is-error" }, describeError(error)));
      return;
    }
    if (signal.aborted) return;
    statusPanel.replaceChildren(...renderStatus(state));
  }

  function renderStatus(state) {
    const parts = [row("Last verified push", formatTime(state.last_push_at))];
    if (!state.robot_reachable) {
      // Deliberately not an error banner for the *page*: an unreachable robot
      // is a normal state here (it is asleep, or on another network), and the
      // people and photos above it are still perfectly usable.
      parts.push(row("Robot", h("span", { class: "badge badge--warn" }, "unreachable")));
      if (state.error) parts.push(h("pre", { class: "pre" }, state.error));
      parts.push(h("p", { class: "muted" }, "Push and import need the robot; the People pages do not."));
      return parts;
    }
    parts.push(row("Robot", h("span", { class: "badge badge--ok" }, "reachable")));
    const drift = state.drift || {};
    const changes = [
      drift.never_pushed && "never pushed from this store",
      drift.faces_changed && "faces.v1.json changed",
      drift.people_changed && "people.v1.json changed",
    ].filter(Boolean);
    parts.push(
      row(
        "Drift",
        changes.length === 0
          ? h("span", { class: "badge badge--ok" }, "in sync")
          : h("span", { class: "badge badge--warn" }, changes.join(", "))
      )
    );
    return parts;
  }

  // -- push ---------------------------------------------------------------

  async function onPush() {
    pushButton.disabled = true;
    pushResult.replaceChildren();
    setStatus(pushStatus, "Pushing…");
    let result;
    try {
      result = await pushSync();
    } catch (error) {
      if (signal.aborted) return;
      setStatus(pushStatus, describeError(error), "error");
      return;
    } finally {
      pushButton.disabled = false;
    }
    if (signal.aborted) return;

    if (result.pushed) {
      setStatus(
        pushStatus,
        `Pushed ${count(result.faces_count, "face record")} and ${count(result.people_count, "person", "people")}.`,
        "ok"
      );
      if (result.skipped?.length) {
        // A skipped person reached the robot as nothing at all — no embedding
        // and no facts — so they would silently not exist there.
        pushResult.replaceChildren(
          h(
            "div",
            { class: "callout callout--warn" },
            h("h3", null, `${count(result.skipped.length, "person", "people")} reached the robot as nothing`),
            h("p", { class: "muted" }, "No embedded photo and no facts. Add a photo, then push again."),
            h("ul", { class: "plain-list" }, ...result.skipped.map((name) => h("li", null, name)))
          )
        );
      }
      await refreshStatus();
      return;
    }

    const blocked = result.blocked_by || {};
    if (blocked.kind === "robot_content") {
      setStatus(pushStatus, "Refused: the robot holds content this store does not know.", "warn");
      pushResult.replaceChildren(renderDiff(blocked.diff, { withImportAffordance: true }));
    } else if (blocked.kind === "race") {
      setStatus(pushStatus, `Refused: ${blocked.message} Nothing was written; push again.`, "warn");
    } else {
      setStatus(pushStatus, `Refused: ${blocked.message || "the backend gave no reason."}`, "warn");
    }
    await refreshStatus();
  }

  // -- import -------------------------------------------------------------

  async function onPreview() {
    previewButton.disabled = true;
    importResult.replaceChildren();
    setStatus(importStatus, "Reading the robot…");
    let preview;
    try {
      preview = await previewImport(signal);
    } catch (error) {
      if (signal.aborted) return;
      setStatus(importStatus, describeError(error), "error");
      return;
    } finally {
      previewButton.disabled = false;
    }
    if (signal.aborted) return;
    setStatus(
      importStatus,
      preview.diff.empty ? "Nothing to import — the robot holds nothing new." : "Preview only; nothing applied yet.",
      preview.diff.empty ? "ok" : ""
    );
    importResult.replaceChildren(renderDiff(preview.diff, { conflicts: preview.conflicts }));
  }

  async function onApply() {
    if (!window.confirm("Apply the robot's content to this Mac? Facts the robot forgot will be deleted here too.")) {
      return;
    }
    applyButton.disabled = true;
    importResult.replaceChildren();
    setStatus(importStatus, "Importing…");
    let outcome;
    try {
      outcome = await applyImport();
    } catch (error) {
      if (signal.aborted) return;
      setStatus(importStatus, describeError(error), "error");
      return;
    } finally {
      applyButton.disabled = false;
    }
    if (signal.aborted) return;
    setStatus(
      importStatus,
      `Imported ${count(outcome.applied, "item")}${outcome.conflicts.length ? " with conflicts." : "."}`,
      outcome.conflicts.length ? "warn" : "ok"
    );
    // The diff shown is the one the server re-fetched and applied, not the
    // preview — the robot may have enrolled a face in between.
    importResult.replaceChildren(renderDiff(outcome.diff, { conflicts: outcome.conflicts }));
    await refreshStatus();
  }

  /** Render one RobotDiff as a table of categories; every string is a text node. */
  function renderDiff(diff, { withImportAffordance = false, conflicts = [] } = {}) {
    const wrapper = h("div", { class: "stack" });
    if (!diff) return wrapper;

    const categories = [
      ["New faces on the robot", diff.new_faces, faceRow, "Enrolled by voice; importing creates or links a person."],
      ["Changed faces", diff.changed_faces, faceRow, "The robot has samples this store does not hold."],
      ["New facts", diff.new_person_facts, factsRow, "Told to the robot mid-conversation."],
      [
        "REMOVED facts",
        diff.removed_person_facts,
        factsRow,
        "Someone told the robot to forget these. Pushing without importing would write them back.",
      ],
    ];

    let anything = false;
    for (const [label, items, renderRow, note] of categories) {
      if (!items || items.length === 0) continue;
      anything = true;
      wrapper.appendChild(
        h(
          "div",
          { class: label.startsWith("REMOVED") ? "callout callout--danger" : "callout" },
          h("h3", null, `${label} (${items.length})`),
          h("p", { class: "muted small" }, note),
          h("table", { class: "table" }, h("tbody", null, ...items.map(renderRow)))
        )
      );
    }
    if (!anything) wrapper.appendChild(empty("Nothing on the robot that this store does not already have."));

    if (conflicts.length > 0) {
      wrapper.appendChild(
        h(
          "div",
          { class: "callout callout--warn" },
          h("h3", null, `Conflicts (${conflicts.length})`),
          h("p", { class: "muted small" }, "The import refused to guess at these; they need an operator decision."),
          h("ul", { class: "plain-list" }, ...conflicts.map((line) => h("li", null, line)))
        )
      );
    }

    if (withImportAffordance) {
      wrapper.appendChild(
        h(
          "div",
          { class: "callout" },
          h("h3", null, "Import first, then push"),
          h(
            "p",
            { class: "muted" },
            "An import copies all of the above into this store. The push then has nothing unknown left to overwrite."
          ),
          h(
            "button",
            {
              type: "button",
              class: "button button--primary",
              onClick: () => {
                importSection.scrollIntoView({ behavior: "smooth", block: "start" });
                void onPreview();
              },
            },
            "Import first"
          )
        )
      );
    }
    return wrapper;
  }
}

function faceRow(face) {
  return h(
    "tr",
    null,
    h("td", null, face.name),
    h("td", { class: "muted small" }, face.record_id),
    h("td", { class: "muted small" }, count(face.sample_count, "sample"))
  );
}

function factsRow(entry) {
  return h(
    "tr",
    null,
    h("td", null, entry.name),
    h("td", { class: "muted small" }, entry.face_id || "no face link"),
    h("td", null, h("ul", { class: "plain-list" }, ...entry.facts.map((text) => h("li", null, text))))
  );
}
