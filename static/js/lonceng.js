// The bell in the header: the signed-in user's tasks, loaded after the page so no page waits for them.
(function () {
  const REFRESH_MS = 60000;
  const bell = document.querySelector('[data-lonceng-url]');
  if (!bell) return;
  const list = document.getElementById('notification-list');
  const count = bell.querySelector('.lonceng-jumlah');

  async function refresh() {
    if (document.visibilityState !== 'visible') return;
    try {
      const response = await fetch(bell.dataset.loncengUrl, { credentials: 'same-origin' });
      // Signed out meanwhile: the answer is the login page, so keep what is shown.
      if (!response.ok || response.redirected) return;
      list.innerHTML = await response.text();  // rendered and escaped by the server
      const total = Number(list.querySelector('[data-total]').dataset.total);
      count.textContent = total > 99 ? '99+' : total;
      count.hidden = total === 0;
    } catch (error) {
      console.warn('Tugas di lonceng belum bisa dimuat', error);
    }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
  document.addEventListener('visibilitychange', refresh);
})();
