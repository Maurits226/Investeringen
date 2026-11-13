// De lijst met posities op elk apparaat, in een echte browser met een nagebootste GitHub (geen netwerk).
// tickers.txt is de gedeelde lijst: wat de beheerder weghaalt verdwijnt daar en dus overal (Investeringen op een ander
// apparaat, voor een bezoeker, en op het scorebord = Beoordeling); wat hij toevoegt komt er overal bij.
//  1. Een positie die niet meer in tickers.txt staat (BlackRock) verdwijnt op een apparaat dat haar nog had.
//  2. De beheerder verwijdert een positie uit de startlijst: de regel gaat uit tickers.txt en ze staat niet meer op het scorebord.
//  3. Een ander apparaat met token, dat de positie nog had: weg na het openen.
//  4. Een apparaat zonder token (versie van de website): ook weg, op Investeringen en op het scorebord.
//  5. Een nieuw apparaat zonder token: het scorebord toont alleen wat in tickers.txt staat.
//  6. Is de versie van de website geen lijst (foutpagina) of zou meer dan de helft verdwijnen, dan blijft alles staan.
//  7. Toevoegen schrijft altijd naar tickers.txt, ook als er nog een koers van die ticker is; het andere apparaat krijgt haar erbij.
//  8. Eerder alleen op dit apparaat verwijderd (van vóór de gedeelde lijst): één keer vragen, ja = overal weg, nee = hier terug.
//  9. Net toegevoegd en nog niet in tickers.txt te zien (schrijven mislukt): de positie blijft staan.
// Lokaal: node tests/test_lijst.js   (PLAYWRIGHT=/pad/naar/playwright om een andere installatie te gebruiken)
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const ROOT = path.resolve(__dirname, '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.txt': 'text/plain', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
const OWNER = 'github_pat_EIGENAAR1234567890abcdefghijklmnopqrstuvwxyz';
const b64 = s => Buffer.from(s, 'utf8').toString('base64');
const REAL = fs.readFileSync(path.join(ROOT, 'tickers.txt'), 'utf8');
// De startlijst uit portfolio.html (zoals een apparaat haar bewaart); BlackRock (UU2.DE) staat daar nog in, maar niet meer
// in tickers.txt
const DEFAULT = [...fs.readFileSync(path.join(ROOT, 'portfolio.html'), 'utf8').matchAll(/\{ ticker: '([^']+)',\s*section: (?:'([^']*)'|"([^"]*)")[^{}]*\}/g)]
  .map(m => ({ ticker: m[1], section: m[2] || m[3], watch: m[2] === 'Watchlist', thresh: 20, cur: 'native' }));
const lines = txt => txt.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#')).map(l => l.split(/[\s,]/)[0].toUpperCase());

// De website: de bestanden uit de repository; tickers.txt is de versie van de website (site.tickers)
const site = { tickers: REAL };
function serve() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
      if (rel === 'tickers.txt') { res.writeHead(200, { 'Content-Type': 'text/plain' }); res.end(site.tickers); return; }
      const file = path.join(ROOT, rel);
      if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}
// GitHub: gh.tickers is de echte tickers.txt; gh.fail = schrijven naar tickers.txt mislukt
async function fakeGitHub(ctx, base, gh) {
  await ctx.route('**/*', async r => {
    const req = r.request(), u = req.url();
    if (u.startsWith(base)) return r.continue();
    if (!u.startsWith('https://api.github.com/')) return r.abort();
    const pathn = u.split('?')[0].replace('https://api.github.com/repos/Maurits226/Investeringen', '');
    const json = (status, body) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (pathn === '') return json(200, { full_name: 'Maurits226/Investeringen', permissions: { admin: true, push: true, pull: true } });
    if (pathn === '/contents/tickers.txt') {
      if (req.method() !== 'PUT') return json(200, { sha: 't' + gh.puts, content: b64(gh.tickers) });
      if (gh.fail) return json(500, {});
      gh.puts++;
      gh.tickers = Buffer.from(JSON.parse(req.postData()).content, 'base64').toString('utf8');
      return json(200, { content: { sha: 't' + gh.puts } });
    }
    if (req.method() === 'PUT') return json(200, { content: { sha: 'x' } });
    return json(404, {});
  });
}

(async () => {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch();
  const problems = [], errs = [];
  const check = (ok, msg) => { if (!ok) problems.push(msg); };
  const gh = { tickers: REAL, puts: 0, fail: false };

  // Een apparaat: eigen opslag. saved = de lijst die er al staat (null = nieuw apparaat), token = beheerder of niet
  async function device({ token, saved }) {
    const ctx = await browser.newContext();
    await fakeGitHub(ctx, base, gh);
    await ctx.addInitScript(([tok, list]) => {
      if (sessionStorage.getItem('s')) return;
      sessionStorage.setItem('s', '1');
      if (tok) localStorage.setItem('gh_token', tok);
      if (list) { localStorage.setItem('portfolio_positions', JSON.stringify(list)); localStorage.setItem('portfolio_version', '999'); }
    }, [token || null, saved || null]);
    return ctx;
  }
  async function open(ctx, page, dialogs) {
    const pg = await ctx.newPage();
    pg.on('pageerror', e => errs.push(`${page}: ${e.message}`));
    pg.on('dialog', async d => { const a = dialogs ? dialogs(d.message()) : true; await (a ? d.accept() : d.dismiss()); });
    await pg.goto(base + page);
    await pg.waitForTimeout(2200);
    return pg;
  }
  const local = pg => pg.evaluate(() => (JSON.parse(localStorage.getItem('portfolio_positions') || '[]')).map(p => p.ticker.toUpperCase()));
  const scored = pg => pg.evaluate(() => [...document.querySelectorAll('.b-card')].map(e => e.id.replace(/^c-Stocks\|/, '')));
  const full = () => DEFAULT.map(p => ({ ...p }));
  const DASH = REAL.split('\n').filter(l => /#\s*via dashboard\s*$/.test(l)).length;      // via het dashboard toegevoegd
  const hasDefault = ['MSF.DE', 'TL0.DE', 'UU2.DE'].every(t => DEFAULT.some(p => p.ticker === t)) && DEFAULT.length >= 20;
  check(hasDefault, `startlijst niet te lezen uit portfolio.html (${DEFAULT.length} posities)`);
  check(!lines(REAL).includes('UU2.DE') && lines(REAL).includes('MSF.DE'), 'tickers.txt: BlackRock (UU2.DE) hoort er niet meer in, Microsoft (MSF.DE) wel');

  // 1 + 2. Beheerder op een apparaat dat BlackRock nog had: weg na het openen. Daarna Microsoft verwijderen.
  const A = await device({ token: OWNER, saved: full() });
  {
    const pf = await open(A, 'portfolio.html');
    let l = await local(pf);
    check(!l.includes('UU2.DE') && l.includes('MSF.DE') && l.length === DEFAULT.length - 1 + DASH, `1. BlackRock staat niet meer in tickers.txt en moet hier verdwijnen (${l.length} posities, UU2.DE: ${l.includes('UU2.DE')})`);
    check(gh.puts === 0, '1. bij alleen gelijklopen mag er niets naar GitHub geschreven worden');
    await pf.click('.remove[data-id="Stocks|MSF.DE"]');
    await pf.waitForTimeout(1500);
    l = await local(pf);
    check(!l.includes('MSF.DE'), '2. verwijderde positie staat nog in de lijst van dit apparaat');
    check(!lines(gh.tickers).includes('MSF.DE') && lines(gh.tickers).includes('AMD.DE') && gh.tickers.includes('# via dashboard'),
      `2. Microsoft moet uit tickers.txt zijn, de rest niet (${lines(gh.tickers).length} regels)`);
    check(gh.tickers.startsWith('#'), '2. het commentaar bovenin tickers.txt moet blijven staan');
    const sc = await open(A, 'beoordeling.html');
    const rows = await scored(sc);
    check(!rows.includes('MSF.DE') && !rows.includes('UU2.DE') && rows.includes('AMD.DE'), `2. scorebord: Microsoft en BlackRock weg, AMD er nog (${rows.length} rijen)`);
    await sc.close(); await pf.close();
  }
  site.tickers = gh.tickers;                        // de website is bijgewerkt

  // 3. Ander apparaat met token, dat beide nog had
  const B = await device({ token: OWNER, saved: full() });
  {
    const pf = await open(B, 'portfolio.html');
    const l = await local(pf);
    check(!l.includes('MSF.DE') && !l.includes('UU2.DE') && l.includes('AMD.DE'), `3. ander apparaat met token: Microsoft en BlackRock moeten weg zijn (${l.includes('MSF.DE')}, ${l.includes('UU2.DE')})`);
    await pf.close();
  }

  // 4. Apparaat zonder token met een bewaarde lijst: gaat af op de versie van de website
  {
    const C = await device({ saved: full() });
    const pf = await open(C, 'portfolio.html');
    const l = await local(pf);
    check(!l.includes('MSF.DE') && !l.includes('UU2.DE') && l.includes('AMD.DE'), `4. zonder token: Microsoft en BlackRock moeten weg zijn (${l.includes('MSF.DE')}, ${l.includes('UU2.DE')})`);
    const sc = await open(C, 'beoordeling.html');
    const rows = await scored(sc);
    check(!rows.includes('MSF.DE') && !rows.includes('UU2.DE') && rows.includes('AMD.DE'), '4. zonder token: scorebord toont nog een verwijderde positie');
    await C.close();
  }

  // 5. Nieuw apparaat zonder token: het scorebord (en Vandaag) volgen tickers.txt
  {
    const D = await device({});
    const sc = await open(D, 'beoordeling.html');
    const rows = await scored(sc);
    check(rows.includes('AMD.DE') && !rows.includes('MSF.DE') && !rows.includes('UU2.DE') && rows.length >= 15, `5. nieuw apparaat: scorebord klopt niet met tickers.txt (${rows.length} rijen)`);
    await open(D, 'vandaag.html');
    await D.close();
  }

  // 6. De versie van de website is geen lijst, of bijna leeg: niets weghalen
  for (const [naam, txt] of [['foutpagina', '<!DOCTYPE html><html><body>Service Unavailable</body></html>'], ['bijna leeg', '# kop\nAMD.DE\n'], ['leeg', '']]) {
    site.tickers = txt;
    const E = await device({ saved: full() });
    const pf = await open(E, 'portfolio.html');
    const l = await local(pf);
    check(l.length === DEFAULT.length && l.includes('UU2.DE'), `6. ${naam}: de lijst moet blijven staan (${l.length} van ${DEFAULT.length})`);
    await E.close();
  }
  site.tickers = gh.tickers;

  // 7. Toevoegen: Microsoft terug (er is nog een koers van) en een nieuwe ticker; beide komen in tickers.txt
  {
    const pf = await open(A, 'portfolio.html');
    for (const t of ['MSF.DE', 'ORCL']) {
      await pf.click('#add');
      await pf.fill('#f-ticker', t);
      await pf.selectOption('#f-section', 'Stocks');
      await pf.fill('#f-cost', '100');
      await pf.click('#f-save');
      await pf.waitForTimeout(1300);
    }
    const l = await local(pf);
    check(l.includes('MSF.DE') && l.includes('ORCL'), '7. toegevoegde posities staan niet in de lijst');
    check(/^MSF\.DE, .*Stocks\s+# via dashboard$/m.test(gh.tickers) && /^ORCL, .*Stocks\s+# via dashboard$/m.test(gh.tickers),
      `7. toegevoegde posities moeten in tickers.txt staan, ook als er al een koers was:\n${gh.tickers.split('\n').slice(-4).join('\n')}`);
    await pf.reload(); await pf.waitForTimeout(2200);
    check((await local(pf)).includes('MSF.DE'), '7. na herladen is de toegevoegde positie weer weg');
    await pf.close();
    const pb = await open(B, 'portfolio.html');
    const lb = await local(pb);
    check(lb.includes('MSF.DE') && lb.includes('ORCL'), `7. het andere apparaat krijgt de toegevoegde posities niet (${lb.includes('MSF.DE')}, ${lb.includes('ORCL')})`);
    const sc = await open(B, 'beoordeling.html');
    check((await scored(sc)).includes('MSF.DE'), '7. toegevoegde positie staat niet op het scorebord van het andere apparaat');
    await pb.close(); await sc.close();
  }

  // 8. Eerder alleen hier verwijderd (Tesla staat nog in tickers.txt): één keer vragen
  for (const ja of [false, true]) {
    const saved = full().filter(p => p.ticker !== 'TL0.DE' && p.ticker !== 'UU2.DE');
    const F = await device({ token: OWNER, saved });
    const asked = [];
    const before = gh.tickers;
    const pf = await open(F, 'portfolio.html', msg => { asked.push(msg); return ja; });
    await pf.waitForTimeout(1200);
    const l = await local(pf);
    check(asked.length === 1 && /Tesla|TL0\.DE/.test(asked[0]) && !/Microsoft|MSF\.DE/.test(asked[0]), `8. één vraag over Tesla verwacht, kreeg ${asked.length}: ${String(asked[0]).slice(0, 120)}`);
    if (ja) check(!lines(gh.tickers).includes('TL0.DE') && !l.includes('TL0.DE'), '8. ja: Tesla moet uit tickers.txt en hier weg blijven');
    else check(gh.tickers === before && l.includes('TL0.DE'), '8. nee: tickers.txt ongewijzigd en Tesla hier terug');
    await F.close();
  }

  // 9. Net toegevoegd, schrijven naar tickers.txt mislukt: blijft staan (ook na herladen)
  {
    gh.fail = true;
    const G = await device({ token: OWNER, saved: full().filter(p => lines(gh.tickers).includes(p.ticker)) });
    const pf = await open(G, 'portfolio.html');
    await pf.click('#add');
    await pf.fill('#f-ticker', 'IBM');
    await pf.selectOption('#f-section', 'Stocks');
    await pf.click('#f-save');
    await pf.waitForTimeout(1300);
    await pf.reload(); await pf.waitForTimeout(2200);
    check((await local(pf)).includes('IBM') && !lines(gh.tickers).includes('IBM'), '9. een positie die nog niet weggeschreven kon worden moet blijven staan');
    gh.fail = false;
    await G.close();
  }

  await browser.close();
  srv.close();
  errs.forEach(e => problems.push('JS-fout: ' + e));
  problems.forEach(p => console.log('  FOUT', p));
  console.log(problems.length ? `LIJST: ${problems.length} probleem/problemen`
    : 'LIJST: weghalen en toevoegen werkt door op elk apparaat, voor bezoekers en op het scorebord');
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
