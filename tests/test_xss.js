// Beveiliging: kan tekst van buiten (bedrijfsnamen, nieuwskoppen, sectoren, beurzen) of je eigen invoer (namen van
// posities, notities) als HTML of code in een pagina belanden? Vult al die velden in de databestanden met een
// HTML-lading, maakt links javascript:-links en faalt als er ook maar één ingevoegd element of zo'n link op een pagina
// staat. Tweede ronde: hetzelfde voor de vaste waarden die als CSS-klasse gebruikt worden (richting, status, uitkomst).
// Lokaal: node tests/test_xss.js   (PLAYWRIGHT=/pad/naar/playwright om een andere installatie te gebruiken)
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const lookupFixture = require('./fixture_opzoeken');

const ROOT = path.resolve(__dirname, '..');
const PAY = `"'><i data-xss="1"></i>`;
const TEXT = new Set(['name', 'title', 'publisher', 'about', 'sector', 'industry', 'country', 'exch', 'us_exch', 'exchange', 'exchangeFull',
  'etf_name', 'label', 'err', 'noot', 'note', 'detail', 'plus', 'warn', 'why', 'text', 'uitleg', 'c', 'm', 'patterns', 'skip', 'topic',
  'wijziging', 'rule', 'mult', 'oordeel']);
const ENUM = new Set(['dir', 'direction', 'outcome', 'status', 's', 'kind', 'lvl', 'regime', 'val', 'state', 'rating', 'branch', 'region',
  'end_reason', 'trend', 'set', 'k', 'key', 'pre', 'at10', 'on']);
const LINK = new Set(['link', 'url', 'href']);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
const PAGES = ['vandaag.html', 'portfolio.html', 'beoordeling.html', 'beoordeling.html?set=top', 'signals.html', 'signals.html?set=top250',
  'archief.html', 'archief.html?set=top250', 'screener.html', 'koopmoment.html', 'koopmoment.html?set=own', 'opzoeken.html'];

function taint(x, keys, k) {
  if (Array.isArray(x)) {
    if (k === 'rows') return x.map(r => Array.isArray(r) && keys === TEXT ? r.map((v, i) => i === 2 && typeof v === 'string' ? v + PAY : v) : r);
    return x.map(v => typeof v === 'string' ? (keys.has(k) ? v + PAY : v) : taint(v, keys, k));
  }
  if (x && typeof x === 'object') return Object.fromEntries(Object.entries(x).map(([kk, v]) => [kk, taint(v, keys, kk)]));
  if (typeof x === 'string' && k) {
    if (LINK.has(k)) return 'javascript:window.__xss=1';
    if (keys.has(k)) return x + PAY;
  }
  return x;
}

function serve(keys) {
  const cache = {}, fixture = lookupFixture(ROOT) || {};     // lege opzoeklijst: testdata, ook besmet
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
      const file = path.join(ROOT, rel);
      if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
      if (path.extname(file) === '.json') {
        try { cache[rel] = cache[rel] || JSON.stringify(taint(fixture[rel] || JSON.parse(fs.readFileSync(file, 'utf8')), keys)); }
        catch (e) { cache[rel] = fs.readFileSync(file, 'utf8'); }
        res.writeHead(200, { 'Content-Type': TYPES['.json'] }); res.end(cache[rel]); return;
      }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  const browser = await chromium.launch();
  const problems = [];
  let shown = 0;
  for (const [ronde, keys] of [['tekst', TEXT], ['klassen', ENUM]]) {
    const srv = await serve(keys);
    const base = `http://127.0.0.1:${srv.address().port}/`;
    for (const pg of PAGES) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      // je eigen invoer: een positie met een eigen naam, een verkoop en een notitie
      await ctx.addInitScript(p => {
        if (sessionStorage.getItem('xss-seeded')) return;
        sessionStorage.setItem('xss-seeded', '1');
        localStorage.setItem('portfolio_positions', JSON.stringify([{ ticker: 'AMD.DE', section: 'Stocks', label: 'AMD' + p, thresh: 20, cur: 'native' },
          { ticker: 'NVD.DE', section: 'Watchlist', label: 'NV' + p, thresh: 20, watch: true, cur: 'native' }]));
        localStorage.setItem('portfolio_version', '999');
        localStorage.setItem('portfolio_state', JSON.stringify({ 'Stocks|AMD.DE': { cost: 100, qty: 1,
          sells: [{ level: 1, pct: 10, date: '1-6-2026' + p, price: 150 }], review: { note: 'notitie' + p, updated: '2026-10-01' } } }));
      }, PAY);
      const page = await ctx.newPage();
      page.on('dialog', d => d.dismiss());
      await page.route('**/*', r => r.request().url().startsWith(base) ? r.continue() : r.abort());
      await page.goto(base + pg);
      await page.waitForTimeout(2000);
      // details openklappen (alleen weergave: knoppen die iets opslaan blijven dicht). Na elke ronde controleren:
      // een tweede klik op dezelfde knop klapt een kaart weer dicht.
      const r = { injected: [], js: [], ran: false, shown: 0 };
      for (let i = 0; i < 2; i++) {
        await page.evaluate(() => {
          document.querySelectorAll('details:not([open]) > summary').forEach(s => s.click());
          document.querySelectorAll('button.toggle, [data-view]').forEach(e => { try { e.click(); } catch (x) {} });
        });
        await page.waitForTimeout(700);
        const x = await page.evaluate(() => ({
          injected: [...document.querySelectorAll('[data-xss]')].map(e => (e.parentElement ? e.parentElement.outerHTML : '?').slice(0, 160)),
          js: [...document.querySelectorAll('[href^="javascript:" i], [src^="javascript:" i], [action^="javascript:" i]')].map(e => e.outerHTML.slice(0, 160)),
          ran: !!window.__xss,
          shown: (document.body.innerText.match(/data-xss/g) || []).length
        }));
        r.injected.push(...x.injected); r.js.push(...x.js); r.ran = r.ran || x.ran; r.shown = Math.max(r.shown, x.shown);
      }
      shown += r.shown;
      r.injected.slice(0, 3).forEach(h => problems.push(`${pg} (${ronde}): ingevoegde HTML: ${h}`));
      r.js.slice(0, 3).forEach(h => problems.push(`${pg} (${ronde}): javascript-link: ${h}`));
      if (r.ran) problems.push(`${pg} (${ronde}): ingevoegde code is uitgevoerd`);
      await ctx.close();
    }
    srv.close();
  }
  await browser.close();
  // de lading moet wel ergens als gewone tekst te zien zijn, anders test deze controle niets
  if (shown < 50) problems.push(`maar ${shown} keer de lading als tekst gezien: test dekt de pagina's niet meer`);
  problems.forEach(p => console.log('  FOUT', p));
  console.log(problems.length ? `XSS: ${problems.length} probleem/problemen` : `XSS: geen ingevoegde HTML of code op ${PAGES.length} pagina's (${shown} keer veilig als tekst getoond)`);
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
