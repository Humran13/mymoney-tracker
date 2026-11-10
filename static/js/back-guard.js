// Warns before following a Back link away from a form the user has
// started filling in, so a stray tap on mobile doesn't silently discard
// typed-in financial data. A form only becomes "dirty" on a real
// input/change from the user, not just because fields have server-filled
// initial values.
let formGuardDirty = false;

document.addEventListener("input", (event) => {
  if (event.target.closest("form")) formGuardDirty = true;
});
document.addEventListener("change", (event) => {
  if (event.target.closest("form")) formGuardDirty = true;
});
// A page that just submitted (form-guard.js already disabled its button)
// is navigating away on purpose -- don't warn about that submission.
document.addEventListener("submit", () => {
  formGuardDirty = false;
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-confirm-leave]");
  if (!link || !formGuardDirty) return;
  if (!window.confirm("You have unsaved changes. Leave this page without saving?")) {
    event.preventDefault();
  }
});
