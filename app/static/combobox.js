(function () {
  function initCombobox(root) {
    const input = root.querySelector(".combobox-input");
    const hidden = root.querySelector('input[type="hidden"]');
    const list = root.querySelector(".combobox-list");
    if (!input || !hidden || !list) return;

    const options = Array.from(list.querySelectorAll(".combobox-option"));
    let activeIndex = -1;

    function open() {
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      root.classList.add("is-open");
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      root.classList.remove("is-open");
      activeIndex = -1;
      options.forEach((opt) => opt.classList.remove("is-active"));
    }

    function visibleOptions() {
      return options.filter((opt) => !opt.hidden);
    }

    function setActive(index) {
      const vis = visibleOptions();
      options.forEach((opt) => opt.classList.remove("is-active"));
      if (!vis.length) {
        activeIndex = -1;
        return;
      }
      activeIndex = ((index % vis.length) + vis.length) % vis.length;
      const opt = vis[activeIndex];
      opt.classList.add("is-active");
      opt.scrollIntoView({ block: "nearest" });
    }

    function selectOption(opt) {
      if (!opt) return;
      const value = opt.getAttribute("data-value") || "";
      const label = opt.getAttribute("data-label") || opt.textContent.trim();
      hidden.value = value;
      input.value = value ? label : "";
      options.forEach((o) => o.classList.toggle("is-selected", o === opt && !!value));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
      close();
      filter("");
    }

    function filter(query) {
      const q = (query || "").trim().toLowerCase();
      let shown = 0;
      options.forEach((opt) => {
        const isEmpty = !opt.getAttribute("data-value");
        const hay = [opt.getAttribute("data-search"), opt.getAttribute("data-label")]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        const match = !q || isEmpty || hay.includes(q);
        opt.hidden = !match;
        if (match) shown += 1;
      });
      return shown;
    }

    const initiallySelected = options.find((opt) => opt.getAttribute("data-value") === hidden.value);
    if (initiallySelected && hidden.value) {
      const label = initiallySelected.getAttribute("data-label") || initiallySelected.textContent.trim();
      if (!input.value.trim()) input.value = label;
      initiallySelected.classList.add("is-selected");
    }

    input.addEventListener("focus", () => {
      filter(input.value);
      open();
    });

    input.addEventListener("input", () => {
      // Typing invalidates a prior selection unless text still matches it
      const selected = options.find((o) => o.getAttribute("data-value") === hidden.value);
      if (selected) {
        const label = selected.getAttribute("data-label") || "";
        if (input.value.trim() !== label) {
          hidden.value = "";
          options.forEach((o) => o.classList.remove("is-selected"));
          hidden.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      const shown = filter(input.value);
      if (shown) open();
      else close();
      setActive(0);
    });

    input.addEventListener("keydown", (e) => {
      const vis = visibleOptions();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (list.hidden) {
          filter(input.value);
          open();
        }
        setActive(activeIndex < 0 ? 0 : activeIndex + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (list.hidden) {
          filter(input.value);
          open();
        }
        setActive(activeIndex < 0 ? vis.length - 1 : activeIndex - 1);
      } else if (e.key === "Enter") {
        if (!list.hidden && activeIndex >= 0) {
          e.preventDefault();
          selectOption(vis[activeIndex]);
        }
      } else if (e.key === "Escape") {
        close();
        input.blur();
      }
    });

    list.addEventListener("mousedown", (e) => {
      const opt = e.target.closest(".combobox-option");
      if (!opt || opt.hidden) return;
      e.preventDefault();
      selectOption(opt);
    });

    document.addEventListener("click", (e) => {
      if (!root.contains(e.target)) close();
    });
  }

  document.querySelectorAll(".combobox").forEach(initCombobox);
})();
