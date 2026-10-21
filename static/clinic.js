document.querySelectorAll('[data-nav]').forEach(link => {
  if (window.location.pathname.split('/')[1] === link.dataset.nav) {
    link.classList.add('active');
    link.setAttribute('aria-current', 'page');
  }
});
document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
