(function () {
  const drafts = document.getElementById("contact-drafts");
  const template = document.getElementById("contact-draft-template");
  const addButton = document.querySelector("[data-add-contact]");
  const importButton = document.querySelector("[data-import-contacts]");
  const importStatus = document.querySelector("[data-import-status]");
  if (!drafts || !template) return;

  let nextIndex = Number(drafts.dataset.nextIndex || 0);

  function cards() {
    return Array.from(drafts.querySelectorAll("[data-contact-card]"));
  }

  function replaceIndex(card, index) {
    ["id", "name", "for", "data-contact-index"].forEach((attribute) => {
      if (card.hasAttribute(attribute)) {
        card.setAttribute(attribute, card.getAttribute(attribute).replaceAll("__INDEX__", String(index)));
      }
    });
    card.querySelectorAll("[id], [name], [for], [data-contact-index]").forEach((element) => {
      ["id", "name", "for", "data-contact-index"].forEach((attribute) => {
        if (element.hasAttribute(attribute)) {
          element.setAttribute(attribute, element.getAttribute(attribute).replaceAll("__INDEX__", String(index)));
        }
      });
    });
  }

  function renumber() {
    cards().forEach((card, index) => {
      const number = card.querySelector("[data-contact-number]");
      if (number) number.textContent = String(index + 1);
    });
  }

  function bindPhoneField(card) {
    const phone = card.querySelector('[data-field="phone"]');
    if (phone && window.HangoutPhone) window.HangoutPhone.bind(phone);
  }

  function addCard(values) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector("[data-contact-card]");
    replaceIndex(card, nextIndex++);
    drafts.appendChild(fragment);
    const added = drafts.lastElementChild;
    bindPhoneField(added);
    if (values) fillCard(added, values);
    renumber();
    return added;
  }

  function clearCard(card) {
    card.querySelector('[data-field="name"]').value = "";
    card.querySelector('[data-field="phone"]').value = "";
    card.querySelectorAll("select").forEach((select) => {
      select.value = "";
    });
    card.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
      checkbox.checked = false;
    });
  }

  function isBlank(card) {
    const name = card.querySelector('[data-field="name"]')?.value.trim() || "";
    const phone = card.querySelector('[data-field="phone"]')?.value.trim() || "";
    const hasPreference = Array.from(card.querySelectorAll("select")).some((select) => select.value);
    const hasCheck = card.querySelector('input[type="checkbox"]:checked');
    return !name && !phone && !hasPreference && !hasCheck;
  }

  function fillCard(card, values) {
    const name = card.querySelector('[data-field="name"]');
    const phone = card.querySelector('[data-field="phone"]');
    if (name) name.value = values.name || "";
    if (phone) phone.value = values.phone || "";
  }

  function firstValue(value) {
    if (Array.isArray(value)) return value[0] || "";
    return value || "";
  }

  function contactValues(contact) {
    return {
      name: firstValue(contact.name),
      phone: firstValue(contact.tel),
    };
  }

  drafts.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-contact]");
    if (!remove) return;
    const card = remove.closest("[data-contact-card]");
    if (!card) return;
    if (cards().length === 1) clearCard(card);
    else card.remove();
    renumber();
  });

  if (addButton) {
    addButton.addEventListener("click", () => addCard());
  }

  const pickerSupported = "contacts" in navigator && "ContactsManager" in window;
  if (!pickerSupported) {
    if (importButton) importButton.disabled = true;
    if (importStatus) importStatus.textContent = "Phone contact import is not supported in this browser.";
    return;
  }

  if (importButton) {
    importButton.addEventListener("click", async () => {
      importButton.disabled = true;
      if (importStatus) importStatus.textContent = "Choose contacts to import…";
      try {
        const selected = await navigator.contacts.select(["name", "tel"], { multiple: true });
        if (!selected.length) {
          if (importStatus) importStatus.textContent = "No contacts selected.";
          return;
        }

        let firstBlank = cards().length === 1 && isBlank(cards()[0]) ? cards()[0] : null;
        selected.forEach((contact, index) => {
          const card = index === 0 && firstBlank ? firstBlank : addCard();
          fillCard(card, contactValues(contact));
        });
        if (importStatus) {
          importStatus.textContent = `${selected.length} contact${selected.length === 1 ? "" : "s"} loaded. Review before saving.`;
        }
      } catch (error) {
        if (error && error.name === "AbortError") {
          if (importStatus) importStatus.textContent = "No contacts selected.";
        } else if (importStatus) {
          importStatus.textContent = "Contact import failed. Add the contact manually.";
        }
      } finally {
        importButton.disabled = false;
      }
    });
  }
})();
