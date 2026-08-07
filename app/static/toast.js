(function () {
  var MESSAGES = {
    hangout_deleted: "Hangout removed from the list.",
  };

  var TOAST_MS = 4000;

  function root() {
    return document.getElementById("toast-root");
  }

  function showToast(message) {
    var host = root();
    if (!host || !message) return;

    var el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.textContent = message;
    host.appendChild(el);

    // Trigger enter transition on next frame
    requestAnimationFrame(function () {
      el.classList.add("toast-visible");
    });

    window.setTimeout(function () {
      el.classList.remove("toast-visible");
      el.classList.add("toast-leaving");
      window.setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 220);
    }, TOAST_MS);
  }

  function consumeToastQuery() {
    try {
      var url = new URL(window.location.href);
      var key = url.searchParams.get("toast");
      if (!key) return;
      var message = MESSAGES[key];
      if (message) showToast(message);
      url.searchParams.delete("toast");
      var next = url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash;
      window.history.replaceState({}, "", next);
    } catch (e) {
      /* ignore */
    }
  }

  window.__hangoutToast = { show: showToast };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", consumeToastQuery);
  } else {
    consumeToastQuery();
  }
})();
