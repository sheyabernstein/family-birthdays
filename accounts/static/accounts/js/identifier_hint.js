// Soft, non-blocking hint for the sign-in page's combined "email or
// phone" field. It can't use the site's real phone widget (intl-tel-
// input - see phone_input.js) the way a dedicated phone field can: the
// flag/country picker makes no sense while someone might still be
// mid-typing an email address here, and its own parser expects
// something phone-shaped to begin with. This is deliberately a much
// lighter heuristic instead - not real validation, just a nudge - since
// Account.find_by_identifier matches the stored phone exactly (E.164,
// modulo punctuation - see that method's own docstring), so a number
// typed without a country code will never match a real account, and the
// confirmation page is intentionally identical whether or not it did
// (see accounts.views.RequestMagicLinkView - no account enumeration).
// Without this hint, that failure is completely silent.
function initIdentifierHint(selector) {
  const input = document.querySelector(selector);
  if (!input) {
    return;
  }

  let hintEl = null;

  // Digits and common phone punctuation only (spaces, hyphens, dots,
  // parens), with no leading "+" - deliberately narrow, so this never
  // fires for a mistyped email (no "@" yet) or other plain text, only
  // for something that's actually phone-shaped and just missing its
  // country code.
  function looksLikeAPhoneNumberMissingItsCountryCode(value) {
    return (
      value !== "" && /^[\d\s().-]+$/.test(value) && !value.startsWith("+")
    );
  }

  function showHint() {
    if (!hintEl) {
      hintEl = document.createElement("p");
      hintEl.className = "help-text";
      input.insertAdjacentElement("afterend", hintEl);
    }
    hintEl.textContent =
      "Include the country code, e.g. +44 7700 900123 for a UK mobile - without it, we won't recognize it.";
  }

  function clearHint() {
    if (hintEl) {
      hintEl.remove();
      hintEl = null;
    }
  }

  input.addEventListener("blur", () => {
    if (looksLikeAPhoneNumberMissingItsCountryCode(input.value.trim())) {
      showHint();
    } else {
      clearHint();
    }
  });

  input.addEventListener("input", clearHint);
}
