/* File attachments (drag/drop or paste an image into the editor) are
 * disabled outright - Trix's default toolbar/behavior supports them, but
 * there's no upload endpoint for it to hand a file to, and broadcasts
 * are text-only by design (see family.widgets.TrixEditorWidget and
 * notifications.models.Broadcast's tag allowlist, which drops <img>
 * regardless). Cancelling trix-file-accept stops the attachment from
 * being added at all, rather than letting it appear to work and then
 * silently vanish on save once the sanitizer strips it. The toolbar's
 * own attach-file button is hidden via app.css.
 */
function initBroadcastEditor() {
  document.addEventListener("trix-file-accept", (event) => {
    event.preventDefault();
  });
}
