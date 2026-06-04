(function activateTopNav() {
  const nav = document.querySelector('.top-nav');
  if (!nav) return;

  const currentPath = window.location.pathname.replace(/\/+$/, '') || '/';
  const normalize = (path) => (path || '/').replace(/\/+$/, '') || '/';

  const analyticsLink = nav.querySelector('a[href="/analytics"]');
  [['/stats', 'Статистика'], ['/archive', 'Архив']].forEach(([href, label]) => {
    if (nav.querySelector(`a[href="${href}"]`)) return;
    const link = document.createElement('a');
    link.href = href;
    link.textContent = label;
    nav.insertBefore(link, analyticsLink);
  });

  nav.querySelectorAll('a[href]').forEach((link) => {
    const linkPath = normalize(link.getAttribute('href'));
    const isActive = linkPath === currentPath;
    link.classList.toggle('is-active', isActive);
    if (isActive) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
})();
