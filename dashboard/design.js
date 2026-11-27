/* AF-53 — tiny theme/nav glue for the unified design system. No framework. */
(function () {
  const root = document.documentElement;
  const saved = localStorage.getItem('af.theme');
  if (saved === 'light' || saved === 'dark') root.dataset.theme = saved;
  else if (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches)
    root.dataset.theme = 'light';

  const apply = () => {
    const b = document.querySelector('.af-theme');
    if (b) b.textContent = root.dataset.theme === 'light' ? '☀️' : '🌙';
  };
  apply();
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('.af-theme');
    if (!b) return;
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('af.theme', root.dataset.theme);
    apply();
  });
  const here = location.pathname;
  document.querySelectorAll('.af-nav a[data-page]').forEach((a) => {
    if (a.getAttribute('data-page') === here ||
        (here.indexOf('workspace') > -1 && a.dataset.page === 'workspace') ||
        (here.indexOf('factory') > -1 && a.dataset.page === 'deliver')) {
      a.classList.add('active');
    }
  });
})();
