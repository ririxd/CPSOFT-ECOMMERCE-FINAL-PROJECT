// This static preview never sends or stores account credentials.
document.querySelectorAll('.auth-form').forEach((form) => {
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    form.querySelector('.auth-status').textContent =
      'Account access is not available yet. Please come back soon.';
    form.querySelector('[name="password"]').value = '';
  });
  form.querySelector('[type="submit"]').disabled = false;
});
