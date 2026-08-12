(function () {
  const root = document.getElementById("location-autocomplete");
  if (!root || root.dataset.placesEnabled !== "true") return;

  const input = root.querySelector("#location");
  const list = root.querySelector("#location-suggestions");
  const status = root.querySelector("#location-status");
  const spinner = root.querySelector("#location-spinner");
  const placeIdInput = root.querySelector("#location_place_id");
  const latInput = root.querySelector("#location_latitude");
  const lngInput = root.querySelector("#location_longitude");
  const form = root.closest("form");
  if (!input || !list) return;

  const MIN_QUERY_LENGTH = 3;
  const PICK_HINT = "Choose a place from the Google Maps list (free text is not saved).";

  // Last Maps-backed selection (or empty). Free-typed text is never committed.
  let committed = {
    display: "",
    placeId: "",
    latitude: "",
    longitude: "",
  };

  let sessionToken = null;
  let debounceTimer = null;
  let requestController = null;
  let requestSequence = 0;
  let activeIndex = -1;
  let options = [];
  let loadingCount = 0;

  function hasPlaceId() {
    return !!(placeIdInput && placeIdInput.value && placeIdInput.value.trim());
  }

  function readStructured() {
    return {
      display: (input.value || "").trim(),
      placeId: placeIdInput ? placeIdInput.value.trim() : "",
      latitude: latInput ? latInput.value.trim() : "",
      longitude: lngInput ? lngInput.value.trim() : "",
    };
  }

  function applyCommittedToFields() {
    input.value = committed.display;
    if (placeIdInput) placeIdInput.value = committed.placeId;
    if (latInput) latInput.value = committed.latitude;
    if (lngInput) lngInput.value = committed.longitude;
  }

  function commitFromFields() {
    committed = readStructured();
  }

  function clearStructuredLocation() {
    if (placeIdInput) placeIdInput.value = "";
    if (latInput) latInput.value = "";
    if (lngInput) lngInput.value = "";
  }

  function setStructuredLocation(placeId, latitude, longitude) {
    if (placeIdInput) {
      placeIdInput.value =
        typeof placeId === "string" && placeId ? placeId.slice(0, 512) : "";
    }
    if (latInput) {
      latInput.value =
        typeof latitude === "number" && Number.isFinite(latitude)
          ? String(latitude)
          : "";
    }
    if (lngInput) {
      lngInput.value =
        typeof longitude === "number" && Number.isFinite(longitude)
          ? String(longitude)
          : "";
    }
  }

  function setLoading(on) {
    if (on) {
      loadingCount += 1;
    } else {
      loadingCount = Math.max(0, loadingCount - 1);
    }
    const busy = loadingCount > 0;
    if (spinner) spinner.hidden = !busy;
    root.classList.toggle("is-loading", busy);
    if (busy) input.setAttribute("aria-busy", "true");
    else input.removeAttribute("aria-busy");
  }

  function clearStatus() {
    if (!status) return;
    status.hidden = true;
    status.textContent = "";
  }

  function showStatus(message) {
    if (!status) return;
    status.textContent = message;
    status.hidden = false;
  }

  function newSessionToken() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "loc-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }

  function ensureSession() {
    if (!sessionToken) sessionToken = newSessionToken();
    return sessionToken;
  }

  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    activeIndex = -1;
    options.forEach((option) => option.classList.remove("is-active"));
  }

  function openList() {
    if (!options.length) return;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function setActive(index) {
    options.forEach((option) => option.classList.remove("is-active"));
    if (!options.length) {
      activeIndex = -1;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    activeIndex = ((index % options.length) + options.length) % options.length;
    const option = options[activeIndex];
    option.classList.add("is-active");
    option.scrollIntoView({ block: "nearest" });
    input.setAttribute("aria-activedescendant", option.id);
  }

  function renderSuggestions(suggestions) {
    list.replaceChildren();
    options = [];
    activeIndex = -1;

    suggestions.forEach((suggestion, index) => {
      const option = document.createElement("li");
      option.className = "places-option";
      option.id = "location-suggestion-" + index;
      option.setAttribute("role", "option");
      option.dataset.placeId = suggestion.place_id;
      option.dataset.label = suggestion.text;

      const main = document.createElement("span");
      main.className = "places-option-main";
      main.textContent = suggestion.main_text || suggestion.text;
      option.appendChild(main);

      if (suggestion.secondary_text) {
        const secondary = document.createElement("span");
        secondary.className = "places-option-secondary";
        secondary.textContent = suggestion.secondary_text;
        option.appendChild(secondary);
      }

      option.addEventListener("mousedown", function (event) {
        event.preventDefault();
        selectSuggestion(suggestion);
      });
      list.appendChild(option);
      options.push(option);
    });

    if (options.length) {
      const attribution = document.createElement("li");
      attribution.className = "places-attribution";
      attribution.textContent = "Google Maps";
      attribution.setAttribute("aria-hidden", "true");
      attribution.setAttribute("translate", "no");
      list.appendChild(attribution);
      openList();
    } else {
      closeList();
    }
  }

  async function fetchSuggestions(query, sequence) {
    const token = ensureSession();
    if (requestController) requestController.abort();
    requestController = typeof AbortController === "function" ? new AbortController() : null;

    const params = new URLSearchParams({ input: query, session_token: token });
    const request = {
      headers: { Accept: "application/json" },
    };
    if (requestController) request.signal = requestController.signal;

    setLoading(true);
    try {
      const response = await fetch("/api/places/autocomplete?" + params.toString(), request);
      if (sequence !== requestSequence) return;
      if (!response.ok) {
        closeList();
        if (response.status === 503) {
          showStatus(
            "Location suggestions are unavailable. Check that Places API (New) is enabled for this key."
          );
        } else if (response.status === 404) {
          showStatus("Location suggestions are not configured.");
        } else {
          showStatus("Location suggestions are unavailable right now.");
        }
        return;
      }
      const data = await response.json();
      if (sequence !== requestSequence) return;
      clearStatus();
      renderSuggestions(Array.isArray(data.suggestions) ? data.suggestions : []);
      if (!Array.isArray(data.suggestions) || !data.suggestions.length) {
        showStatus("No matching places. Pick from the list when results appear.");
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (sequence === requestSequence) {
        closeList();
        showStatus("Could not load location suggestions.");
      }
    } finally {
      setLoading(false);
    }
  }

  function cancelAutocomplete() {
    if (requestController) {
      requestController.abort();
      requestController = null;
    }
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }

  function dismissSuggestions() {
    requestSequence += 1;
    cancelAutocomplete();
    closeList();
    // Drop in-flight spinner for cancelled autocomplete.
    loadingCount = 0;
    if (spinner) spinner.hidden = true;
    root.classList.remove("is-loading");
    input.removeAttribute("aria-busy");
  }

  /** Revert free-typed text that was never chosen from Maps. */
  function rejectNonMapsLocation() {
    const text = (input.value || "").trim();
    if (!text) {
      // Empty location is allowed.
      clearStructuredLocation();
      committed = { display: "", placeId: "", latitude: "", longitude: "" };
      clearStatus();
      input.value = "";
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    if (hasPlaceId()) {
      // Keep selection; normalize display if needed.
      commitFromFields();
      clearStatus();
      return;
    }
    // Typed without selecting a suggestion — restore last Maps pick or clear.
    applyCommittedToFields();
    if (committed.placeId) {
      showStatus(PICK_HINT);
    } else {
      input.value = "";
      clearStructuredLocation();
      showStatus(PICK_HINT);
    }
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function selectSuggestion(suggestion) {
    requestSequence += 1;
    const selectionSequence = requestSequence;
    cancelAutocomplete();
    closeList();
    const token = sessionToken;
    sessionToken = null; // Place Details terminates this session.
    const suggestionText = typeof suggestion.text === "string" ? suggestion.text : "";
    input.value = suggestionText.slice(0, 255);
    // place_id is required for a valid Maps location (coords optional if details fail).
    setStructuredLocation(suggestion.place_id, null, null);
    clearStatus();

    const params = new URLSearchParams({ place_id: suggestion.place_id });
    if (token) params.set("session_token", token);
    setLoading(true);
    try {
      const response = await fetch("/api/places/details?" + params.toString(), {
        headers: { Accept: "application/json" },
      });
      if (response.ok && selectionSequence === requestSequence) {
        const data = await response.json();
        if (
          selectionSequence === requestSequence &&
          typeof data.formatted_address === "string" &&
          data.formatted_address
        ) {
          input.value = String(data.formatted_address).slice(0, 255);
        }
        if (selectionSequence === requestSequence) {
          const placeId =
            typeof data.place_id === "string" && data.place_id
              ? data.place_id
              : suggestion.place_id;
          setStructuredLocation(placeId, data.latitude, data.longitude);
        }
      } else if (!response.ok && selectionSequence === requestSequence) {
        showStatus("Location details incomplete; using the selected Maps place.");
      }
    } catch (error) {
      // Prediction text + place_id remain a valid Maps selection.
    } finally {
      if (selectionSequence === requestSequence) {
        setLoading(false);
        if (hasPlaceId()) {
          commitFromFields();
          clearStatus();
        }
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  // Seed committed state: only Maps-backed rows count as selected.
  if (hasPlaceId()) {
    commitFromFields();
  } else if ((input.value || "").trim()) {
    // Legacy free-text without place_id — not a valid Maps location.
    showStatus(PICK_HINT);
    committed = { display: "", placeId: "", latitude: "", longitude: "" };
  }

  // Never submit free-typed location text; use last Maps commit or empty.
  if (form) {
    form.addEventListener("formdata", function (event) {
      if (hasPlaceId()) return;
      const fd = event.formData;
      fd.set("location", committed.placeId ? committed.display : "");
      fd.set("location_place_id", committed.placeId || "");
      fd.set("location_latitude", committed.latitude || "");
      fd.set("location_longitude", committed.longitude || "");
    });
  }

  input.addEventListener("input", function () {
    requestSequence += 1;
    const sequence = requestSequence;
    cancelAutocomplete();
    closeList();
    clearStatus();
    // Manual typing invalidates the previous Maps selection until a new pick.
    clearStructuredLocation();

    const query = input.value.trim();
    if (!query) {
      loadingCount = 0;
      if (spinner) spinner.hidden = true;
      root.classList.remove("is-loading");
      input.removeAttribute("aria-busy");
      return;
    }
    if (query.length < MIN_QUERY_LENGTH) {
      showStatus("Type at least " + MIN_QUERY_LENGTH + " characters, then pick from the list.");
      return;
    }
    debounceTimer = setTimeout(function () {
      fetchSuggestions(query, sequence);
    }, 280);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (list.hidden) openList();
      setActive(activeIndex < 0 ? 0 : activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) openList();
      setActive(activeIndex < 0 ? options.length - 1 : activeIndex - 1);
    } else if (event.key === "Enter") {
      if (!list.hidden && activeIndex >= 0) {
        event.preventDefault();
        const suggestion = options[activeIndex];
        if (suggestion) {
          selectSuggestion({
            place_id: suggestion.dataset.placeId,
            text: suggestion.dataset.label,
          });
        }
      } else if (!hasPlaceId() && (input.value || "").trim()) {
        // Block form submit with free text; force list selection.
        event.preventDefault();
        showStatus(PICK_HINT);
      }
    } else if (event.key === "Escape") {
      dismissSuggestions();
      rejectNonMapsLocation();
    }
  });

  input.addEventListener("blur", function () {
    window.setTimeout(function () {
      if (root.contains(document.activeElement)) {
        closeList();
        return;
      }
      dismissSuggestions();
      rejectNonMapsLocation();
    }, 120);
  });

  document.addEventListener("click", function (event) {
    if (!root.contains(event.target)) {
      dismissSuggestions();
    }
  });
})();
