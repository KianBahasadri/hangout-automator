(function () {
  "use strict";

  function safeRedirect(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) {
      return "/";
    }
    try {
      var destination = new URL(value, window.location.origin);
      if (destination.origin !== window.location.origin) return "/";
      return destination.pathname + destination.search + destination.hash;
    } catch (error) {
      return "/";
    }
  }

  function showError() {
    var error = document.getElementById("clerk-auth-error");
    if (error) error.hidden = false;
  }

  async function initializeClerk() {
    if (!window.Clerk) {
      showError();
      return;
    }

    try {
      var loadOptions = window.__internal_ClerkUICtor
        ? { ui: { ClerkUI: window.__internal_ClerkUICtor } }
        : {};
      await window.Clerk.load(loadOptions);

      var signIn = document.getElementById("clerk-sign-in");
      if (signIn) {
        var redirect = safeRedirect(signIn.getAttribute("data-redirect-url"));
        if (window.Clerk.isSignedIn) {
          window.location.replace(redirect);
          return;
        }
        window.Clerk.mountSignIn(signIn, {
          fallbackRedirectUrl: redirect,
          signUpFallbackRedirectUrl: redirect,
          withSignUp: true,
        });
      }

      var userButton = document.getElementById("clerk-user-button");
      if (userButton && window.Clerk.isSignedIn) {
        window.Clerk.mountUserButton(userButton);
      }
    } catch (error) {
      console.error("Clerk failed to initialize", error);
      showError();
    }
  }

  window.addEventListener("load", initializeClerk);
})();
