(function () {
  const form = document.querySelector("[data-hangout-form]");
  const openBtn = document.getElementById("preview-invite-sms");
  const dialog = document.getElementById("sms-preview-dialog");
  const closeBtn = document.getElementById("sms-preview-close");
  const bodyEl = document.getElementById("sms-preview-body");
  if (!form || !openBtn || !dialog || !bodyEl) return;

  function fieldValue(name) {
    const el = form.elements.namedItem(name);
    if (!el) return "";
    if (el instanceof RadioNodeList) {
      return (el.value || "").trim();
    }
    return (el.value || "").trim();
  }

  function firstSelectedInviteeName() {
    const checked = form.querySelectorAll('input[name="profile_ids"]:checked');
    for (const input of checked) {
      const row = input.closest(".invitee-row");
      const nameEl = row && row.querySelector(".invitee-name");
      if (nameEl && nameEl.textContent.trim()) {
        return nameEl.textContent.trim();
      }
    }
    return "Alex";
  }

  function closeDialog() {
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function openDialog() {
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  async function preview() {
    const recipientName = firstSelectedInviteeName();
    bodyEl.textContent = "Loading…";
    openDialog();

    const payload = {
      recipient_name: recipientName,
      day_date: fieldValue("day_date") || null,
      time: fieldValue("time") || null,
      duration: fieldValue("duration") || null,
      location: fieldValue("location") || null,
      motive: fieldValue("motive") || null,
      alcohol_involved: fieldValue("alcohol_involved") || null,
      weed_involved: fieldValue("weed_involved") || null,
      notes: fieldValue("notes") || null,
    };

    try {
      const response = await fetch("/api/sms/preview-invite", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error("Preview failed (" + response.status + ")");
      }
      const data = await response.json();
      bodyEl.textContent = data.body || "";
    } catch (err) {
      bodyEl.textContent = "Could not build preview. " + (err && err.message ? err.message : "");
    }
  }

  openBtn.addEventListener("click", function (event) {
    event.preventDefault();
    preview();
  });

  if (closeBtn) {
    closeBtn.addEventListener("click", function (event) {
      event.preventDefault();
      closeDialog();
    });
  }

  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) closeDialog();
  });
})();
