/** People view: everyone this Mac knows, with create and delete. */

import {
  createPerson,
  deletePerson,
  describeError,
  listPeople,
  photoStatus,
  photoTone,
} from "../api.js";
import { $, count, empty, formatTime, h, panel, setStatus, statusLine } from "../ui.js";

export async function mountPeopleView({ outlet, signal, navigate }) {
  const status = statusLine();
  const list = h("div", { class: "cards" }, empty("Loading…"));

  const nameInput = h("input", {
    type: "text",
    class: "input",
    placeholder: "Name, as the robot should say it",
    "aria-label": "New person's name",
    autocomplete: "off",
  });
  const createButton = h("button", { type: "submit", class: "button button--primary" }, "Add person");
  const form = h(
    "form",
    { class: "inline-form", onSubmit: onCreate },
    nameInput,
    createButton
  );

  outlet.replaceChildren(
    h(
      "section",
      { class: "view" },
      h("h1", { class: "view__title" }, "People"),
      h(
        "p",
        { class: "view__subtitle" },
        "The durable copy. Photos and facts live here; the robot gets a projection of them on the Sync page."
      ),
      panel("Add someone", form, status),
      list
    )
  );

  await refresh();

  async function refresh() {
    let people;
    try {
      people = await listPeople(signal);
    } catch (error) {
      if (signal.aborted) return;
      list.replaceChildren(h("p", { class: "status is-error" }, describeError(error)));
      return;
    }
    if (signal.aborted) return;
    // The route is a BARE ARRAY, not an envelope.
    if (!Array.isArray(people) || people.length === 0) {
      list.replaceChildren(empty("Nobody yet. Add a person, then upload a photo of their face."));
      return;
    }
    list.replaceChildren(...people.map((person) => personCard(person, { navigate, onDeleted: refresh, status })));
  }

  async function onCreate(event) {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (!name) {
      setStatus(status, "Type a name first.", "warn");
      return;
    }
    createButton.disabled = true;
    setStatus(status, `Adding “${name}”…`);
    try {
      const person = await createPerson(name);
      if (signal.aborted) return;
      nameInput.value = "";
      setStatus(status, `Added “${person.name}”.`, "ok");
      await refresh();
      if (!signal.aborted) $(".input", form)?.focus();
    } catch (error) {
      if (signal.aborted) return;
      setStatus(status, describeError(error), "error");
    } finally {
      createButton.disabled = false;
    }
  }
}

function personCard(person, { navigate, onDeleted, status }) {
  const photos = person.photos || [];
  const facts = person.facts || [];
  const failures = photos.filter((photo) => photo.error);
  const embedded = photos.filter((photo) => photo.has_embedding);

  const open = () => navigate(`#/people/${encodeURIComponent(person.id)}`);

  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "card__body" },
      // textContent via h()'s text node: display names are stored verbatim.
      h("button", { type: "button", class: "card__name", onClick: open }, person.name),
      h(
        "p",
        { class: "card__meta" },
        `${count(photos.length, "photo")} · ${count(embedded.length, "embedding")} · ${count(facts.length, "fact")}`
      ),
      h(
        "p",
        { class: "card__meta muted" },
        person.face_id ? `linked to robot face ${person.face_id}` : "no robot face link yet"
      ),
      h("p", { class: "card__meta muted" }, `updated ${formatTime(person.updated_at)}`),
      failures.length > 0 &&
        h(
          "div",
          { class: "badges" },
          ...failures.map((photo) => h("span", { class: `badge badge--${photoTone(photo)}` }, photoStatus(photo)))
        )
    ),
    h(
      "div",
      { class: "card__actions" },
      h("button", { type: "button", class: "button", onClick: open }, "Open"),
      h(
        "button",
        {
          type: "button",
          class: "button button--danger",
          onClick: () => onDelete(person, { onDeleted, status }),
        },
        "Delete"
      )
    )
  );
}

async function onDelete(person, { onDeleted, status }) {
  const confirmed = window.confirm(
    `Delete ${person.name}? Their ${person.photos.length} photo(s) and ${person.facts.length} fact(s) go with them. ` +
      "The robot keeps its copy until the next push."
  );
  if (!confirmed) return;
  setStatus(status, `Deleting “${person.name}”…`);
  try {
    await deletePerson(person.id);
    setStatus(status, `Deleted “${person.name}”.`, "ok");
  } catch (error) {
    setStatus(status, describeError(error), "error");
    return;
  }
  await onDeleted();
}
