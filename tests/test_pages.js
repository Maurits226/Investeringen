// Laadt elke pagina van het dashboard in een echte browser (telefoon en desktop) en faalt bij:
// JavaScript-fouten, schendingen van het beveiligingsbeleid (CSP), zijwaarts scrollen, of pagina's die leeg blijven.
// Als bezoeker (zonder token) mag er nergens een knop zijn om iets te wijzigen en geen verkeer naar de GitHub-API;
// als beheerder (met token) moeten die knoppen er juist wel zijn.
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

// Bezoeker of beheerder. Zonder token is het dashboard alleen-lezen (kijkmodus): geen enkele knop om iets te wijzigen en
// geen verkeer naar de GitHub-API. Met de token (hier een neptoken; GitHub zelf is in de test onbereikbaar) ben je beheerder.
const TOKEN = 'github_pat_TEST1234567890abcdefghijklmnopqrstuvwxyz';
function login(tok) { try { localStorage.setItem('gh_token', tok); } catch (e) {} }
// Alles waarmee je iets kunt wijzigen: mag er voor een bezoeker niet zijn
const EDIT_CONTROLS = '#add:not([hidden]), .remove, .edit-cost, .realise, .buy-step, .undo-buy, .sell-price, .undo-sell, '
  + '[data-follow], [data-close], [data-del], .notes textarea, [data-qcheck], input[type=checkbox][data-step], .rm, #q:not([hidden])';

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

// Nieuwe onderdelen die van data afhangen: het bodemsignaal (bodem.json) en de stresstest (stress per positie in
// signals.json, komt met de eerstvolgende run van de Patroonradar)
const readJSON = f => { try { return JSON.parse(fs.readFileSync(path.join(ROOT, f), 'utf8')); } catch (e) { return null; } };
const hasBodem = !!(readJSON('bodem.json') || {}).dip;
const hasStress = SEED.some(([t]) => ((readJSON('signals.json') || {}).items || []).some(i => i.symbol === t && i.stress && typeof i.stress.ndx === 'number'));

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
  // bezoeker: alle pagina's; beheerder: toevoegen en opzoeken, en Investeringen en Vandaag met echte posities
  const RUNS = WIDTHS.flatMap(w => PAGES.map(pg => ({ w, pg, seeded: false, admin: false })))
    .concat(WIDTHS.flatMap(w => ['portfolio.html', 'opzoeken.html', 'beoordeling.html', 'signals.html'].map(pg => ({ w, pg, seeded: false, admin: true }))))
    .concat(WIDTHS.flatMap(w => ['portfolio.html', 'vandaag.html'].map(pg => ({ w, pg, seeded: true, admin: true }))));
  for (const { w, pg, seeded, admin } of RUNS) {
    {
      const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, hasTouch: w < 1000, isMobile: w < 1000 });
      if (admin) await ctx.addInitScript(login, TOKEN);
      if (seeded) await ctx.addInitScript(seed, SEED);
      const page = await ctx.newPage();
      const errs = [];
      const api = [];                                    // verkeer naar de GitHub-API (een bezoeker hoort er geen te hebben)
      page.on('request', r => { if (/^https:\/\/api\.github\.com\//.test(r.url())) api.push(r.method() + ' ' + r.url().split('?')[0].split('/').slice(-1)[0]); });
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
        if (pg === 'screener.html' && await page.$('.stock')) {   // standaardvolgorde: groepen met een kopje, zeer duur onderaan
          const order = await page.evaluate(() => [...document.querySelectorAll('#cards > .grp-h, #cards > .stock')].slice(0, 40)
            .map(e => e.classList.contains('grp-h') ? 'H:' + [...e.classList].find(c => c.startsWith('g-')) : /zeer duur/.test(e.querySelector('.tags') ? e.querySelector('.tags').textContent : '') ? 'duur' : 'kaart'));
          if (!order[0] || !order[0].startsWith('H:')) errs.push('Kansenlijst: geen kopje boven de eerste groep in de standaardvolgorde');
          const d = order.indexOf('duur'), h = order.indexOf('H:g-duur');
          if (d >= 0 && (h < 0 || d < h)) errs.push('Kansenlijst: een zeer duur aandeel staat niet in de groep onderaan');
          await page.selectOption('#sortSel', 'score');
          await page.waitForTimeout(300);
          if (await page.$('#cards > .grp-h')) errs.push('Kansenlijst: groepskopjes horen alleen bij de standaardvolgorde');
          await page.selectOption('#sortSel', 'kans');
          await page.waitForTimeout(300);
        }
        if (pg === 'signals.html' && hasBodem && !(await page.$('.bd .bd-verdict'))) errs.push('Patroonradar: bodemsignaal ontbreekt');
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
          if (hasStress && !(await page.$('#pcGrid [data-stress]'))) errs.push('Investeringen: stresstest ontbreekt in de portefeuillecheck');
          if (!(await page.$('.ladder .st.done'))) errs.push('Investeringen: vastgelegde trede niet als gekocht getoond');
        }
        if (seeded && pg === 'vandaag.html') {             // slechte maand in de kop, kooptrap bij Kopen
          if (!/slechte maand/.test(await page.textContent('#hero'))) errs.push('Vandaag: slechte maand ontbreekt in de kop');
          if (hasStress && !(await page.$('#hero [data-stress]'))) errs.push('Vandaag: stresstest ontbreekt in de kop');
          if (await page.$('.g-kopen') && !(await page.$('.g-kopen .steps.buy'))) errs.push('Vandaag: Kopen zonder kooptrap');
        }
        if (admin && !seeded && pg === 'portfolio.html') { // positie toevoegen: zoeken op naam vult de ticker in
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
        if (pg === 'opzoeken.html' && admin) {             // beheerder: zoeken op naam en toevoegen (GitHub is hier onbereikbaar: nette melding)
          await page.fill('#q', 'nvidia');
          await page.waitForSelector('#results .z-co', { timeout: 8000 });
          await page.click('#results .z-co');
          await page.waitForFunction(() => /kon niet worden toegevoegd/.test(document.getElementById('note').textContent), null, { timeout: 8000 })
            .catch(() => errs.push('opzoeken als beheerder: toevoegen bereikt GitHub niet (geen melding)'));
          if (!(await page.$('.b-card .rm'))) errs.push('opzoeken als beheerder: knop verwijderen ontbreekt');
        }
        if (pg === 'opzoeken.html' && !admin) {            // bezoeker: geen zoekveld, wel uitleg
          if (!(await page.$eval('#q', e => e.hidden))) errs.push('opzoeken als bezoeker: zoekveld staat er nog');
          if (!/Alleen-lezen/.test(await page.textContent('#note'))) errs.push('opzoeken als bezoeker: geen uitleg');
        }
        if (pg === 'opzoeken.html') {                      // kaart openklappen: de 12 stappen
          await page.click('.b-card .b-head .toggle');
          await page.waitForSelector('.b-card .b-body .s12-step', { timeout: 8000 });
        }
        if (pg === 'index.html') {
          for (const v of ['overzicht', 'score', 'radar', 'archief', 'koop']) await page.click(`.seg.view button[data-view="${v}"]`);
          await page.click('.seg.world button[data-world="zoek"]');
          if (!(await page.$('iframe#zoek.active'))) errs.push('tab Opzoeken opent niet');
        }
        // Kijkmodus (bezoeker): nergens een knop om iets te wijzigen, en geen verkeer naar de GitHub-API
        const edits = await page.$$eval(EDIT_CONTROLS, els => els.map(e => e.id ? '#' + e.id : '.' + String(e.className).split(' ')[0] || e.tagName));
        const hasShared = await page.evaluate(() => typeof Shared !== 'undefined');
        if (!admin) {
          if (edits.length) errs.push('kijkmodus: bewerkknoppen aanwezig: ' + [...new Set(edits)].join(', '));
          if (api.length) errs.push('kijkmodus: verkeer naar de GitHub-API: ' + [...new Set(api)].join(', '));
          if (hasShared && !(await page.evaluate(() => document.documentElement.classList.contains('kijk') && !Shared.isAdmin()))) errs.push('kijkmodus: pagina staat niet in de kijkmodus');
        } else {
          if (hasShared && await page.evaluate(() => document.documentElement.classList.contains('kijk') || !Shared.isAdmin())) errs.push('beheerder: pagina staat in de kijkmodus');
          if (pg === 'portfolio.html' && !['#add', '.remove'].every(c => edits.includes(c))) errs.push('beheerder: + Positie of verwijder ontbreekt (' + [...new Set(edits)].join(', ') + ')');
          if (pg === 'portfolio.html' && seeded && !edits.includes('.edit-cost')) errs.push('beheerder: Aankoop aanpassen ontbreekt');
          if (pg === 'beoordeling.html' && await page.$('.b-body') && !edits.includes('.notes')) {
            if (!(await page.$('.notes textarea'))) errs.push('beheerder: notities ontbreken op Beoordeling');
          }
        }
      } catch (e) { errs.push('interactie: ' + e.message.split('\n')[0]); }
      await page.waitForTimeout(300);
      if (errs.length) problems.push(`${w}px ${pg}${admin ? ' (beheerder' + (seeded ? ', met posities' : '') + ')' : ''}:\n    ` + [...new Set(errs)].join('\n    '));
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
