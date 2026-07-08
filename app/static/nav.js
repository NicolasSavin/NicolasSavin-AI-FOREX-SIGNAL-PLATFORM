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

  nav.querySelectorAll('a').forEach((link) => {
    const label = (link.textContent || '').trim().toLowerCase();
    if (label === 'api ideas' || label === 'api идеи') {
      link.href = '/tv';
      link.textContent = '🎥 FXPilot TV';
    }
  });

  if (!nav.querySelector('a[href="/committee"]')) {
    const committeeLink = document.createElement('a');
    committeeLink.href = '/committee';
    committeeLink.textContent = '🏛 Committee';
    nav.appendChild(committeeLink);
  }

  if (!nav.querySelector('a[href="/consensus"]')) {
    const consensusLink = document.createElement('a');
    consensusLink.href = '/consensus';
    consensusLink.textContent = '🤝 Consensus';
    nav.appendChild(consensusLink);
  }

  if (!nav.querySelector('a[href="/tv"]')) {
    const tvLink = document.createElement('a');
    tvLink.href = '/tv';
    tvLink.textContent = '🎥 FXPilot TV';
    nav.appendChild(tvLink);
  }

  nav.querySelectorAll('a[href]').forEach((link) => {
    const linkPath = normalize(link.getAttribute('href'));
    const isActive = linkPath === currentPath;
    link.classList.toggle('is-active', isActive);
    if (isActive) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
})();

(function initVisitorCounter() {
  const shell = document.querySelector('.page-shell') || document.body;
  if (!shell || document.querySelector('[data-visitor-counter]')) return;

  const counter = document.createElement('div');
  counter.className = 'visitor-counter';
  counter.setAttribute('data-visitor-counter', '');
  counter.setAttribute('aria-label', 'Счётчик посещений');
  counter.hidden = true;
  shell.appendChild(counter);

  const render = (payload) => {
    const today = Number(payload && payload.today);
    const total = Number(payload && payload.total);
    if (!Number.isFinite(today) || !Number.isFinite(total)) return;
    counter.textContent = `👁 Сегодня: ${today} · Всего: ${total}`;
    counter.hidden = false;
  };

  const load = async () => {
    try {
      const visitedKey = 'fxpilot_visit_counted';
      const shouldIncrement = window.sessionStorage.getItem(visitedKey) !== '1';
      const response = await fetch(`/api/visits${shouldIncrement ? '?increment=true' : ''}`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (shouldIncrement) window.sessionStorage.setItem(visitedKey, '1');
      render(payload);
    } catch (_) {
      counter.hidden = true;
    }
  };

  load();
})();
