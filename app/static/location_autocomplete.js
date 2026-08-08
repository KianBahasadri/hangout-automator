(function () {
  const root = document.getElementById("location-autocomplete");
  if (!root || root.dataset.placesEnabled !== "true") return;

  const input = root.querySelector("#location");
  const list = root.querySelector("#location-suggestions");
  const status = root.querySelector("#location-status");
  if (!input || !list) return;

  const MIN_QUERY_LENGTH = 3;
  let sessionToken = null;
  let debounceTimer = null;
  let requestController = null;
  let requestSequence = 0;
  let activeIndex = -1;
  let options = [];

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

    try {
      const response = await fetch("/api/places/autocomplete?" + params.toString(), request);
      if (sequence !== requestSequence) return;
      if (!response.ok) {
        closeList();
        if (response.status === 503) {
          showStatus("Location suggestions are unavailable. Check that Places API (New) is enabled for this key.");
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
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (sequence === requestSequence) {
        closeList();
        showStatus("Could not load location suggestions.");
      }
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
    input.removeAttribute("aria-busy");
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
    input.setAttribute("aria-busy", "true");

    const params = new URLSearchParams({ place_id: suggestion.place_id });
    if (token) params.set("session_token", token);
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
      } else if (!response.ok && selectionSequence === requestSequence) {
        showStatus("Location details are unavailable; using the selected suggestion.");
      }
    } catch (error) {
      // The prediction text remains a useful fallback when details fail.
    } finally {
      if (selectionSequence === requestSequence) {
        input.removeAttribute("aria-busy");
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  input.addEventListener("input", function () {
    requestSequence += 1;
    const sequence = requestSequence;
    cancelAutocomplete();
    closeList();
    clearStatus();
    input.removeAttribute("aria-busy");

    const query = input.value.trim();
    if (query.length < MIN_QUERY_LENGTH) return;
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
    } else if (event.key === "Enter" && !list.hidden && activeIndex >= 0) {
      event.preventDefault();
      const suggestion = options[activeIndex];
      if (suggestion) {
        selectSuggestion({
          place_id: suggestion.dataset.placeId,
          text: suggestion.dataset.label,
        });
      }
    } else if (event.key === "Escape") {
      dismissSuggestions();
    }
  });

  input.addEventListener("blur", function () {
    window.setTimeout(function () {
      if (!root.contains(document.activeElement)) dismissSuggestions();
      else closeList();
    }, 120);
  });

  document.addEventListener("click", function (event) {
    if (!root.contains(event.target)) dismissSuggestions();
  });
})();
