(() => {
  const root = document.documentElement;
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const themeColor = document.querySelector('meta[name="theme-color"]');
  const storageKey = 'pufibara-theme';
  let theme = 'system';
  let darkMascot;

  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch {
    // The theme still works when browser storage is unavailable.
  }

  function applyTheme() {
    const dark = theme === 'dark' || (theme === 'system' && systemTheme.matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    themeColor.content = dark ? '#211d19' : '#fcf9f4';
    if (darkMascot) darkMascot.media = dark ? 'all' : 'not all';
  }

  applyTheme();
  systemTheme.addEventListener('change', applyTheme);

  document.addEventListener('DOMContentLoaded', () => {
    darkMascot = document.querySelector('#dark-mascot');
    applyTheme();
    const control = document.querySelector('.theme-control');
    const summary = control.querySelector('summary');
    const options = control.querySelectorAll('button[data-choice]');

    function updateControl() {
      control.dataset.choice = theme;
      for (const option of options) {
        const selected = option.dataset.choice === theme;
        option.setAttribute('aria-pressed', String(selected));
        if (selected) summary.setAttribute('aria-label', `Color theme: ${option.textContent.trim()}`);
      }
    }

    function closeMenu() {
      control.open = false;
      summary.focus();
    }

    updateControl();
    control.hidden = false;
    control.addEventListener('click', (event) => {
      const option = event.target.closest('button[data-choice]');
      if (!option) return;
      theme = option.dataset.choice;
      applyTheme();
      updateControl();
      closeMenu();
      try {
        if (theme === 'system') localStorage.removeItem(storageKey);
        else localStorage.setItem(storageKey, theme);
      } catch {
        // Keep the selected theme for this page.
      }
    });
    control.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !control.open) return;
      event.preventDefault();
      closeMenu();
    });
    document.addEventListener('click', (event) => {
      if (control.open && !control.contains(event.target)) control.open = false;
    });
    summary.addEventListener('click', () => {
      document.querySelector('.mobile-nav[open]')?.removeAttribute('open');
    });
  });
})();
