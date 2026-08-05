(function () {
  const PAGE_SIZE = 9;

  function initPicker(root) {
    const grid = root.querySelector(".invitee-grid");
    if (!grid) return;

    const rows = () => Array.from(root.querySelectorAll(".invitee-row"));
    const countEl = root.querySelector("[data-selected-count]");
    const search = root.querySelector(".invitee-search");
    const pager = root.querySelector("[data-pager]");
    const pageCurrentEl = root.querySelector("[data-page-current]");
    const pageTotalEl = root.querySelector("[data-page-total]");
    const matchCountEl = root.querySelector("[data-match-count]");
    const prevBtn = root.querySelector('[data-page="prev"]');
    const nextBtn = root.querySelector('[data-page="next"]');

    let page = 1;

    function updateCount() {
      if (!countEl) return;
      const n = rows().filter((row) => {
        const cb = row.querySelector('input[type="checkbox"]');
        return cb && cb.checked;
      }).length;
      countEl.textContent = String(n);
    }

    function matchingRows() {
      const q = (search && search.value ? search.value : "").trim().toLowerCase();
      return rows().filter((row) => {
        if (!q) return true;
        const hay =
          (row.dataset.name || "") + " " + (row.dataset.phone || "") + " " + (row.dataset.tagNames || "");
        return hay.includes(q);
      });
    }

    function renderPage() {
      const matched = matchingRows();
      const totalPages = Math.max(1, Math.ceil(matched.length / PAGE_SIZE) || 1);
      if (page > totalPages) page = totalPages;
      if (page < 1) page = 1;

      const start = (page - 1) * PAGE_SIZE;
      const end = start + PAGE_SIZE;
      const onPage = new Set(matched.slice(start, end));

      rows().forEach((row) => {
        row.hidden = !onPage.has(row);
      });

      if (pageCurrentEl) pageCurrentEl.textContent = String(page);
      if (pageTotalEl) pageTotalEl.textContent = String(totalPages);
      if (matchCountEl) matchCountEl.textContent = String(matched.length);
      if (pager) pager.hidden = matched.length <= PAGE_SIZE;
      if (prevBtn) prevBtn.disabled = page <= 1;
      if (nextBtn) nextBtn.disabled = page >= totalPages;
    }

    function toggleGroup(matching) {
      const checks = matching
        .map((row) => row.querySelector('input[type="checkbox"]'))
        .filter(Boolean);
      if (!checks.length) return;
      const allOn = checks.every((c) => c.checked);
      checks.forEach((c) => {
        c.checked = !allOn;
      });
      updateCount();
      syncChipStates();
    }

    function matchesFilter(row, filter) {
      for (const [key, value] of Object.entries(filter)) {
        if (key === "allergies") {
          const has = row.dataset.allergies === "1";
          if (value === "yes" && !has) return false;
          if (value === "no" && has) return false;
          continue;
        }
        if ((row.dataset[key] || "") !== String(value)) return false;
      }
      return true;
    }

    function syncChipStates() {
      root.querySelectorAll("[data-toggle-tag]").forEach((chip) => {
        const tagId = chip.getAttribute("data-toggle-tag");
        const matching = rows().filter((row) => {
          const ids = (row.dataset.tags || "").split(",").filter(Boolean);
          return ids.includes(tagId);
        });
        const checks = matching.map((r) => r.querySelector('input[type="checkbox"]')).filter(Boolean);
        const allOn = checks.length > 0 && checks.every((c) => c.checked);
        chip.classList.toggle("is-active", allOn);
      });
      root.querySelectorAll("[data-toggle-filter]").forEach((chip) => {
        let filter;
        try {
          filter = JSON.parse(chip.getAttribute("data-toggle-filter") || "{}");
        } catch {
          return;
        }
        const matching = rows().filter((row) => matchesFilter(row, filter));
        const checks = matching.map((r) => r.querySelector('input[type="checkbox"]')).filter(Boolean);
        const allOn = checks.length > 0 && checks.every((c) => c.checked);
        chip.classList.toggle("is-active", allOn);
      });
    }

    root.addEventListener("click", (ev) => {
      const pageBtn = ev.target.closest("[data-page]");
      if (pageBtn && root.contains(pageBtn)) {
        ev.preventDefault();
        const dir = pageBtn.getAttribute("data-page");
        if (dir === "prev") page -= 1;
        if (dir === "next") page += 1;
        renderPage();
        return;
      }

      const chip = ev.target.closest("[data-toggle-tag], [data-toggle-filter], [data-action]");
      if (!chip || !root.contains(chip)) return;
      ev.preventDefault();

      if (chip.hasAttribute("data-toggle-tag")) {
        const tagId = chip.getAttribute("data-toggle-tag");
        toggleGroup(
          rows().filter((row) => {
            const ids = (row.dataset.tags || "").split(",").filter(Boolean);
            return ids.includes(tagId);
          })
        );
        return;
      }

      if (chip.hasAttribute("data-toggle-filter")) {
        let filter;
        try {
          filter = JSON.parse(chip.getAttribute("data-toggle-filter") || "{}");
        } catch {
          return;
        }
        toggleGroup(rows().filter((row) => matchesFilter(row, filter)));
        return;
      }

      const action = chip.getAttribute("data-action");
      const matched = matchingRows();
      if (action === "select-all") {
        matched.forEach((row) => {
          const cb = row.querySelector('input[type="checkbox"]');
          if (cb) cb.checked = true;
        });
        updateCount();
        syncChipStates();
      } else if (action === "clear") {
        rows().forEach((row) => {
          const cb = row.querySelector('input[type="checkbox"]');
          if (cb) cb.checked = false;
        });
        updateCount();
        syncChipStates();
      } else if (action === "invert") {
        matched.forEach((row) => {
          const cb = row.querySelector('input[type="checkbox"]');
          if (cb) cb.checked = !cb.checked;
        });
        updateCount();
        syncChipStates();
      }
    });

    grid.addEventListener("change", () => {
      updateCount();
      syncChipStates();
    });

    if (search) {
      search.addEventListener("input", () => {
        page = 1;
        renderPage();
      });
    }

    updateCount();
    syncChipStates();
    renderPage();
  }

  document.querySelectorAll(".invitee-picker:not([data-ready])").forEach((root) => {
    root.setAttribute("data-ready", "1");
    initPicker(root);
  });
})();
