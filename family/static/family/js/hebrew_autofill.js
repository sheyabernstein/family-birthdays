/* Prefills a Hebrew year/month/day trio from a Gregorian date field, via
 * family:gregorian_to_hebrew (see family.views.GregorianToHebrewView).
 *
 * This is only ever a convenience prefill, never authoritative - see
 * AGENTS.md's "two calendars are recorded independently, never derived"
 * rule. The conversion is ambiguous whenever the real event happened
 * after sunset, which only a person entering the data can judge, so this:
 *   - only fires when all three Hebrew fields are still empty (never
 *     overwrites something someone already typed in, including a value
 *     it prefilled a moment ago and the user then edited),
 *   - leaves the filled-in fields fully editable, exactly like manual
 *     entry - nothing is locked or disabled,
 *   - shows a dismissible note next to the fields flagging them as
 *     prefilled and naming the after-sunset caveat explicitly, so it's
 *     never mistaken for a value someone actually confirmed.
 */
function initHebrewAutofill(url, fields) {
  fields.forEach(({ gregorianId, yearId, monthId, dayId, noteId }) => {
    const gregorianInput = document.getElementById(gregorianId);
    const yearInput = document.getElementById(yearId);
    const monthInput = document.getElementById(monthId);
    const dayInput = document.getElementById(dayId);
    const note = noteId ? document.getElementById(noteId) : null;
    if (!gregorianInput || !yearInput || !monthInput || !dayInput) return;

    const csrfToken = document.querySelector(
      "[name=csrfmiddlewaretoken]",
    )?.value;

    const clearNoteIfEditedByHand = () => {
      if (note) note.hidden = true;
    };
    [yearInput, monthInput, dayInput].forEach((el) =>
      el.addEventListener("input", clearNoteIfEditedByHand),
    );

    // blur, not change - a native date input's segmented keyboard entry
    // (day, then month, then year one digit at a time) can fire change
    // more than once while the user is still mid-edit, since some
    // browsers treat an intermediate, not-yet-fully-typed year as a
    // momentarily "complete" value (found for real: typing a year one
    // digit at a time produced a garbage Hebrew year from an
    // intermediate state, then silently never corrected itself once
    // the real year finished, since the fields were no longer empty by
    // then - see the allEmpty guard below). blur only fires once, when
    // the user has actually finished with the field.
    gregorianInput.addEventListener("blur", async () => {
      const allEmpty = !yearInput.value && !monthInput.value && !dayInput.value;
      if (!allEmpty || !gregorianInput.value) return;

      let response;
      try {
        response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": csrfToken,
          },
          body: `date=${encodeURIComponent(gregorianInput.value)}`,
        });
      } catch {
        return;
      }
      if (!response.ok) return;
      const data = await response.json();

      yearInput.value = data.year;
      monthInput.value = data.month;
      dayInput.value = data.day;
      if (note) note.hidden = false;
    });
  });
}
