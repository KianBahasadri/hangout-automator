/* Confirmation prompts for destructive forms.

   The message lives in a data-confirm attribute rather than an inline
   onsubmit="confirm('…')". Jinja escapes for HTML, not for JavaScript, so a
   name holding an apostrophe ("O'Brien") used to close the JS string literal
   early: the handler failed to parse, never ran, and the form submitted with
   no prompt at all. Reading the text back through dataset keeps the value a
   string no matter what characters it contains. */
(function () {
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches("form[data-confirm]")) return;
    var message = form.dataset.confirm;
    if (!message) return;
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });
})();
