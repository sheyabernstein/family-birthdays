// Soft, non-blocking hints for the sign-in page's combined "email or
// phone" field. It can't use the site's real phone widget (intl-tel-
// input - see phone_input.js) the way a dedicated phone field can: the
// flag/country picker makes no sense while someone might still be
// mid-typing an email address here, and its own parser expects
// something phone-shaped to begin with. This is deliberately much
// lighter than that instead - not real validation, just a nudge.
//
// The phone case isn't just cosmetic: Account.find_by_identifier
// matches the stored phone exactly (E.164, modulo punctuation - see
// that method's own docstring), so a number typed without a country
// code will never match a real account, and the confirmation page is
// intentionally identical whether or not it did (see accounts.views.
// RequestMagicLinkView - no account enumeration). Without this hint,
// that failure is completely silent.
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

  // Loose "does this have an @ with something on both sides and a dot
  // in the domain" shape - not real email validation (accounts.helpers.
  // validate_email_address already does that, server-side, for the
  // person/account contact forms), just enough to catch an obvious typo
  // here too.
  function looksLikeAWellFormedEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function showHint(message) {
    if (!hintEl) {
      hintEl = document.createElement("p");
      hintEl.className = "help-text";
      input.insertAdjacentElement("afterend", hintEl);
    }
    hintEl.textContent = message;
  }

  function clearHint() {
    if (hintEl) {
      hintEl.remove();
      hintEl = null;
    }
  }

  input.addEventListener("blur", () => {
    const value = input.value.trim();
    if (value === "") {
      clearHint();
    } else if (looksLikeAPhoneNumberMissingItsCountryCode(value)) {
      showHint(
        "Include the country code, e.g. +44 7700 900123 for a UK mobile - without it, it won't be recognized.",
      );
    } else if (!value.startsWith("+") && !looksLikeAWellFormedEmail(value)) {
      showHint(
        "Enter a full email address (e.g. you@example.com), or a phone number with the country code.",
      );
    } else {
      clearHint();
    }
  });

  input.addEventListener("input", clearHint);
}
