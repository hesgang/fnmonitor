const [groups, done] = arguments;

async function prepareReport() {
  await window.fnmonitorReportReady(groups.map(group => group.id));
  const page = document.querySelector('#page-overview');
  const hero = page.querySelector('#hero');
  const layout = page.querySelector('#layout-overview');
  const byId = new Map(groups.map(group => [group.id, group]));
  const hide = element => element.style.setProperty('display', 'none', 'important');

  function filterCards(section, group) {
    if (!group || section.hidden || section.classList.contains('off')) {
      hide(section);
      return false;
    }
    const kept = new Set(section.querySelectorAll(group.keep.join(',')));
    for (const card of section.querySelectorAll(group.cards.join(','))) {
      if (!kept.has(card)) hide(card);
    }
    if (![...kept].some(card => card.getClientRects().length &&
        (!card.matches('[data-ref="raid"], [data-ref="c"]') || card.childElementCount))) {
      hide(section);
      return false;
    }
    for (const grid of section.querySelectorAll('.grid:not(.metrics), .energy')) {
      if ([...grid.children].every(card => getComputedStyle(card).display === 'none')) hide(grid);
    }
    return true;
  }

  hide(document.querySelector('#topbar'));
  page.style.paddingBottom = '24px';
  const heroGroup = byId.get('hero');
  let visible = filterCards(hero, heroGroup);
  if (visible && heroGroup.keep.length === 1) {
    if (heroGroup.keep[0] === '.clock-card') {
      hero.style.gridTemplateColumns = 'minmax(0, 340px)';
    } else {
      hero.style.gridTemplateColumns = 'auto minmax(0, 1fr)';
    }
  }
  for (const section of layout.querySelectorAll(':scope > [data-mod]')) {
    visible = filterCards(section, byId.get(section.dataset.mod)) || visible;
  }
  if (!visible) throw new Error('选中的卡片在当前总览页没有显示');

  await document.fonts.ready;
  scrollTo(0, 0);
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const rect = page.getBoundingClientRect();
  return { x: rect.x + scrollX, y: rect.y + scrollY, width: rect.width, height: rect.height };
}

prepareReport().then(done, error => done({ error: error.message }));
