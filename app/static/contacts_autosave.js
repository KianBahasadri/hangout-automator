(function () {
  const ENUM_FIELDS = new Set(["drinks", "smokes", "drive"]);
  const TEXT_FIELDS = new Set(["name", "phone"]);

  function setStatus(editor, text, kind) {
    const el = editor.querySelector(".save-status");
    if (!el) return;
    el.textContent = text;
    el.classList.remove("is-saving", "is-saved", "is-error");
    if (kind) el.classList.add(kind);
  }

  function optionalValue(raw) {
    const v = (raw || "").trim();
    return v === "" ? null : v;
  }

  function collectTagIds(editor) {
    return Array.from(editor.querySelectorAll('[data-field="tag_ids"] input[type="checkbox"]:checked')).map(
      (cb) => Number(cb.value)
    );
  }

  function collectAllergyIds(editor) {
    return Array.from(editor.querySelectorAll('[data-field="allergy_ids"] input[type="checkbox"]:checked')).map(
      (cb) => Number(cb.value)
    );
  }

  async function patchContact(editor, body) {
    const id = editor.getAttribute("data-contact-id");
    setStatus(editor, "Saving…", "is-saving");
    try {
      const res = await fetch(`/api/contacts/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = "Save failed";
        try {
          const data = await res.json();
          if (typeof data.detail === "string") detail = data.detail;
          else if (Array.isArray(data.detail) && data.detail[0]?.msg) detail = data.detail[0].msg;
        } catch (_) {
          /* ignore */
        }
        setStatus(editor, detail, "is-error");
        return false;
      }
      const saved = await res.json();
      syncDataset(editor, saved);
      setStatus(editor, "Saved", "is-saved");
      window.clearTimeout(editor._savedClear);
      editor._savedClear = window.setTimeout(() => {
        if (editor.querySelector(".save-status")?.textContent === "Saved") {
          setStatus(editor, "", null);
        }
      }, 1600);
      return true;
    } catch (_) {
      setStatus(editor, "Save failed", "is-error");
      return false;
    }
  }

  function syncDataset(editor, saved) {
    if (!saved) return;
    if (saved.name != null) editor.dataset.name = String(saved.name).toLowerCase();
    if (saved.phone != null) {
      editor.dataset.phone = String(saved.phone);
      const phoneInput = editor.querySelector('[data-field="phone"]');
      if (phoneInput && window.HangoutPhone) {
        phoneInput.value = window.HangoutPhone.format(saved.phone);
      }
    }
    editor.dataset.drinks = saved.drinks || "";
    editor.dataset.smokes = saved.smokes || "";
    editor.dataset.drive = saved.drive || "";
    editor.dataset.allergies = Array.isArray(saved.allergies) && saved.allergies.length ? "1" : "0";
    if (Array.isArray(saved.tags)) {
      editor.dataset.tags = saved.tags.map((t) => t.id).join(",");
      editor.dataset.tagNames = saved.tags.map((t) => String(t.name || "").toLowerCase()).join(" ");
    }
    editor.dispatchEvent(new Event("contacts:data-changed", { bubbles: true }));
  }

  function queueSave(editor, body, immediate) {
    editor._pending = Object.assign({}, editor._pending || {}, body);
    window.clearTimeout(editor._timer);
    const run = () => {
      const payload = editor._pending;
      editor._pending = null;
      if (!payload || !Object.keys(payload).length) return;
      patchContact(editor, payload);
    };
    if (immediate) run();
    else editor._timer = window.setTimeout(run, 450);
  }

  function bindEditor(editor) {
    editor.querySelectorAll("[data-field]").forEach((el) => {
      const field = el.getAttribute("data-field");
      if (!field) return;

      if (field === "tag_ids") {
        el.addEventListener("change", () => {
          queueSave(editor, { tag_ids: collectTagIds(editor) }, true);
        });
        return;
      }

      if (field === "allergy_ids") {
        el.addEventListener("change", () => {
          queueSave(editor, { allergy_ids: collectAllergyIds(editor) }, true);
        });
        return;
      }

      if (el.tagName === "SELECT" || ENUM_FIELDS.has(field)) {
        el.addEventListener("change", () => {
          queueSave(editor, { [field]: optionalValue(el.value) }, true);
        });
        return;
      }

      if (TEXT_FIELDS.has(field)) {
        el.addEventListener("input", () => {
          queueSave(editor, { [field]: el.value }, false);
        });
        el.addEventListener("change", () => {
          queueSave(editor, { [field]: el.value }, true);
        });
      }
    });
  }

  document.querySelectorAll(".profile-editor").forEach(bindEditor);
})();
