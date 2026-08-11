(function () {
  const root = document.getElementById("contacts-browser");
  if (!root) return;

  const cards = () => Array.from(root.querySelectorAll(".profile-editor"));
  const search = root.querySelector(".profiles-search");
  const countEl = root.querySelector("[data-visible-count]");

  function activeTagIds() {
    return Array.from(root.querySelectorAll("[data-filter-tag].is-active")).map((el) =>
      el.getAttribute("data-filter-tag")
    );
  }

  function activeFieldFilters() {
    return Array.from(root.querySelectorAll("[data-filter-field].is-active")).map((el) => {
      try {
        return JSON.parse(el.getAttribute("data-filter-field") || "{}");
      } catch {
        return null;
      }
    }).filter(Boolean);
  }

  function matchesField(card, filter) {
    for (const [key, value] of Object.entries(filter)) {
      if (key === "allergies") {
        const has = card.dataset.allergies === "1";
        if (value === "yes" && !has) return false;
        if (value === "no" && has) return false;
        continue;
      }
      if ((card.dataset[key] || "") !== String(value)) return false;
    }
    return true;
  }

  function applyFilters() {
    const q = (search && search.value ? search.value : "").trim().toLowerCase();
    const tags = activeTagIds();
    const fields = activeFieldFilters();
    let visible = 0;

    cards().forEach((card) => {
      const hay =
        (card.dataset.name || "") +
        " " +
        (card.dataset.phone || "") +
        " " +
        (card.dataset.tagNames || "");
      let ok = !q || hay.includes(q);

      if (ok && tags.length) {
        const cardTags = (card.dataset.tags || "").split(",").filter(Boolean);
        ok = tags.some((id) => cardTags.includes(id));
      }

      if (ok && fields.length) {
        ok = fields.every((filter) => matchesField(card, filter));
      }

      card.style.display = ok ? "" : "none";
      if (ok) visible += 1;
    });

    if (countEl) countEl.textContent = String(visible);
  }

  root.addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-filter-tag], [data-filter-field], [data-action]");
    if (!chip || !root.contains(chip)) return;
    ev.preventDefault();

    if (chip.hasAttribute("data-filter-tag") || chip.hasAttribute("data-filter-field")) {
      chip.classList.toggle("is-active");
      applyFilters();
      return;
    }

    if (chip.getAttribute("data-action") === "clear-filters") {
      root.querySelectorAll(".chip.is-active").forEach((el) => el.classList.remove("is-active"));
      if (search) search.value = "";
      applyFilters();
    }
  });

  if (search) {
    search.addEventListener("input", applyFilters);
  }

  root.addEventListener("contacts:data-changed", applyFilters);

  applyFilters();
})();
