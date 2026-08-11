(function () {
  const root = document.querySelector("[data-sms-simulator]");
  const form = root && root.querySelector("[data-sms-sim-form]");
  const statusEl = root && root.querySelector("[data-sms-sim-status]");
  if (!root || !form) return;

  const DEBOUNCE_MS = 400;
  let timer = null;
  let requestId = 0;

  function fieldValue(name) {
    const el = form.elements.namedItem(name);
    if (!el) return "";
    if (el instanceof RadioNodeList) {
      return (el.value || "").trim();
    }
    return (el.value || "").trim();
  }

  function firstSelectedInviteeName() {
    const checked = form.querySelectorAll(
      'input[name="contact_ids"]:checked, input[name="profile_ids"]:checked'
    );
    for (const input of checked) {
      const row = input.closest(".invitee-row");
      const nameEl = row && row.querySelector(".invitee-name");
      if (nameEl && nameEl.textContent.trim()) {
        return nameEl.textContent.trim();
      }
    }
    return "Alex";
  }

  function selectedContactIds() {
    const checked = form.querySelectorAll(
      'input[name="contact_ids"]:checked, input[name="profile_ids"]:checked'
    );
    const ids = [];
    checked.forEach(function (input) {
      const n = parseInt(input.value, 10);
      if (!Number.isNaN(n)) ids.push(n);
    });
    return ids;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
  }

  function applyMessages(messages) {
    if (!Array.isArray(messages)) return;
    messages.forEach(function (msg) {
      if (!msg || !msg.key) return;
      const card = root.querySelector('[data-sms-key="' + msg.key + '"]');
      if (!card) return;
      const bodyEl = card.querySelector("[data-sms-body]");
      if (bodyEl) bodyEl.textContent = msg.body || "";
      if (msg.title) {
        const titleEl = card.querySelector(".sms-sim-title");
        if (titleEl) titleEl.textContent = msg.title;
      }
      if (msg.description) {
        const descEl = card.querySelector(".sms-sim-desc");
        if (descEl) descEl.textContent = msg.description;
      }
    });
  }

  async function refresh() {
    const id = ++requestId;
    setStatus("Updating previews…");
    const payload = {
      recipient_name: firstSelectedInviteeName(),
      day_date: fieldValue("day_date") || null,
      time: fieldValue("time") || null,
      duration: fieldValue("duration") || null,
      location: fieldValue("location") || null,
      motive: fieldValue("motive") || null,
      alcohol_involved: fieldValue("alcohol_involved") || null,
      weed_involved: fieldValue("weed_involved") || null,
      notes: fieldValue("notes") || null,
      contact_ids: selectedContactIds(),
    };

    try {
      const response = await fetch("/api/sms/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error("Preview failed (" + response.status + ")");
      }
      const data = await response.json();
      if (id !== requestId) return;
      applyMessages(data.messages || []);
      setStatus("");
    } catch (err) {
      if (id !== requestId) return;
      setStatus(
        "Could not update previews. " + (err && err.message ? err.message : "")
      );
    }
  }

  function scheduleRefresh() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(refresh, DEBOUNCE_MS);
  }

  form.addEventListener("input", scheduleRefresh);
  form.addEventListener("change", scheduleRefresh);

  form.addEventListener("submit", function (event) {
    event.preventDefault();
  });
})();
