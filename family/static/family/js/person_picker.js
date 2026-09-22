/* Turns a father/mother/existing_spouse <select> (see
 * family.forms.PersonPickerSelect) into a type-to-filter Tom Select
 * combobox with a multi-line option: display name + birth year on top,
 * Hebrew first name and a "child of .../spouse of .../parent of ..."
 * relations hint as subtitles
 * underneath - plain text alone can't disambiguate a ledger's worth of
 * repeated names (two "Blimi Rokach"s is normal), see AGENTS.md. Found
 * for real: a two-person cycle created by picking the wrong same-named,
 * no-other-distinguishing-info person from this exact picker.
 *
 * Tom Select doesn't read arbitrary data-* attributes off the source
 * <option> elements on its own (only value/text/disabled) - confirmed
 * against the library directly, not just assumed. So this reads each
 * option's .dataset itself, after the Tom Select instance exists, and
 * merges the extra fields in with updateOption() before anything
 * renders.
 */
function initPersonPicker(selector) {
  document.querySelectorAll(selector).forEach((el) => {
    const sourceOptions = Array.from(el.options);
    const ts = new TomSelect(el, {
      create: false,
      // Tom Select's own default (50) silently truncates the dropdown
      // well short of a real ledger's size (a couple hundred people is
      // normal here, see AGENTS.md) - scrolling to the end looks like
      // "that's everyone" when it's really just wherever the cap cut
      // off. This is a render cap only, not a search-result cap - Tom
      // Select still searches every underlying <option> regardless -
      // so it only ever bit browsing the full list with no search text
      // typed yet.
      maxOptions: 10000,
      // Search only ever matches the person's own name - never "text"
      // (the full rendered label, which also contains the birth year and
      // the relations hint). A relations hint names *other* people
      // ("spouse of Bruchele Rokach"), so searching against it would
      // surface someone whose own name isn't what was typed at all -
      // exactly the kind of ambiguity this picker exists to prevent.
      // Hebrew first name is real search text too, not just a subtitle -
      // typing it should filter the list the same as typing the English
      // name does.
      searchField: ["displayName", "hebrewFirstName"],
      render: {
        option: (data, escape) => renderRow(data, escape),
        item: (data, escape) =>
          `<div>${escape(data.displayName || data.text)}</div>`,
      },
    });

    sourceOptions.forEach((opt) => {
      if (!opt.value) return;
      const existing = ts.options[opt.value];
      if (!existing) return;
      ts.updateOption(
        opt.value,
        Object.assign({}, existing, {
          displayName: opt.dataset.displayName || "",
          hebrewFirstName: opt.dataset.hebrewFirstName || "",
          birthYear: opt.dataset.birthYear || "",
          relationsHint: opt.dataset.relationsHint || "",
          warning: opt.dataset.warning || "",
        }),
      );
    });
  });
}

function renderRow(data, escape) {
  const name = data.displayName || data.text;
  const hebrew = data.hebrewFirstName
    ? `<div class="ts-option-hebrew">${escape(data.hebrewFirstName)}</div>`
    : "";
  const relations = data.relationsHint
    ? `<div class="ts-option-relations">${escape(data.relationsHint)}</div>`
    : "";
  const warning = data.warning
    ? `<div class="ts-option-warning">${escape(data.warning)}</div>`
    : "";
  return `
    <div class="ts-option-row">
      <div class="ts-option-top">
        <span class="ts-option-name">${escape(name)}</span>
        ${
          data.birthYear
            ? `<span class="ts-option-year">${escape(data.birthYear)}</span>`
            : ""
        }
      </div>
      ${hebrew}
      ${relations}
      ${warning}
    </div>
  `;
}
