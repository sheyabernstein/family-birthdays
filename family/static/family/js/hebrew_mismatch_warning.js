/* Warns (never blocks) when an already-entered Hebrew date and Gregorian
 * date don't correspond, beyond the one day sunset can plausibly explain.
 *
 * Assistive only, same "never authoritative" stance as hebrew_autofill.js
 * and accounts/static/accounts/js/email_input.js - see AGENTS.md's "two
 * calendars are recorded independently, never derived" rule. A mismatch
 * beyond a day is very likely a typo, but the whole reason both dates
 * are entered independently is that only the person entering the data
 * knows the true correspondence (an event after sunset advances the
 * Hebrew date by one), so this only ever warns - it never prevents
 * saving, unlike a hard validation error.
 *
 * Compares in Gregorian terms only: family:hebrew_to_gregorian converts
 * whatever's currently in the three Hebrew fields to its own Gregorian
 * equivalent, and the difference in days against the Gregorian field
 * already on the page is plain date arithmetic from there - no need to
 * reimplement Hebrew-calendar day differencing in JS.
 */
function initHebrewMismatchWarning(url, fields) {
  fields.forEach(({ gregorianId, yearId, monthId, dayId, warningId }) => {
    const gregorianInput = document.getElementById(gregorianId);
    const yearInput = document.getElementById(yearId);
    const monthInput = document.getElementById(monthId);
    const dayInput = document.getElementById(dayId);
    const warning = document.getElementById(warningId);
    if (!gregorianInput || !yearInput || !monthInput || !dayInput || !warning)
      return;

    const csrfToken = document.querySelector(
      "[name=csrfmiddlewaretoken]",
    )?.value;

    const check = async () => {
      const year = yearInput.value;
      const month = monthInput.value;
      const day = dayInput.value;
      if (!gregorianInput.value || !year || !month || !day) {
        warning.hidden = true;
        return;
      }

      let response;
      try {
        response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": csrfToken,
          },
          body: `year=${year}&month=${month}&day=${day}`,
        });
      } catch {
        return; // Fail open - a network hiccup shouldn't manufacture a warning.
      }
      if (!response.ok) {
        warning.hidden = true;
        return;
      }
      const data = await response.json();

      const expected = new Date(`${data.date}T00:00:00`);
      const actual = new Date(`${gregorianInput.value}T00:00:00`);
      const dayMs = 24 * 60 * 60 * 1000;
      const diffDays = Math.round(Math.abs(expected - actual) / dayMs);

      warning.hidden = diffDays <= 1;
    };

    [gregorianInput, yearInput, monthInput, dayInput].forEach((el) =>
      el.addEventListener("change", check),
    );
    check();
  });
}
