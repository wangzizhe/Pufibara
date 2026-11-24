(() => {
  const root = document.documentElement;
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const themeColor = document.querySelector('meta[name="theme-color"]');
  const storageKey = 'pufibara-theme';
  let theme;
  let toggle;
  const mascotReady = {};

  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch {
    // The theme still works when browser storage is unavailable.
  }

  function systemScheme() {
    return systemTheme.matches ? 'dark' : 'light';
  }

  function selectedScheme() {
    return theme ?? systemScheme();
  }

  function oppositeScheme(scheme) {
    return scheme === 'dark' ? 'light' : 'dark';
  }

  function updateToggle() {
    if (!toggle) return;
    const label = `Switch to ${oppositeScheme(root.dataset.theme)} theme`;
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
  }

  async function applyTheme() {
    const scheme = selectedScheme();
    if (mascotReady[scheme]) await mascotReady[scheme];
    if (scheme !== selectedScheme()) return;
    root.dataset.theme = scheme;
    themeColor.content = scheme === 'dark' ? '#211d19' : '#fcf9f4';
    updateToggle();
  }

  applyTheme();
  systemTheme.addEventListener('change', applyTheme);

  document.addEventListener('DOMContentLoaded', () => {
    for (const image of document.querySelectorAll('[data-mascot-theme]')) {
      mascotReady[image.dataset.mascotTheme] = image.decode().catch(() => {});
    }
    toggle = document.querySelector('.theme-toggle');
    updateToggle();
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      const next = oppositeScheme(selectedScheme());
      theme = next === systemScheme() ? undefined : next;
      document.querySelector('.mobile-nav[open]')?.removeAttribute('open');
      applyTheme();
      try {
        if (theme) localStorage.setItem(storageKey, theme);
        else localStorage.removeItem(storageKey);
      } catch {
        // Keep the selected theme for this page.
      }
    });
  });
})();
