// Soft, non-blocking hint for the sign-in page's combined "email or
// phone" field. It can't use the site's real phone widget (intl-tel-
// input - see phone_input.js) the way a dedicated phone field can: the
// flag/country picker makes no sense while someone might still be
// mid-typing an email address here, and its own parser expects
// something phone-shaped to begin with. This is deliberately a much
// lighter heuristic instead - not real validation, just a nudge - since
// Account.find_by_identifier matches the stored phone exactly (E.164,
// no normalization), so a number typed without a country code will
// never match a real account, and the confirmation page is intentionally
// identical whether or not it did (see accounts.views.
// RequestMagicLinkView - no account enumeration). Without this hint,
// that failure is completely silent.
function initIdentifierHint(selector) {
  const input = document.querySelector(selector);
  if (!input) {
    return;
  }

  let hintEl = null;

  // Not an email (no "@") and doesn't already start with the "+" a
  // country code needs - deliberately loose, since this only has to
  // catch the common case (a bare national-format number) without
  // trying to actually parse phone numbers the way intl-tel-input does.
  function looksLikeAPhoneNumberMissingItsCountryCode(value) {
    return value !== "" && !value.includes("@") && !value.startsWith("+");
  }

  function showHint() {
    if (!hintEl) {
      hintEl = document.createElement("p");
      hintEl.className = "help-text";
      input.insertAdjacentElement("afterend", hintEl);
    }
    hintEl.textContent =
      "Entering a phone number? Include the country code (e.g. +44 for the UK) - without it, it won't be recognized.";
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
