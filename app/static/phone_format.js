(function () {
  const MAX_DIGITS = 15;

  function digitsOnly(value) {
    return String(value || "").replace(/\D/g, "").slice(0, MAX_DIGITS);
  }

  function formatNanp(national) {
    const d = national.slice(0, 10);
    const len = d.length;
    if (len === 0) return "+1";
    if (len < 4) return `+1 (${d}`;
    if (len < 7) return `+1 (${d.slice(0, 3)}) ${d.slice(3)}`;
    return `+1 (${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  }

  /** Pretty display / as-you-type. Standard NANP: +1 (XXX) XXX-XXXX */
  function formatPhone(value) {
    const digits = digitsOnly(value);
    if (!digits) return "";

    // NANP: 10 digits, or 11 starting with 1 (including partial while typing)
    if (digits.length <= 11 && (digits[0] === "1" || digits.length <= 10)) {
      if (digits[0] === "1") {
        return formatNanp(digits.slice(1));
      }
      return formatNanp(digits);
    }

    // Other country codes: +XXX XXX XXX …
    return "+" + digits.replace(/(\d{3})(?=\d)/g, "$1 ").trim();
  }

  function countDigits(str) {
    return (String(str).match(/\d/g) || []).length;
  }

  function caretPosForDigitIndex(formatted, digitIndex) {
    if (digitIndex <= 0) return 0;
    let seen = 0;
    for (let i = 0; i < formatted.length; i++) {
      if (/\d/.test(formatted[i])) {
        seen += 1;
        if (seen === digitIndex) return i + 1;
      }
    }
    return formatted.length;
  }

  function applyInputFormat(input) {
    const prev = input.value;
    const start = input.selectionStart || 0;
    const digitsBefore = countDigits(prev.slice(0, start));
    const next = formatPhone(prev);
    if (next === prev) return;
    input.value = next;
    const pos = caretPosForDigitIndex(next, digitsBefore);
    try {
      input.setSelectionRange(pos, pos);
    } catch (_) {
      /* ignore non-text inputs */
    }
  }

  function bindTelInput(input) {
    if (!input || input.dataset.phoneBound === "1") return;
    input.dataset.phoneBound = "1";
    // Initial pretty format (e.g. values from server as E.164)
    if (input.value) input.value = formatPhone(input.value);
    input.addEventListener("input", () => applyInputFormat(input));
    input.addEventListener("blur", () => {
      if (input.value) input.value = formatPhone(input.value);
    });
  }

  function bindAll(root) {
    (root || document).querySelectorAll('input[type="tel"]').forEach(bindTelInput);
  }

  window.HangoutPhone = {
    format: formatPhone,
    digits: digitsOnly,
    bind: bindTelInput,
    bindAll: bindAll,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bindAll(document));
  } else {
    bindAll(document);
  }
})();
