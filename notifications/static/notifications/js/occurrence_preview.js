/* One shared <dialog> (family/templates/family/dashboard.html) reused for
 * every "Preview" button on the Upcoming page, rather than one dialog per
 * occurrence - the content is fetched and swapped in on click, not
 * pre-rendered for every row up front (see notifications.views.
 * OccurrencePreviewView's own docstring: rendering an email costs a real
 * css_inline() call, not worth paying for every occurrence on every page
 * load when most will never be clicked).
 */
function initOccurrencePreview() {
  const dialog = document.getElementById("occurrence-preview-dialog");
  if (!dialog) return;
  const body = dialog.querySelector(".occurrence-preview-body");
  // Aborts whichever fetch is still in flight the moment a new "Preview"
  // is clicked - without this, clicking occurrence A then quickly
  // clicking occurrence B could still let A's response land last (render
  // cost varies per occurrence), silently showing A's content in a
  // dialog the user opened for B.
  let activeRequest = null;

  document.querySelectorAll(".occurrence-preview-trigger").forEach((button) => {
    button.addEventListener("click", () => {
      if (activeRequest) activeRequest.abort();
      const controller = new AbortController();
      activeRequest = controller;

      body.innerHTML = '<p class="muted">Loading preview&hellip;</p>';
      dialog.showModal();
      fetch(button.dataset.previewUrl, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
      })
        .then((response) => {
          if (!response.ok)
            throw new Error(`Preview request failed (${response.status})`);
          return response.text();
        })
        .then((html) => {
          body.innerHTML = html;
        })
        .catch((error) => {
          if (error.name === "AbortError") return;
          body.innerHTML =
            '<p class="muted">Couldn\'t load this preview. Try again.</p>';
        });
    });
  });
}
