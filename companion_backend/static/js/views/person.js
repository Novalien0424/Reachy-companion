/** Person view: one person's photos and facts. */

import {
  addFact,
  deleteFact,
  deletePhoto,
  describeError,
  listPeople,
  photoFileUrl,
  photoStatus,
  photoTone,
  renamePerson,
  uploadPhoto,
} from "../api.js";
import { count, empty, formatTime, h, panel, setStatus, statusLine } from "../ui.js";

// The robot's own cap (`memory.MAX_FACT_CHARS`). The server truncates past it
// rather than refusing, so the counter is a warning, not a validator.
const MAX_FACT_CHARS = 280;

const JPEG_QUALITY = 0.92;

/**
 * Re-encode an upload with its EXIF orientation baked into the pixels.
 *
 * A photo taken on a phone in portrait is stored landscape with an EXIF
 * `Orientation` tag telling viewers to rotate it. Browsers honour that tag;
 * `backend/embedding.py`'s ffmpeg decode — which is what the detector actually
 * sees — hands back the raw, unrotated pixels. So the operator sees an upright
 * face, YuNet sees a sideways one, and the photo comes back `no_face` for no
 * visible reason. `createImageBitmap` with `imageOrientation: "from-image"`
 * applies the tag, and drawing that onto a canvas produces pixels that need no
 * tag at all.
 *
 * Every failure path falls back to the original file: a photo the server can
 * still try to embed beats an upload that never happens.
 */
async function normalizeForUpload(file) {
  if (typeof createImageBitmap !== "function" || typeof document.createElement("canvas").toBlob !== "function") {
    return file;
  }
  let bitmap = null;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.drawImage(bitmap, 0, 0);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob) return file;
    return new File([blob], jpegName(file.name), { type: "image/jpeg" });
  } catch (error) {
    // HEIC on a browser that cannot decode it, an SVG, a corrupt file: let the
    // server say what is wrong with it rather than swallowing the upload here.
    console.warn("Could not re-encode", file.name, "— uploading it as-is.", error);
    return file;
  } finally {
    bitmap?.close?.();
  }
}

/** Keep the operator's filename recognizable while telling the truth about the format. */
function jpegName(name) {
  const base = (name || "photo").replace(/\.[^./\\]+$/, "");
  return `${base || "photo"}.jpg`;
}

export async function mountPersonView({ outlet, signal, params, navigate }) {
  const personId = params.id;
  let person = null;

  const status = statusLine();
  const title = h("h1", { class: "view__title" }, "…");
  const subtitle = h("p", { class: "view__subtitle" });
  const photoPanel = h("div");
  const factPanel = h("div");

  outlet.replaceChildren(
    h(
      "section",
      { class: "view" },
      h("button", { type: "button", class: "link-button", onClick: () => navigate("#/people") }, "← All people"),
      title,
      subtitle,
      status,
      photoPanel,
      factPanel
    )
  );

  await reload();

  async function reload() {
    let people;
    try {
      // There is no GET /api/people/{id}; the list route is the whole store and
      // is small by construction (the robot itself only keeps twelve).
      people = await listPeople(signal);
    } catch (error) {
      if (signal.aborted) return;
      setStatus(status, describeError(error), "error");
      return;
    }
    if (signal.aborted) return;
    person = (Array.isArray(people) ? people : []).find((entry) => entry.id === personId) || null;
    if (!person) {
      title.textContent = "Not found";
      subtitle.textContent = `No person with id ${personId}. They may have been deleted.`;
      photoPanel.replaceChildren();
      factPanel.replaceChildren();
      return;
    }
    title.textContent = person.name;
    subtitle.textContent =
      `${count(person.photos.length, "photo")} · ${count(person.facts.length, "fact")} · ` +
      `created ${formatTime(person.created_at)}` +
      (person.face_id ? ` · robot face ${person.face_id}` : "");
    photoPanel.replaceChildren(renderPhotos());
    factPanel.replaceChildren(renderFacts());
  }

  // -- name ---------------------------------------------------------------

  async function onRename(nextName) {
    if (!nextName || nextName === person.name) return;
    setStatus(status, `Renaming to “${nextName}”…`);
    try {
      await renamePerson(person.id, nextName);
    } catch (error) {
      setStatus(status, describeError(error), "error");
      return;
    }
    setStatus(status, "Renamed.", "ok");
    await reload();
  }

  // -- photos -------------------------------------------------------------

  function renderPhotos() {
    const fileInput = h("input", {
      type: "file",
      class: "input",
      accept: "image/*",
      multiple: "multiple",
      "aria-label": "Photos to upload",
      onChange: (event) => onUpload(event.target),
    });

    const grid =
      person.photos.length === 0
        ? empty("No photos yet. Upload a clear, front-on headshot — one face per photo.")
        : h("div", { class: "photo-grid" }, ...person.photos.map(photoTile));

    return panel(
      "Photos",
      h(
        "p",
        { class: "muted" },
        "Each photo is embedded on upload; only the embedding is ever pushed to the robot. " +
          "The robot keeps the newest three per person."
      ),
      fileInput,
      grid
    );
  }

  function photoTile(photo) {
    // A synthetic photo is an embedding imported from the robot: the file route
    // 404s for it, so no <img> is built at all rather than one that fails.
    const thumb = photo.synthetic
      ? h("div", { class: "photo__thumb photo__thumb--none" }, "no image")
      : h("img", {
          class: "photo__thumb",
          src: photoFileUrl(person.id, photo.id),
          alt: `Photo ${photo.display_name}`,
          loading: "lazy",
        });

    return h(
      "figure",
      { class: "photo" },
      thumb,
      h(
        "figcaption",
        { class: "photo__caption" },
        h("span", { class: "photo__name" }, photo.display_name),
        h("span", { class: `badge badge--${photoTone(photo)}` }, photoStatus(photo)),
        h("span", { class: "muted small" }, formatTime(photo.added_at))
      ),
      h(
        "button",
        {
          type: "button",
          class: "button button--danger button--small",
          onClick: () => onDeletePhoto(photo),
        },
        "Delete"
      )
    );
  }

  async function onUpload(input) {
    const files = Array.from(input.files || []);
    if (files.length === 0) return;
    input.disabled = true;
    let uploaded = 0;
    const problems = [];
    for (const [index, file] of files.entries()) {
      setStatus(status, `Uploading ${index + 1} of ${files.length}: ${file.name}…`);
      try {
        const prepared = await normalizeForUpload(file);
        const photo = await uploadPhoto(person.id, prepared);
        uploaded += 1;
        // A failed embedding is a 200 carrying `error` — it is data, not a failure.
        if (photo.error) problems.push(`${photo.display_name}: ${photo.error}`);
      } catch (error) {
        problems.push(`${file.name}: ${describeError(error)}`);
      }
      if (signal.aborted) return;
    }
    input.disabled = false;
    input.value = "";
    if (problems.length === 0) {
      setStatus(status, `Uploaded and embedded ${count(uploaded, "photo")}.`, "ok");
    } else {
      setStatus(status, `Uploaded ${count(uploaded, "photo")}. ${problems.join("; ")}`, "warn");
    }
    await reload();
  }

  async function onDeletePhoto(photo) {
    if (!window.confirm(`Delete ${photo.display_name}?`)) return;
    setStatus(status, "Deleting photo…");
    try {
      await deletePhoto(person.id, photo.id);
    } catch (error) {
      setStatus(status, describeError(error), "error");
      return;
    }
    setStatus(status, "Photo deleted.", "ok");
    await reload();
  }

  // -- facts --------------------------------------------------------------

  function renderFacts() {
    const textarea = h("textarea", {
      class: "input textarea",
      rows: "3",
      placeholder: "Something the robot should remember about this person",
      "aria-label": "New fact",
      onInput: () => syncCounter(),
    });
    const counter = h("span", { class: "counter" }, `0 / ${MAX_FACT_CHARS}`);
    const addButton = h("button", { type: "submit", class: "button button--primary" }, "Add fact");

    // No `maxlength`: the server truncates past the cap rather than refusing, so
    // an operator who pastes something long should *see* that it will be cut,
    // not have the tail silently swallowed by the input before they notice.
    function syncCounter() {
      const used = textarea.value.length;
      const over = used > MAX_FACT_CHARS;
      counter.textContent = over
        ? `${used} / ${MAX_FACT_CHARS} — will be truncated`
        : `${used} / ${MAX_FACT_CHARS}`;
      counter.className = over ? "counter is-full" : "counter";
    }

    const form = h(
      "form",
      {
        class: "stack",
        onSubmit: async (event) => {
          event.preventDefault();
          const text = textarea.value.trim();
          if (!text) {
            setStatus(status, "Type a fact first.", "warn");
            return;
          }
          addButton.disabled = true;
          setStatus(status, "Adding fact…");
          try {
            await addFact(person.id, text);
          } catch (error) {
            setStatus(status, describeError(error), "error");
            return;
          } finally {
            addButton.disabled = false;
          }
          textarea.value = "";
          setStatus(status, "Fact added.", "ok");
          await reload();
        },
      },
      textarea,
      h("div", { class: "inline-form" }, addButton, counter)
    );

    const list =
      person.facts.length === 0
        ? empty("No facts yet.")
        : h(
            "ul",
            { class: "fact-list" },
            ...person.facts.map((fact) =>
              h(
                "li",
                { class: "fact" },
                h("span", { class: "fact__text" }, fact.text),
                h("span", { class: "muted small" }, formatTime(fact.created_at)),
                h(
                  "button",
                  {
                    type: "button",
                    class: "button button--danger button--small",
                    onClick: () => onDeleteFact(fact),
                  },
                  "Forget"
                )
              )
            )
          );

    return panel("Facts", form, list);
  }

  async function onDeleteFact(fact) {
    if (!window.confirm(`Forget “${fact.text}”?`)) return;
    setStatus(status, "Forgetting…");
    try {
      await deleteFact(person.id, fact.id);
    } catch (error) {
      setStatus(status, describeError(error), "error");
      return;
    }
    setStatus(status, "Fact forgotten.", "ok");
    await reload();
  }

  // Rename lives on the title: click it, get a prompt. An operator renames
  // roughly never, and a dedicated form would out-weigh the photo grid.
  title.classList.add("view__title--editable");
  title.title = "Click to rename";
  title.addEventListener("click", () => {
    if (!person) return;
    const next = window.prompt("New name", person.name);
    if (next != null) void onRename(next.trim());
  });
}
