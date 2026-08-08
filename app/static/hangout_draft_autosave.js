(function () {
  const form = document.querySelector("[data-draft-autosave]");
  if (!form) return;

  const status = form.querySelector("[data-draft-autosave-status]");
  const setupButton = form.querySelector('button[name="action"][value="setup"]');
  const saveUrl = form.getAttribute("action") || window.location.pathname;
  const DEBOUNCE_MS = 450;
  let timer = null;
  let saveQueue = Promise.resolve(true);
  let submitting = false;
  let setupRequested = false;

  function setStatus(text, kind) {
    if (!status) return;
    status.textContent = text;
    status.classList.remove("is-saving", "is-saved", "is-error");
    if (kind) status.classList.add(kind);
  }

  function showSaved() {
    setStatus("", null);
    if (window.__hangoutToast && typeof window.__hangoutToast.show === "function") {
      window.__hangoutToast.show("Draft saved");
    }
  }

  async function saveDraft(body) {
    setStatus("Saving…", "is-saving");
    try {
      const response = await fetch(saveUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        body,
      });
      if (response.status !== 204) {
        let message = "Save failed";
        try {
          const data = await response.json();
          if (typeof data.detail === "string") message = data.detail;
        } catch (_) {
          /* The HTML redirect/error response has no useful autosave message. */
        }
        setStatus(message, "is-error");
        return false;
      }
      showSaved();
      return true;
    } catch (_) {
      setStatus("Save failed", "is-error");
      return false;
    }
  }

  function enqueueSave() {
    const body = new FormData(form);
    body.set("action", "draft");
    const next = saveQueue.catch(() => false).then(() => saveDraft(body));
    saveQueue = next;
    return next;
  }

  function queueSave(immediate) {
    if (submitting) return;
    window.clearTimeout(timer);
    if (immediate) {
      void enqueueSave();
      return;
    }
    timer = window.setTimeout(() => {
      timer = null;
      void enqueueSave();
    }, DEBOUNCE_MS);
  }

  function isPersistedControl(target) {
    return (
      target instanceof HTMLInputElement ||
      target instanceof HTMLSelectElement ||
      target instanceof HTMLTextAreaElement
    ) &&
      !!target.name &&
      target.type !== "submit" &&
      target.type !== "button";
  }

  form.addEventListener("input", (event) => {
    if (isPersistedControl(event.target)) queueSave(false);
  });

  form.addEventListener("change", (event) => {
    if (isPersistedControl(event.target)) queueSave(true);
  });

  form.addEventListener("invitees:changed", () => queueSave(true));

  form.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.defaultPrevented || event.isComposing) return;
    const target = event.target;
    if (target === setupButton || target instanceof HTMLTextAreaElement) return;
    if (target instanceof HTMLInputElement) {
      event.preventDefault();
      if (isPersistedControl(target)) queueSave(true);
    }
  });

  if (setupButton) {
    setupButton.addEventListener("click", () => {
      setupRequested = true;
      window.setTimeout(() => {
        setupRequested = false;
      }, 0);
    });
  }

  form.addEventListener("submit", async (event) => {
    if (submitting) return;
    if (!setupRequested) {
      event.preventDefault();
      queueSave(true);
      return;
    }

    event.preventDefault();
    setupRequested = false;
    submitting = true;
    window.clearTimeout(timer);
    if (setupButton) setupButton.disabled = true;

    const saved = await enqueueSave();
    if (!saved) {
      submitting = false;
      if (setupButton) setupButton.disabled = false;
      return;
    }

    if (setupButton) setupButton.disabled = false;
    if (typeof form.requestSubmit === "function" && setupButton) {
      form.requestSubmit(setupButton);
      return;
    }

    const action = document.createElement("input");
    action.type = "hidden";
    action.name = "action";
    action.value = "setup";
    form.append(action);
    form.submit();
  });
})();
