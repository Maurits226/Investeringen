// Beveiliging in een echte browser, met een nagebootste GitHub (geen netwerk). Wat hier getest wordt:
//  1. Bezoeker (geen token): alles alleen-lezen, ook via de code is er niets op te slaan, en de browser praat niet met GitHub.
//  2. Token van iemand anders (geldig bij GitHub, geen schrijfrecht): geweigerd bij het invoeren.
//  3. Die token toch in de browser gezet: je gegevens gaan er niet mee open, dus alleen-lezen, en er wordt niets geschreven.
//  4. Beheerder (de token die je gegevens opent): bewerken en opslaan werkt; de token gaat alleen naar api.github.com.
//  5. Ingevoegde code draait niet: inline script, onerror-attribuut, javascript:-link en eval worden geblokkeerd.
//  6. Token vervangen op een apparaat zonder gegevens wist niets op GitHub.
//  7. Elders intussen met een andere token versleuteld: opslaan weigert (overschrijft niets).
//  8. Token vervangen op een apparaat mét gegevens: na bevestiging opnieuw versleuteld met de nieuwe token.
// Lokaal: node tests/test_security.js   (PLAYWRIGHT=/pad/naar/playwright om een andere installatie te gebruiken)
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const ROOT = path.resolve(__dirname, '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.txt': 'text/plain', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
const OWNER = 'github_pat_EIGENAAR1234567890abcdefghijklmnopqrstuvwxyz';      // de token van de beheerder
const OWNER2 = 'github_pat_EIGENAAR2nieuw567890abcdefghijklmnopqrstuvwxyz';   // zijn nieuwe token (na vervangen)
const STRANGER = 'github_pat_VREEMDE01234567890abcdefghijklmnopqrstuvwxyz';   // geldige token van iemand anders
const ID = 'Stocks|AMD.DE';
const b64 = s => Buffer.from(s, 'utf8').toString('base64');
const EDIT = '#add:not([hidden]), .remove, .edit-cost, .realise, .buy-step, .undo-buy, .sell-price, .undo-sell, .notes textarea, [data-follow], .rm';

function serve() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
      const file = path.join(ROOT, rel);
      if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

// GitHub nabootsen. writers = tokens met schrijfrecht; elke andere bekende token mag alleen lezen (openbare repository),
// een onbekende token geeft 401. gh.state = de inhoud van state.json (tekst); gh.log = alle verzoeken.
async function fakeGitHub(ctx, base, gh) {
  gh.log = []; gh.leaks = []; gh.puts = 0;
  await ctx.route('**/*', async r => {
    const req = r.request(), u = req.url(), auth = req.headers().authorization || '';
    const tok = auth.replace(/^Bearer /, '');
    if (auth && !u.startsWith('https://api.github.com/')) gh.leaks.push(u);          // de token mag nergens anders heen
    if (u.startsWith(base)) return r.continue();
    if (!u.startsWith('https://api.github.com/')) return r.abort();
    const pathn = u.split('?')[0].replace('https://api.github.com/repos/Maurits226/Investeringen', '');
    gh.log.push(`${req.method()} ${pathn || '/'}`);
    const json = (status, body) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (tok && !gh.known.includes(tok)) return json(401, { message: 'Bad credentials' });
    if (pathn === '') return json(200, { full_name: 'Maurits226/Investeringen', permissions: { admin: gh.writers.includes(tok), push: gh.writers.includes(tok), pull: true } });
    if (pathn === '/contents/state.json') {
      if (req.method() === 'PUT') {
        if (!gh.writers.includes(tok)) return json(403, { message: 'Resource not accessible by personal access token' });
        gh.puts++;
        gh.state = Buffer.from(JSON.parse(req.postData()).content, 'base64').toString('utf8');
        return json(200, { content: { sha: 'sha' + gh.puts } });
      }
      return gh.state == null ? json(404, {}) : json(200, { sha: 'sha' + gh.puts, content: b64(gh.state) });
    }
    if (pathn === '/contents/tickers.txt' && req.method() === 'GET')
      return json(200, { sha: 't', content: b64(fs.readFileSync(path.join(ROOT, 'tickers.txt'), 'utf8')) });
    if (req.method() === 'PUT') { if (!gh.writers.includes(tok)) return json(403, {}); gh.puts++; return json(200, { content: { sha: 'x' } }); }
    return json(404, {});
  });
}

(async () => {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch();
  const problems = [];
  const check = (ok, msg) => { if (!ok) problems.push(msg); };

  // versleutelen en openen met de echte code van het dashboard (vault.js)
  const tool = await (await browser.newContext()).newPage();
  await tool.goto(base + 'archief.html');
  const lock = (obj, tok) => tool.evaluate(([o, t]) => Vault.lock(o, t).then(x => JSON.stringify(x)), [obj, tok]);
  const open = (txt, tok) => tool.evaluate(([x, t]) => Vault.unlock(JSON.parse(x), t).catch(() => null), [txt, tok]);
  const DATA = { [ID]: { cost: 180, qty: 2, sells: [], t: 1000 } };

  // Eén scenario: eigen browsercontext, eigen GitHub. local = wat er al op het apparaat staat.
  async function scenario({ token, local, state, known = [OWNER, OWNER2, STRANGER], writers = [OWNER, OWNER2] }) {
    const ctx = await browser.newContext();
    const gh = { state, known, writers };
    await fakeGitHub(ctx, base, gh);
    await ctx.addInitScript(([tok, st]) => {
      if (sessionStorage.getItem('s')) return;
      sessionStorage.setItem('s', '1');
      if (tok) localStorage.setItem('gh_token', tok);
      localStorage.setItem('portfolio_positions', JSON.stringify([{ ticker: 'AMD.DE', section: 'Stocks', thresh: 20, cur: 'native' }]));
      localStorage.setItem('portfolio_version', '999');
      if (st) localStorage.setItem('portfolio_state', JSON.stringify(st));
    }, [token || '', local || null]);
    const page = await ctx.newPage();
    const answers = [], alerts = [];
    page.on('pageerror', e => problems.push('JS-fout: ' + e.message));
    page.on('dialog', d => {
      if (d.type() === 'alert') { alerts.push(d.message()); return d.accept(); }
      const a = answers.shift();
      return a === false || a === undefined ? d.dismiss() : d.accept(a === true ? undefined : a);
    });
    const go = async pg => { await page.goto(base + pg); await page.waitForTimeout(2200); };
    const view = () => page.evaluate(sel => ({ kijk: document.documentElement.classList.contains('kijk'), admin: Shared.isAdmin(),
      edits: [...document.querySelectorAll(sel)].map(e => e.id ? '#' + e.id : '.' + String(e.className).split(' ')[0]),
      token: localStorage.getItem('gh_token'), state: JSON.parse(localStorage.getItem('portfolio_state') || 'null'),
      badge: (document.getElementById('syncBtn') || {}).textContent || '' }), EDIT);
    return { ctx, gh, page, answers, alerts, go, view };
  }

  // 1. Bezoeker
  {
    const s = await scenario({ token: null, state: await lock(DATA, OWNER) });
    for (const pg of ['portfolio.html', 'beoordeling.html', 'signals.html', 'opzoeken.html', 'vandaag.html']) {
      await s.go(pg);
      if (pg === 'beoordeling.html' && await s.page.$('.b-card .b-head')) { await s.page.click('.b-card .b-head'); await s.page.waitForTimeout(400); }
      const v = await s.view();
      check(v.kijk && !v.admin, `bezoeker ${pg}: niet in de kijkmodus`);
      check(!v.edits.length, `bezoeker ${pg}: bewerkknoppen aanwezig (${[...new Set(v.edits)].join(', ')})`);
    }
    await s.go('portfolio.html');
    const tried = await s.page.evaluate(async id => {
      const before = localStorage.getItem('portfolio_state');
      const out = { save: await Shared.saveState(st => { st[id] = { cost: 1 }; }), add: await Shared.addTicker('NVDA', 'x', 'Stocks'),
        rm: await Shared.removeTicker('SNDK'), look: await Shared.lookAdd('NVDA', [], 'x'), write: await Shared.ghWrite('state.json', {}, null, 'x') };
      const n0 = positions.length;
      removePosition(id); saveState();                                   // de functies van de pagina zelf, rechtstreeks aangeroepen
      return { ...out, same: localStorage.getItem('portfolio_state') === before, kept: positions.length === n0 };
    }, ID);
    check(tried.save === 'readonly' && tried.add === 'no-token' && tried.rm === 'no-token' && tried.look === 'no-token' && tried.write === false,
      `bezoeker: opslaan via de code niet geweigerd (${JSON.stringify(tried)})`);
    check(tried.same && tried.kept, 'bezoeker: de code kon toch iets wijzigen op het apparaat');
    check(!s.gh.log.length, `bezoeker: verkeer naar de GitHub-API (${[...new Set(s.gh.log)].join(', ')})`);
    check(/Beheerder/.test((await s.view()).badge), 'bezoeker: knop Beheerder ontbreekt');
    await s.ctx.close();
  }

  // 2. Vreemde token invoeren: geweigerd
  {
    const s = await scenario({ token: null, state: await lock(DATA, OWNER) });
    await s.go('portfolio.html');
    s.answers.push(STRANGER);
    await s.page.click('#syncBtn');
    await s.page.waitForTimeout(1200);
    const v = await s.view();
    check(!v.token && v.kijk && !v.edits.length, `vreemde token: niet geweigerd bij invoeren (token bewaard: ${!!v.token}, knoppen: ${v.edits.length})`);
    check(s.alerts.some(a => /hoort niet bij de beheerder/.test(a)), `vreemde token: geen duidelijke melding (${s.alerts.join(' | ').slice(0, 120)})`);
    check(!s.gh.puts, 'vreemde token: er is iets naar GitHub geschreven');
    await s.ctx.close();
  }

  // 3. Vreemde token toch in de browser gezet: gegevens gaan niet open → alleen-lezen, niets geschreven
  {
    const before = await lock(DATA, OWNER);
    const s = await scenario({ token: STRANGER, state: before });
    await s.go('portfolio.html');
    let v = await s.view();
    check(v.kijk && !v.admin && !v.edits.length, `vreemde token in de browser: niet alleen-lezen (knoppen: ${[...new Set(v.edits)].join(', ')})`);
    check(/Token nodig/.test(v.badge), `vreemde token in de browser: geen melding op de knop (${v.badge})`);
    const res = await s.page.evaluate(async id => ({ save: await Shared.saveState(st => { st[id] = { cost: 1 }; }), force: await saveStateToGitHub(true) }), ID);
    check(res.save === 'readonly' && res.force === false, `vreemde token: opslaan niet geweigerd (${JSON.stringify(res)})`);
    check(!s.gh.puts && s.gh.state === before, 'vreemde token: de gegevens op GitHub zijn gewijzigd');
    await s.go('beoordeling.html');
    v = await s.view();
    check(v.kijk && !v.edits.length, 'vreemde token: Beoordeling niet alleen-lezen');
    await s.ctx.close();
  }

  // 4. Beheerder: gegevens open, bewerken en opslaan werkt; token alleen naar GitHub
  {
    const s = await scenario({ token: OWNER, state: await lock(DATA, OWNER) });
    await s.go('portfolio.html');
    const v = await s.view();
    check(!v.kijk && v.admin && ['#add', '.remove', '.edit-cost'].every(c => v.edits.includes(c)), `beheerder: knoppen ontbreken (${[...new Set(v.edits)].join(', ')})`);
    check(v.state && v.state[ID] && v.state[ID].cost === 180, 'beheerder: gegevens van GitHub niet geopend');
    s.answers.push('250', '30');
    await s.page.click('.edit-cost');
    await s.page.waitForTimeout(1500);
    const stored = await open(s.gh.state, OWNER);
    check(s.gh.puts >= 1 && stored && stored[ID].cost === 250 && stored[ID].qty === 30, `beheerder: wijziging niet versleuteld op GitHub (${JSON.stringify(stored && stored[ID])})`);
    check(await open(s.gh.state, STRANGER) === null, 'beheerder: gegevens op GitHub zijn met een andere token te openen');
    check(!/250|"cost"/.test(s.gh.state), 'beheerder: gegevens staan leesbaar (onversleuteld) op GitHub');
    check(!s.gh.leaks.length, `de token is naar een ander adres gestuurd: ${s.gh.leaks.join(', ')}`);
    await s.ctx.close();
  }

  // 5. Ingevoegde code draait niet (beveiligingsbeleid), op elke pagina
  for (const pg of ['index.html', 'vandaag.html', 'portfolio.html', 'beoordeling.html', 'signals.html', 'archief.html', 'screener.html', 'koopmoment.html', 'opzoeken.html']) {
    const s = await scenario({ token: OWNER, state: await lock(DATA, OWNER) });
    await s.go(pg);
    const ran = await s.page.evaluate(async () => {
      const d = document.createElement('div');
      d.innerHTML = '<img src="bestaat-niet.png" onerror="window.__p1=1"><a id="jl" href="javascript:window.__p2=1">x</a><svg onload="window.__p5=1"></svg>';
      document.body.appendChild(d);
      const sc = document.createElement('script'); sc.textContent = 'window.__p3=1'; document.body.appendChild(sc);
      document.getElementById('jl').click();
      // eval en Function vanuit de pagina zelf (een eigen taak): rechtstreeks vanuit de testbrowser gelden de regels niet
      setTimeout(() => { try { eval('window.__p4=1'); } catch (e) {} try { new Function('window.__p6=1')(); } catch (e) {} }, 0);
      await new Promise(r => setTimeout(r, 500));
      return ['__p1', '__p2', '__p3', '__p4', '__p5', '__p6'].filter(k => window[k]);
    });
    check(!ran.length, `${pg}: ingevoegde code is uitgevoerd (${ran.join(', ')}: 1 onerror, 2 javascript-link, 3 inline script, 4 eval, 5 onload, 6 Function)`);
    await s.ctx.close();
  }

  // 6. Nieuwe token op een apparaat zonder gegevens, GitHub nog versleuteld met de vorige: niets wissen
  {
    const before = await lock(DATA, OWNER);
    const s = await scenario({ token: null, state: before });
    await s.go('portfolio.html');
    s.answers.push(OWNER2);
    await s.page.click('#syncBtn');
    await s.page.waitForTimeout(1500);
    const v = await s.view();
    check(!s.gh.puts && s.gh.state === before, 'nieuwe token zonder gegevens: de gegevens op GitHub zijn overschreven');
    check(v.kijk && !v.edits.length && s.alerts.some(a => /opent je gegevens op GitHub niet/.test(a)), `nieuwe token zonder gegevens: geen uitleg of niet alleen-lezen (${s.alerts.join(' | ').slice(0, 100)})`);
    await s.ctx.close();
  }

  // 7. Tijdens het werken elders opnieuw versleuteld met een andere token: opslaan weigert
  {
    const s = await scenario({ token: OWNER, local: DATA, state: await lock(DATA, OWNER) });
    await s.go('portfolio.html');
    const other = await lock({ [ID]: { cost: 999, qty: 9, sells: [], t: Date.now() } }, OWNER2);
    s.gh.state = other;
    s.answers.push('250', '30');
    await s.page.click('.edit-cost');
    await s.page.waitForTimeout(1500);
    const v = await s.view();
    check(!s.gh.puts && s.gh.state === other, 'elders opnieuw versleuteld: toch overschreven met de oude token');
    check(v.kijk && /Token nodig/.test(v.badge), `elders opnieuw versleuteld: geen melding of niet alleen-lezen (${v.badge})`);
    await s.ctx.close();
  }

  // 8. Token vervangen op een apparaat mét gegevens: na bevestiging opnieuw versleuteld met de nieuwe token
  {
    const s = await scenario({ token: OWNER, local: DATA, state: await lock(DATA, OWNER) });
    await s.go('portfolio.html');
    s.answers.push(true, OWNER2, true);                                  // vervangen · nieuwe token · bevestigen
    await s.page.click('#syncBtn');
    await s.page.waitForTimeout(2000);
    const v = await s.view();
    const stored = await open(s.gh.state, OWNER2);
    check(v.token === OWNER2 && !v.kijk && v.admin, `token vervangen: daarna geen beheerder (${v.badge})`);
    check(stored && stored[ID] && stored[ID].cost === 180 && await open(s.gh.state, OWNER) === null, 'token vervangen: gegevens niet opnieuw versleuteld met de nieuwe token');
    // annuleren bij de bevestiging: er verandert niets
    const s2 = await scenario({ token: OWNER, local: DATA, state: await lock(DATA, OWNER) });
    await s2.go('portfolio.html');
    const before = s2.gh.state;
    s2.answers.push(true, OWNER2, false);
    await s2.page.click('#syncBtn');
    await s2.page.waitForTimeout(2000);
    check(s2.gh.state === before, 'token vervangen geannuleerd: toch iets geschreven');
    await s.ctx.close(); await s2.ctx.close();
  }

  await browser.close();
  srv.close();
  [...new Set(problems)].forEach(p => console.log('  FOUT', p));
  console.log(problems.length ? `BEVEILIGING: ${new Set(problems).size} probleem/problemen`
    : 'BEVEILIGING: bezoeker alleen-lezen, vreemde token geweigerd, alleen de beheerder kan opslaan, ingevoegde code draait niet');
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
