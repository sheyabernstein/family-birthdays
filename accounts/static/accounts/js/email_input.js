// Wires assistive, server-side email validation onto an email <input> -
// see accounts.views.ValidateEmailView / accounts.helpers.
// validate_email_address for what it actually checks (format, a domain/
// TLD typo suggestion, a real MX/A-record lookup). Lives under accounts
// for the same reason phone_input.js does (see that file) - every email
// field on the site is ultimately a front end onto Account.email.
//
// Checked on blur for immediate feedback, and again at submit time if
// the value changed since the last check - but a failed/errored request
// (network down, non-OK response) is always treated as "can't tell"
// rather than invalid, matching accounts.helpers._domain_accepts_mail's
// own tolerance for a lookup that merely *fails*: a flaky connection
// should never manufacture a false rejection that blocks a real
// submission. This is assistive only - Account.email/PersonForm.email
// are still validated for real by Django's own EmailField at save time
// regardless of what this returns.
function initEmailValidation(selector, url) {
  const input = document.querySelector(selector);
  if (!input) {
    return;
  }

  const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
  let errorEl = null;
  let checkedValue = null;
  let checkedResult = null;

  function showMessage(message, isSuggestionOnly) {
    if (!errorEl) {
      errorEl = document.createElement("div");
      errorEl.className = "email-error";
      input.insertAdjacentElement("afterend", errorEl);
    }
    errorEl.textContent = message;
    errorEl.classList.toggle("email-error-suggestion", isSuggestionOnly);
  }

  function clearMessage() {
    if (errorEl) {
      errorEl.textContent = "";
    }
  }

  function messageFor(result) {
    const suggestionText =
      result.suggestions && result.suggestions.length > 0
        ? `Did you mean ${result.suggestions
            .map((s) => `"${s}"`)
            .join(" or ")}?`
        : "";

    if (result.valid) {
      return suggestionText;
    }
    return suggestionText
      ? `${result.message} ${suggestionText}`
      : result.message;
  }

  // Resolves to whether `value` is acceptable to submit - true for a
  // blank field (this widget never makes email required - see the
  // module docstring), true when the check itself can't be completed,
  // and otherwise whatever the server decided.
  async function check(value) {
    if (value === "") {
      checkedValue = value;
      checkedResult = null;
      clearMessage();
      return true;
    }
    if (value === checkedValue) {
      return checkedResult === null || checkedResult.valid;
    }

    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": csrfToken,
        },
        body: `address=${encodeURIComponent(value)}`,
      });
    } catch {
      return true;
    }
    if (!response.ok) {
      return true;
    }

    const result = await response.json();
    checkedValue = value;
    checkedResult = result;

    const message = messageFor(result);
    if (message) {
      showMessage(message, result.valid);
    } else {
      clearMessage();
    }
    return result.valid;
  }

  input.addEventListener("blur", () => check(input.value.trim()));
  input.addEventListener("input", clearMessage);

  const form = input.closest("form");
  if (form) {
    form.addEventListener("submit", (event) => {
      const value = input.value.trim();
      const alreadyAcceptable =
        value === checkedValue &&
        (checkedResult === null || checkedResult.valid);
      if (alreadyAcceptable) {
        return;
      }

      event.preventDefault();
      check(value).then((acceptable) => {
        if (acceptable) {
          form.requestSubmit();
        } else {
          input.focus();
        }
      });
    });
  }
}
