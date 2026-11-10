// Prevents duplicate submissions (double-click/double-tap) on any form in
// the app -- most importantly transfers and other money-moving forms,
// where a second accidental submit would create a second real transaction.
// Does not call preventDefault, so it never interferes with htmx's own
// submit handling; it only disables the submit button(s) a tick after the
// browser has already captured the form data for its first submission.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
  if (!buttons.length) return;
  // Disabling synchronously here does not cancel *this* submission -- the
  // browser has already committed to it by the time a 'submit' listener
  // runs -- it only stops a second click/tap from firing a second one.
  buttons.forEach((btn) => {
    btn.disabled = true;
    btn.dataset.formGuardDisabled = "1";
  });
  // Safety net: if the page is still here 10s later (validation error,
  // network failure, htmx error response), re-enable so the user isn't
  // stuck unable to retry.
  setTimeout(() => {
    buttons.forEach((btn) => {
      if (btn.dataset.formGuardDisabled) {
        btn.disabled = false;
        delete btn.dataset.formGuardDisabled;
      }
    });
  }, 10000);
});
