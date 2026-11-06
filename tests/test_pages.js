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

// Een paar posities met aankoopprijs en aantal (en een gekochte trede), om de kooptrap, het risico en Vandaag met echte
// posities te zien; de andere ronde draait zonder (zoals een nieuw apparaat zonder Sync)
const SEED = [['AMD.DE', 180, 20], ['MTE.DE', 300, 10], ['NVD.DE', 120, 40], ['MSF.DE', 400, 8], ['QDVE.DE', 30, 100, "ETF's"], ['LLY.DE', null, 0, 'Watchlist']];
function seed(list) {
  if (sessionStorage.getItem('seeded')) return;
  sessionStorage.setItem('seeded', '1');
  localStorage.setItem('portfolio_positions', JSON.stringify(list.map(([t, c, n, sec]) => ({ ticker: t, section: sec || 'Stocks', thresh: 20, cur: 'native', watch: sec === 'Watchlist' }))));
  localStorage.setItem('portfolio_version', '999');
  const st = {};
  list.forEach(([t, c, n, sec]) => { if (c) st[(sec || 'Stocks') + '|' + t] = { cost: c, qty: n, sells: [] }; });
  st['Stocks|MTE.DE'].buys = [{ level: 1, date: '20-10-2026', price: 780, top: 1e9 }];
  localStorage.setItem('portfolio_state', JSON.stringify(st));
}
// Wat via het dashboard in tickers.txt staat, hoort op elk apparaat in de lijst (ook zonder of met een oude lokale lijst)
const viaDashboard = () => fs.readFileSync(path.join(ROOT, 'tickers.txt'), 'utf8').split('\n')
  .filter(l => /#\s*via dashboard\s*$/.test(l)).map(l => l.split(',')[0].trim().toUpperCase()).filter(Boolean);
const marktSoon = () => {
  const d0 = new Date(), base = Date.UTC(d0.getFullYear(), d0.getMonth(), d0.getDate());
  return (JSON.parse(fs.readFileSync(path.join(ROOT, 'marktagenda.json'), 'utf8')).events || [])
    .some(e => { const d = Math.round((Date.parse(e.date + 'T00:00:00Z') - base) / 864e5); return d >= 0 && d <= 14; });
};

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
  const RUNS = WIDTHS.flatMap(w => PAGES.map(pg => ({ w, pg, seeded: false })))
    .concat(WIDTHS.flatMap(w => ['portfolio.html', 'vandaag.html'].map(pg => ({ w, pg, seeded: true }))));
  for (const { w, pg, seeded } of RUNS) {
    {
      const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, hasTouch: w < 1000, isMobile: w < 1000 });
      if (seeded) await ctx.addInitScript(seed, SEED);
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
      const info = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        const alpha = c => { const m = c.match(/rgba\([^)]*,\s*([\d.]+)\)/); return m ? parseFloat(m[1]) : 1; };
        return {
          shown: root.display !== 'none',
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          text: (document.body.innerText || '').trim().length,
          dark: /dark/.test(root.colorScheme) && root.scrollbarColor !== 'auto',
          seeThrough: [...document.querySelectorAll('select')].filter(s => alpha(getComputedStyle(s).backgroundColor) < 1).map(s => s.id || 'select')
        };
      });
      if (!info.shown) errs.push('pagina blijft verborgen (antiframe)');
      if (info.overflow > 1) errs.push(`zijwaarts scrollen: ${info.overflow}px te breed`);
      if (info.text < 40) errs.push('pagina lijkt leeg');
      // schuifbalk en uitklaplijsten donker (anders een lichte balk naast de pagina); index scrolt zelf niet
      if (pg !== 'index.html' && !info.dark) errs.push('schuifbalk en uitklaplijsten niet donker (color-scheme / scrollbar-color)');
      // een doorschijnende keuzelijst geeft een witte uitklaplijst met lichte, onleesbare tekst
      if (info.seeThrough.length) errs.push('keuzelijst met doorschijnende achtergrond: ' + info.seeThrough.join(', '));
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
        if (pg === 'portfolio.html') {                     // posities van een ander apparaat (tickers.txt) staan er ook
          const cards = await page.$$eval('.position .p-company', els => els.map(e => e.textContent));
          const missing = viaDashboard().filter(t => !cards.some(c => c.includes(`(${t})`)));
          if (missing.length) errs.push('Investeringen: via het dashboard toegevoegd maar niet in de lijst: ' + missing.join(', '));
        }
        if (pg === 'portfolio.html') {                     // elk koopsignaal is een trede van de kooptrap, met de treden erbij
          const loose = await page.$$eval('.position.alert', els => els.filter(e => !/KOOP TREDE [123]\/3/.test(e.textContent) || !e.querySelector('.ladder')).length);
          if (loose) errs.push(`Investeringen: ${loose} koopsignaal zonder kooptrap`);
        }
        if (seeded && pg === 'portfolio.html') {           // risico: eerste kaart van de portefeuillecheck; een gekochte trede staat als gekocht
          await page.waitForSelector('#pcGrid .pc', { state: 'attached', timeout: 8000 });   // portefeuillecheck staat dicht
          if (!/Risico/.test(await page.textContent('#pcGrid .pc'))) errs.push('Investeringen: risicokaart ontbreekt in de portefeuillecheck');
          if (!(await page.$('.ladder .st.done'))) errs.push('Investeringen: vastgelegde trede niet als gekocht getoond');
        }
        if (seeded && pg === 'vandaag.html') {             // slechte maand in de kop, kooptrap bij Kopen
          if (!/slechte maand/.test(await page.textContent('#hero'))) errs.push('Vandaag: slechte maand ontbreekt in de kop');
          if (await page.$('.g-kopen') && !(await page.$('.g-kopen .steps.buy'))) errs.push('Vandaag: Kopen zonder kooptrap');
        }
        if (!seeded && pg === 'portfolio.html') {          // positie toevoegen: zoeken op naam vult de ticker in
          await page.click('#add');
          await page.fill('#f-search', 'nvidia');
          await page.waitForSelector('#f-results .z-l', { timeout: 8000 });
          await page.click('#f-results .z-l');
          if (!(await page.inputValue('#f-ticker'))) errs.push('zoeken: tikken op een notering vult de ticker niet in');
          await page.click('#f-cancel');
        }
        if (pg === 'vandaag.html') {                       // geen weekverwachting meer; marktagenda als er de komende 2 weken iets is
          if (/verwacht/.test(await page.textContent('#clim'))) errs.push('Vandaag: beursklimaat toont weer een verwachting');
          if (marktSoon() && !/Markt komende 2 weken/.test(await page.textContent('#todo'))) errs.push('Vandaag: marktagenda ontbreekt');
        }
        if (!seeded && pg === 'vandaag.html') {            // groep openklappen in de lijst; techradar is één regel die openklapt
          if (await page.$('.kansen, #kansen')) errs.push('Kansen van de dag staat weer op Vandaag');
          const more = await page.$('.grp .more');
          if (more) {
            const n0 = await page.$$eval('.grp:first-of-type .row', r => r.length);
            await more.click();
            if (await page.$$eval('.grp:first-of-type .row', r => r.length) <= n0) errs.push('Vandaag: "nog … tonen" klapt de groep niet open');
          }
          const tw = await page.$('#techwarn details > summary');
          if (tw) {
            if (await page.$eval('#techwarn details', d => d.open)) errs.push('Vandaag: techradar staat standaard open');
            await tw.click();
            if (!(await page.$eval('#techwarn details', d => d.open))) errs.push('Vandaag: techradar klapt niet open');
          }
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
      if (errs.length) problems.push(`${w}px ${pg}${seeded ? ' (met posities)' : ''}:\n    ` + [...new Set(errs)].join('\n    '));
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
