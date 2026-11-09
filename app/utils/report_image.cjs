const fs = require('node:fs');

const sections = {
  system: 'host',
  cpu: 'metrics', memory: 'metrics', load: 'metrics',
  gpu: 'compute',
  disk: 'storage', smart: 'storage', storage: 'storage',
  network: 'network', power: 'energy',
  temp: 'thermal', fan: 'thermal',
};
const selector = id => `[data-mod="${id}"]`;

async function capture({ url, modules, session }) {
  const selected = new Set(modules);
  const unsupported = { docker: 'Docker 容器', vms: '虚拟机', idrac: 'iDRAC 硬件' };
  const missing = [...selected].filter(id => !Object.hasOwn(sections, id) && id !== 'weather');
  if (missing.length) {
    throw new Error('总览没有这些报告模块的独立区块：' + missing.map(id => unsupported[id] || id).join('、'));
  }
  if (!selected.size) throw new Error('请至少选择一个报告模块');

  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch {
    throw new Error('日报图需要安装 Playwright');
  }
  const origin = new URL(url).origin;
  const included = new Set([...selected].map(id => sections[id]).filter(Boolean));
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
    });
    if (session) await context.addCookies([{ name: 'fnmp_sid', value: session, url: origin, httpOnly: true }]);
    await context.route('**/*', async route => {
      const request = route.request();
      const target = new URL(request.url());
      // Prevent UI writes and requests to other hosts from the capture session.
      if (target.origin !== origin || request.method() !== 'GET' || target.pathname === '/api/notify/report_image') {
        return route.abort();
      }
      if (target.pathname === '/api/ui') {
        const response = await route.fetch({ maxRedirects: 0 });
        if (!response.ok()) return route.fulfill({ response });
        const data = await response.json();
        const hidden = data.ui?.hidden;
        if (Array.isArray(hidden?.overview)) hidden.overview = hidden.overview.filter(id => !included.has(id));
        return route.fulfill({ response, json: data });
      }
      return route.continue();
    });

    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    const endpoints = ['/api/overview', '/api/ui'];
    if (selected.has('power')) endpoints.push('/api/power_stats');
    if (selected.has('network')) endpoints.push('/api/traffic');
    if (selected.has('weather')) endpoints.push('/api/weather');
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
    await page.evaluate(({ keep, chosen }) => {
      const layout = document.querySelector('#layout-overview');
      for (const section of layout.querySelectorAll(':scope > [data-mod]')) {
        if (!keep.includes(section.dataset.mod)) section.style.display = 'none';
      }
      const hide = selectors => {
        for (const selector of selectors) {
          const element = layout.querySelector(selector);
          if (element) element.style.display = 'none';
        }
      };
      const metrics = [];
      if (chosen.includes('cpu') || chosen.includes('load')) metrics.push('cpu');
      if (chosen.includes('memory')) metrics.push('mem');
      for (const id of ['cpu', 'mem', 'disk', 'net', 'temp', 'pwr']) {
        if (!metrics.includes(id)) hide([`[data-mod="metrics"] [data-m="${id}"]`]);
      }
      if (!chosen.includes('disk') && !chosen.includes('smart')) hide(['[data-mod="storage"] .nas']);
      if (!chosen.includes('storage')) hide(['[data-mod="storage"] .pool', '[data-mod="storage"] [data-ref="raid"]']);
      if (chosen.includes('weather')) layout.prepend(document.querySelector('#hero [data-ref="wx"]'));
      layout.style.padding = '16px';
    }, { keep: [...included], chosen: [...selected] });
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
