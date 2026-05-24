(() => {
  const originalFetch = window.fetch.bind(window);

  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : input?.url || '';
    if (url === '/api/chat' && init && typeof init.body === 'string') {
      try {
        const payload = JSON.parse(init.body);
        if (payload?.context?.use_fundamental === true) {
          payload.context.requested_fundamental = true;
          payload.context.use_fundamental = false;
          init = { ...init, body: JSON.stringify(payload) };
          console.warn('analytics_ai_hotfix: disabled backend :online suffix path for /api/chat; Grok model is still used without OpenRouter :online suffix.');
        }
      } catch (error) {
        console.warn('analytics_ai_hotfix_parse_failed', error);
      }
    }
    return originalFetch(input, init);
  };
})();
