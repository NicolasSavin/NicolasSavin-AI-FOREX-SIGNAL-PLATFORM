(function activateTopNav() {
  const nav = document.querySelector('.top-nav');
  if (!nav) return;

  const currentPath = window.location.pathname.replace(/\/+$/, '') || '/';
  const normalize = (path) => (path || '/').replace(/\/+$/, '') || '/';

  nav.querySelectorAll('a[href]').forEach((link) => {
    const linkPath = normalize(link.getAttribute('href'));
    const isActive = linkPath === currentPath;
    link.classList.toggle('is-active', isActive);
    if (isActive) {
      link.setAttribute('aria-current', 'page');
    } else {
      link.removeAttribute('aria-current');
    }
  });
})();
