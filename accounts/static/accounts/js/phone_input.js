// Wires intl-tel-input onto a phone <input> - a country-flag picker plus
// client-side validation, matching Account.phone's own "E.164 format"
// convention (see accounts/models.py). Lives under accounts/ since every
// phone field on the site - whether it's editing a Person's own contact
// info (family/forms.PersonForm) or an Account's own (My Notifications) -
// is ultimately about Account.phone; PersonForm's own field is a thin
// front end onto it, not a real Person model field.
function initPhoneInput(selector) {
  const input = document.querySelector(selector);
  if (!input) {
    return;
  }

  const iti = window.intlTelInput(input, {});
  const container = input.closest(".iti");
  let errorEl = null;

  function showError(message) {
    if (!errorEl) {
      errorEl = document.createElement("div");
      errorEl.className = "phone-error";
      container.insertAdjacentElement("afterend", errorEl);
    }
    errorEl.textContent = message;
  }

  function clearError() {
    if (errorEl) {
      errorEl.textContent = "";
    }
  }

  // A blank phone field is valid here (it's always optional - the
  // relevant form enforces "email or phone" at the whole-form level, not
  // this field alone), so only flag genuinely typed-but-invalid input.
  function isAcceptable() {
    return input.value.trim() === "" || iti.isValidNumber();
  }

  input.addEventListener("blur", () => {
    if (isAcceptable()) {
      clearError();
    } else {
      showError("Enter a valid phone number, or leave this blank.");
    }
  });

  input.addEventListener("input", clearError);

  const form = input.closest("form");
  if (form) {
    // Posted in place of `input` itself, which keeps showing exactly
    // what the user typed (national format) - never overwritten in
    // place. Found for real: writing iti.getNumber()'s E.164 result
    // straight into input.value put a second "+44" right next to the
    // flag picker's own "+44" dial-code label, reading as a doubled
    // country code ("+44 +447xxxxxxxxx") - not a real data problem (the
    // posted value was always correct), but confusing enough that a
    // blocked submit (e.g. a native required-field validation failure
    // elsewhere on the form, which never even dispatches this submit
    // handler) looked like the phone field itself had gotten corrupted
    // and needed a second attempt.
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = input.name;
    form.appendChild(hidden);
    input.removeAttribute("name");

    form.addEventListener("submit", (event) => {
      if (!isAcceptable()) {
        event.preventDefault();
        showError("Enter a valid phone number, or leave this blank.");
        input.focus();
        return;
      }
      // getNumber() returns E.164 - recomputed from input's own
      // (unmodified) text every time, so re-running this handler for
      // whatever reason is always safe.
      hidden.value = input.value.trim() === "" ? "" : iti.getNumber();
    });
  }
}
