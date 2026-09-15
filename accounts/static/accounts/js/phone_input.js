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
    form.addEventListener("submit", (event) => {
      if (!isAcceptable()) {
        event.preventDefault();
        showError("Enter a valid phone number, or leave this blank.");
        input.focus();
        return;
      }
      // getNumber() returns E.164 - overwrite the visible (national-
      // format) value with it right before submit, so the field posts
      // the same format the backend already expects, with no server-side
      // changes needed.
      if (input.value.trim() !== "") {
        input.value = iti.getNumber();
      }
    });
  }
}
