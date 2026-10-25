// Laadt elke pagina van het dashboard in een echte browser (telefoon en desktop) en faalt bij:
// JavaScript-fouten, schendingen van het beveiligingsbeleid (CSP), zijwaarts scrollen, of pagina's die leeg blijven.
// Gebruikt de data die in de repository staat. Geen netwerk naar buiten: alleen de eigen bestanden.
// Lokaal: node tests/test_pages.js   (PLAYWRIGHT=/pad/naar/playwright om een andere installatie te gebruiken)
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const lookupFixture = require('./fixture_opzoeken');

const ROOT = path.resolve(__dirname, '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };

// Eenvoudige webserver op de repository (zoals GitHub Pages, zonder caching); lege opzoeklijst: testdata (fixture_opzoeken.js)
function serve() {
  const fixture = lookupFixture(ROOT) || {};
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
      if (fixture[rel]) { res.writeHead(200, { 'Content-Type': TYPES['.json'] }); res.end(JSON.stringify(fixture[rel])); return; }
      const file = path.join(ROOT, rel);
      if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

const PAGES = ['index.html', 'vandaag.html', 'portfolio.html', 'beoordeling.html', 'beoordeling.html?set=top',
  'signals.html', 'signals.html?set=top250', 'archief.html', 'archief.html?set=top250', 'screener.html',
  'koopmoment.html', 'koopmoment.html?set=own', 'opzoeken.html'];
const WIDTHS = [390, 1280];

(async () => {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch();
  const problems = [];
  let n = 0;
  for (const w of WIDTHS) {
    for (const pg of PAGES) {
      const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, hasTouch: w < 1000, isMobile: w < 1000 });
      const page = await ctx.newPage();
      const errs = [];
      page.on('pageerror', e => errs.push('JS: ' + e.message));
      page.on('console', m => {
        const t = m.text();
        if (m.type() !== 'error') return;
        if (/ERR_FAILED|ERR_BLOCKED|404|Failed to load resource/.test(t)) return;   // geblokkeerde GitHub-aanroepen, ontbrekende optionele data
        errs.push('console: ' + t.slice(0, 200));
      });
      page.on('console', m => { if (/Content Security Policy|Refused to/.test(m.text())) errs.push('CSP: ' + m.text().slice(0, 200)); });
      // alleen de eigen site; GitHub-API (sync) en al het andere blokkeren
      await page.route('**/*', r => r.request().url().startsWith(base) ? r.continue() : r.abort());
      await page.goto(base + pg);
      await page.waitForTimeout(2500);
      const info = await page.evaluate(() => ({
        shown: getComputedStyle(document.documentElement).display !== 'none',
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        text: (document.body.innerText || '').trim().length
      }));
      if (!info.shown) errs.push('pagina blijft verborgen (antiframe)');
      if (info.overflow > 1) errs.push(`zijwaarts scrollen: ${info.overflow}px te breed`);
      if (info.text < 40) errs.push('pagina lijkt leeg');
      // belangrijkste interactie per pagina
      try {
        if (pg === 'screener.html' && !(await page.$('.stat[data-q="gap"]'))) errs.push('tegel Find the gap ontbreekt');
        if (pg === 'screener.html' && await page.$('.stock')) {
          await page.click('.stock .toggle');
          await page.waitForSelector('.stock.open .detail', { timeout: 8000 });
        }
        if (pg === 'beoordeling.html' && await page.$('.b-card .b-head')) {
          await page.click('.b-card .b-head');
          await page.waitForSelector('.b-body', { timeout: 8000 });
        }
        if (pg === 'portfolio.html') {                     // positie toevoegen: zoeken op naam vult de ticker in
          await page.click('#add');
          await page.fill('#f-search', 'nvidia');
          await page.waitForSelector('#f-results .z-l', { timeout: 8000 });
          await page.click('#f-results .z-l');
          if (!(await page.inputValue('#f-ticker'))) errs.push('zoeken: tikken op een notering vult de ticker niet in');
          await page.click('#f-cancel');
        }
        if (pg === 'opzoeken.html') {                      // zoeken op naam, zonder token: uitleg in plaats van toevoegen; kaart openklappen
          await page.fill('#q', 'nvidia');
          await page.waitForSelector('#results .z-co', { timeout: 8000 });
          await page.click('#results .z-co');
          if (!/token/.test(await page.textContent('#note'))) errs.push('opzoeken zonder token: geen uitleg');
          await page.click('.b-card .b-head .toggle');
          await page.waitForSelector('.b-card .b-body .s12-step', { timeout: 8000 });
        }
        if (pg === 'index.html') {
          for (const v of ['overzicht', 'score', 'radar', 'archief', 'koop']) await page.click(`.seg.view button[data-view="${v}"]`);
          await page.click('.seg.world button[data-world="zoek"]');
          if (!(await page.$('iframe#zoek.active'))) errs.push('tab Opzoeken opent niet');
        }
      } catch (e) { errs.push('interactie: ' + e.message.split('\n')[0]); }
      await page.waitForTimeout(300);
      if (errs.length) problems.push(`${w}px ${pg}:\n    ` + [...new Set(errs)].join('\n    '));
      n++;
      await ctx.close();
    }
  }
  await browser.close();
  srv.close();
  if (problems.length) {
    console.log(`PAGINA'S: ${problems.length} van ${n} met problemen\n  ` + problems.join('\n  '));
    process.exit(1);
  }
  console.log(`PAGINA'S: alle ${n} weergaven zonder fouten`);
})().catch(e => { console.error(e); process.exit(1); });
