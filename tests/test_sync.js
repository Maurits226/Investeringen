// Sync van je portefeuille-state (aankoopprijs, aantal) in een echte browser, met een nagebootste GitHub (geen netwerk):
// 1. Vandaag en Investeringen tegelijk open (zoals in het dashboard). Je past een aankoopprijs aan terwijl GitHub de eerste
//    seconden nog de vorige state.json teruggeeft: de nieuwe prijs moet blijven staan, ook na herladen en op GitHub.
// 2. Op een ander apparaat later gewijzigd: die nieuwere prijs moet hier binnenkomen.
// Lokaal: node tests/test_sync.js   (PLAYWRIGHT=/pad/naar/playwright om een andere installatie te gebruiken)
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const ROOT = path.resolve(__dirname, '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.txt': 'text/plain', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
const TOKEN = 'github_pat_TEST1234567890abcdefghijklmnopqrstuvwxyz';
const ID = 'Stocks|AMD.DE';
const b64 = s => Buffer.from(s, 'utf8').toString('base64');

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

// GitHub nabootsen: state.json (een geschreven versie is pas na `lag` ms te lezen) en tickers.txt
function fakeGitHub(ctx, base, start, lag) {
  const gh = { remote: JSON.stringify(start), puts: 0 };
  return ctx.route('**/*', async r => {
    const u = r.request().url();
    if (u.startsWith(base)) return r.continue();
    if (u.startsWith('https://api.github.com/repos/Maurits226/Investeringen/contents/state.json')) {
      if (r.request().method() === 'PUT') {
        gh.puts++;
        const content = Buffer.from(JSON.parse(r.request().postData()).content, 'base64').toString('utf8');
        setTimeout(() => { gh.remote = content; }, lag);
        return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ content: { sha: 'nieuw' } }) });
      }
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sha: 'oud', content: b64(gh.remote) }) });
    }
    if (u.startsWith('https://api.github.com/repos/Maurits226/Investeringen/contents/tickers.txt'))
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sha: 't', content: b64(fs.readFileSync(path.join(ROOT, 'tickers.txt'), 'utf8')) }) });
    if (u.startsWith('https://api.github.com/user')) return r.fulfill({ status: 200, contentType: 'application/json', body: '{"login":"test"}' });
    return r.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  }).then(() => gh);
}

async function context(browser, local) {
  const ctx = await browser.newContext();
  await ctx.addInitScript(([tok, st]) => {
    if (localStorage.getItem('seeded')) return;
    localStorage.setItem('seeded', '1');
    localStorage.setItem('gh_token', tok);
    localStorage.setItem('portfolio_positions', JSON.stringify([{ ticker: 'AMD.DE', section: 'Stocks', thresh: 20, cur: 'native' }]));
    localStorage.setItem('portfolio_version', '999');
    localStorage.setItem('portfolio_state', JSON.stringify(st));
  }, [TOKEN, local]);
  return ctx;
}
const stateOf = async page => (JSON.parse(await page.evaluate(() => localStorage.getItem('portfolio_state') || '{}'))[ID] || {});

(async () => {
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch();
  const problems = [];
  const errs = [];

  // 1. aanpassen met Vandaag ernaast en een trage GitHub
  {
    const old = { [ID]: { cost: 180, qty: 1, sells: [] } };
    const ctx = await context(browser, old);
    const gh = await fakeGitHub(ctx, base, old, 3000);
    const vandaag = await ctx.newPage(); vandaag.on('pageerror', e => errs.push('Vandaag: ' + e.message));
    await vandaag.goto(base + 'vandaag.html');
    const pf = await ctx.newPage(); pf.on('pageerror', e => errs.push('Investeringen: ' + e.message));
    await pf.goto(base + 'portfolio.html');
    await pf.waitForTimeout(2500);
    const answers = ['250', '30'];
    pf.on('dialog', d => d.accept(answers.shift()));
    await pf.click('.edit-cost');
    for (const [wait, when] of [[1000, 'na 1 s'], [5000, 'na 6 s']]) {
      await pf.waitForTimeout(wait);
      const st = await stateOf(pf);
      if (st.cost !== 250 || st.qty !== 30) problems.push(`aanpassen: ${when} weer ${st.cost} × ${st.qty} in plaats van 250 × 30`);
    }
    await pf.reload(); await pf.waitForTimeout(3500);
    const st = await stateOf(pf);
    if (st.cost !== 250 || st.qty !== 30) problems.push(`aanpassen: na herladen ${st.cost} × ${st.qty} in plaats van 250 × 30`);
    const onGh = await pf.evaluate(id => Shared.ghRead('state.json').then(d => d && d[id]), ID);
    if (!onGh || onGh.cost !== 250 || onGh.qty !== 30) problems.push(`aanpassen: op GitHub ${JSON.stringify(onGh)} in plaats van 250 × 30`);
    if (!gh.puts) problems.push('aanpassen: niets naar GitHub geschreven');
    await ctx.close();
  }

  // 2. op een ander apparaat later gewijzigd: GitHub heeft de nieuwere prijs, dit apparaat nog de oude
  {
    const now = Date.now();
    const ctx = await context(browser, { [ID]: { cost: 180, qty: 1, sells: [], t: now - 3600e3 } });
    await fakeGitHub(ctx, base, { [ID]: { cost: 300, qty: 12, sells: [], t: now - 60e3 } }, 0);
    const pf = await ctx.newPage(); pf.on('pageerror', e => errs.push('Investeringen: ' + e.message));
    await pf.goto(base + 'portfolio.html');
    await pf.waitForTimeout(3500);
    const st = await stateOf(pf);
    if (st.cost !== 300 || st.qty !== 12) problems.push(`ander apparaat: ${st.cost} × ${st.qty} in plaats van de nieuwere 300 × 12`);
    await ctx.close();
  }

  await browser.close();
  srv.close();
  errs.forEach(e => problems.push('JS-fout: ' + e));
  problems.forEach(p => console.log('  FOUT', p));
  console.log(problems.length ? `SYNC: ${problems.length} probleem/problemen` : 'SYNC: aangepaste aankoopprijs blijft staan, nieuwere van een ander apparaat komt binnen');
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
