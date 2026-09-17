/* Shared appearance. Runs before paint; account configuration stays separate. */
(() => {
  'use strict';
  const KEY = 'maximus.appearance';
  const root = document.documentElement;
  const valid = value => value === 'dark' ? 'dark' : 'light';
  let mode = 'light';
  try { mode = valid(localStorage.getItem(KEY)); } catch { /* private browsing */ }

  function apply(value) {
    mode = valid(value);
    root.dataset.theme = mode;
    root.classList.toggle('dark', mode === 'dark');
    root.style.colorScheme = mode;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === mode));
    });
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-checked', String(mode === 'dark'));
    });
    document.querySelectorAll('[data-theme-name]').forEach(label => {
      label.textContent = mode === 'dark' ? 'Dark' : 'Light';
    });
  }
  function set(value) {
    apply(value);
    let saved = true;
    try { localStorage.setItem(KEY, mode); } catch { saved = false; }
    const status = document.getElementById('appearance-status');
    if (status) status.textContent = saved ? 'Saved for this browser. Applies across MAXIMUS.' : 'Applied for this page. Browser storage is unavailable.';
    window.dispatchEvent(new CustomEvent('maximus:themechange', {detail: {theme: mode}}));
  }
  apply(mode);
  window.MaximusTheme = Object.freeze({get: () => mode, set});
  window.addEventListener('storage', event => {
    if (event.key === KEY || event.key === null) apply(event.newValue);
  });

  function ready() {
    apply(mode);
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', () => set(button.dataset.themeChoice)));
    document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => set(mode === 'dark' ? 'light' : 'dark')));
    document.addEventListener('click', event => {
      const suggestion = event.target.closest('[data-prompt]');
      const input = document.getElementById('userInput');
      if (suggestion && input) { input.value = suggestion.dataset.prompt; input.focus(); }
    });

    // Keep existing page navigation and add an accessible compact alternative.
    const pages = ['settings', 'profile-settings', 'history', 'speakWithAI', 'apipro', 'overview'];
    const header = document.querySelector('body > header');
    if (header && pages.includes(root.dataset.page)) {
      const menu = document.createElement('details');
      menu.className = 'maximus-mobile-nav';
      const summary = document.createElement('summary');
      summary.textContent = 'Navigate';
      menu.append(summary);
      const nav = document.createElement('nav');
      nav.setAttribute('aria-label', 'Mobile navigation');
      [['/home','Chat'],['/overview','Overview'],['/speakwithai','Live AI'],['/history','History'],['/api-pro','Providers'],['/settings','Settings']].forEach(([href,label]) => {
        const link = document.createElement('a'); link.href=href; link.textContent=label;
        if (location.pathname === href) link.setAttribute('aria-current','page');
        nav.append(link);
      });
      menu.append(nav); header.append(menu);
      document.addEventListener('keydown', event => { if(event.key === 'Escape') {menu.open=false;} });
      document.addEventListener('click', event => { if(!menu.contains(event.target)) menu.open=false; });
    }

    const sidebar = document.getElementById('sidebar');
    const trigger = document.getElementById('mobileSidebarToggle');
    const backdrop = document.getElementById('mobileSidebarBackdrop');
    if (sidebar && trigger && backdrop) {
      const mobile = window.matchMedia('(max-width: 767px)');
      function close() {
        root.classList.remove('sidebar-open'); trigger.setAttribute('aria-expanded','false');
        sidebar.inert = mobile.matches;
      }
      trigger.addEventListener('click', () => {
        const opening = !root.classList.contains('sidebar-open');
        root.classList.toggle('sidebar-open', opening); trigger.setAttribute('aria-expanded', String(opening));
        sidebar.inert = !opening && mobile.matches;
        if (opening) sidebar.querySelector('a,button')?.focus();
      });
      backdrop.addEventListener('click', () => {close();trigger.focus();});
      sidebar.addEventListener('click', event => {if(event.target.closest('a,button')) close();});
      document.addEventListener('keydown', event => {
        if(event.key === 'Escape' && root.classList.contains('sidebar-open')) {close();trigger.focus();}
        if(event.key === 'Tab' && mobile.matches && root.classList.contains('sidebar-open')) {
          const items = [...sidebar.querySelectorAll('a[href],button:not([disabled]),input,select')].filter(el=>el.getClientRects().length);
          const first=items[0], last=items[items.length-1];
          if(event.shiftKey && document.activeElement===first) {event.preventDefault();last?.focus();}
          else if(!event.shiftKey && document.activeElement===last) {event.preventDefault();first?.focus();}
        }
      });
      mobile.addEventListener('change', close); close();
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
