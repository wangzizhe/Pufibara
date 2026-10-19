const pilotForm = document.querySelector('.pilot-form');

if (pilotForm) {
  // Resolve at runtime so the redirect works on GitHub Pages project paths too.
  const nextPage = document.createElement('input');
  nextPage.type = 'hidden';
  nextPage.name = '_next';
  nextPage.value = new URL(pilotForm.dataset.successPage, window.location.href).href;
  pilotForm.append(nextPage);

  const submitButton = pilotForm.querySelector('button[type="submit"]');
  const originalContent = submitButton.innerHTML;

  // Native validation and FormSubmit's CAPTCHA remain in the submission path.
  // This event runs only after browser validation has passed.
  pilotForm.addEventListener('submit', (event) => {
    if (event.defaultPrevented) return;
    if (submitButton.disabled) {
      event.preventDefault();
      return;
    }
    submitButton.disabled = true;
    submitButton.textContent = pilotForm.dataset.submitting;
  });

  // Restore the button when returning from a failed or cancelled submission.
  window.addEventListener('pageshow', () => {
    submitButton.disabled = false;
    submitButton.innerHTML = originalContent;
  });
}
