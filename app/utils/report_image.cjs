const fs = require('node:fs');

const selector = id => `[data-mod="${id}"]`;

async function capture({ html, groups, data, sampledAt }) {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch {
    throw new Error('日报图需要安装 Playwright');
  }
  const origin = 'http://fnmonitor-report.invalid';
  const order = groups.map(group => group.id);
  const included = new Set(order.filter(id => id !== 'weather'));
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: process.env.FNMONITOR_REPORT_BROWSER || undefined,
      args: ['--no-proxy-server'],
      timeout: 10000,
    });
  } catch {
    throw new Error('无法启动日报截图浏览器，请检查 Chromium 是否已安装并可运行');
  }
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: 'reduce',
      serviceWorkers: 'block',
      offline: true,
    });
    await context.route('**/*', async route => {
      const request = route.request();
      const target = new URL(request.url());
      // Every document and API response is supplied in memory.
      if (target.origin !== origin || request.method() !== 'GET') return route.abort();
      if (request.isNavigationRequest() && target.pathname === '/') {
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      }
      const key = target.pathname + target.search;
      if (!Object.hasOwn(data, key)) return route.abort();
      return route.fulfill({ json: data[key] });
    });

    const hidden = data['/api/ui']?.ui?.hidden;
    if (Array.isArray(hidden?.overview)) hidden.overview = hidden.overview.filter(id => !included.has(id));

    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    const endpoints = ['/api/overview', '/api/ui'];
    if (included.has('energy')) endpoints.push('/api/power_stats');
    if (included.has('network')) endpoints.push('/api/traffic');
    if (order.includes('weather')) endpoints.push('/api/weather');
    if (included.has('storage')) endpoints.push('/api/disk_health');
    const pending = endpoints.map(path => page.waitForResponse(r => {
      const target = new URL(r.url());
      return target.origin === origin && target.pathname === path && r.ok();
    }));
    await Promise.all([...pending, page.goto(origin + '/#/overview', { waitUntil: 'domcontentloaded' })]);
    const layout = page.locator('#layout-overview');
    await layout.waitFor({ state: 'attached' });
    await page.addStyleTag({ content: `
      html { scroll-behavior: auto !important; }
      body > header { visibility: hidden !important; }
    ` });

    for (const id of included) {
      const section = layout.locator(selector(id));
      await section.scrollIntoViewIfNeeded();
      if (id === 'energy' || id === 'network') {
        await section.locator('[data-ref="bars"] svg g[data-i], [data-ref="barE"]:not([hidden])').first()
          .waitFor({ state: 'attached' });
      } else if (id === 'storage') {
        await section.locator('[data-ref="bays"] > *').first().waitFor({ state: 'attached' });
      } else if (id === 'host') {
        await section.locator('[data-ref="a"] > *').first().waitFor({ state: 'attached' });
      }
    }
    await page.evaluate(({ groups, sampledAt }) => {
      const layout = document.querySelector('#layout-overview');
      const order = groups.map(group => group.id);
      const blocks = new Map();
      for (const section of layout.querySelectorAll(':scope > [data-mod]')) {
        blocks.set(section.dataset.mod, section);
        if (!order.includes(section.dataset.mod)) section.style.display = 'none';
      }
      if (order.includes('weather')) blocks.set('weather', document.querySelector('#hero [data-ref="wx"]'));
      let number = 0;
      for (const { id, hide } of groups) {
        const block = blocks.get(id);
        if (!block) continue;
        layout.append(block);
        for (const selector of hide) {
          const card = block.querySelector(selector);
          if (card) card.style.display = 'none';
        }
        if (hide.length) {
          const description = block.querySelector('.mod-dek');
          if (description) description.style.display = 'none';
          for (const grid of block.querySelectorAll('.g-compute, .g-thermal, .g-2, .energy')) {
            const visible = [...grid.children].filter(card => getComputedStyle(card).display !== 'none');
            if (visible.length === 1) visible[0].style.gridColumn = '1 / -1';
          }
        }
        const index = block.querySelector('[data-ref="idx"]');
        if (index && !block.classList.contains('mod-headless')) {
          index.textContent = String(++number).padStart(2, '0');
        }
      }
      layout.style.padding = '16px';
      const stamp = document.createElement('div');
      stamp.className = 'eyebrow';
      stamp.textContent = '采集时间 ' + sampledAt;
      layout.prepend(stamp);
    }, { groups, sampledAt });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    return await layout.screenshot({ animations: 'disabled' });
  } finally {
    await browser.close();
  }
}

capture(JSON.parse(fs.readFileSync(0, 'utf8'))).then(
  png => process.stdout.write(png),
  error => {
    console.error(error.name === 'TimeoutError' ? '等待首页数据或图表渲染超时' : error.message);
    process.exitCode = 1;
  },
);
