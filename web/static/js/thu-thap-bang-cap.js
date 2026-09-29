(function () {
  const search = document.getElementById('certSearch');
  const nav = document.querySelector('.cert-nav');
  const groups = Array.from(document.querySelectorAll('.cert-group'));
  const empty = document.getElementById('certEmpty');
  if (!search || !nav || !groups.length) return;

  let activeGroup = 'all';

  function applyFilter() {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    groups.forEach((group) => {
      const groupOn = activeGroup === 'all' || group.dataset.group === activeGroup;
      let count = 0;
      group.querySelectorAll('.cert-item').forEach((item) => {
        const match = !query || (item.dataset.search || '').includes(query);
        const show = groupOn && match;
        item.classList.toggle('d-none', !show);
        if (show) count += 1;
      });
      group.classList.toggle('d-none', count === 0);
      visible += count;
    });
    if (empty) empty.classList.toggle('d-none', visible !== 0);
  }

  nav.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-group]');
    if (!button) return;
    activeGroup = button.dataset.group || 'all';
    nav.querySelectorAll('button[data-group]').forEach((item) => {
      item.classList.toggle('active', item === button);
    });
    applyFilter();
  });

  search.addEventListener('input', applyFilter);
})();
