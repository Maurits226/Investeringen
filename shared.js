// Gedeeld door alle pagina's van het dashboard, zodat overal hetzelfde staat:
//  * dezelfde gegevens: je portefeuille-state (aankoopprijzen, verkopen) en opgevolgde adviezen,
//    lokaal én versleuteld op GitHub (state.json / radar.json), samengevoegd op dezelfde manier;
//  * dezelfde regels: winstnemingstrap en koopdrempel;
//  * verversen zodra een andere pagina iets wijzigt of je naar een ander tabblad gaat.
// Vereist vault.js (voor het ontsleutelen met je token).
const Shared = (() => {
  const GH = { owner: 'Maurits226', repo: 'Investeringen', branch: 'main' };
  const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const token = () => { try { return localStorage.getItem('gh_token') || ''; } catch (e) { return ''; } };

  const api = file => `https://api.github.com/repos/${GH.owner}/${GH.repo}/contents/${file}`;
  const ghHeaders = () => Object.assign({ Accept: 'application/vnd.github+json' }, token() ? { Authorization: 'Bearer ' + token() } : {});
  // Bestand uit de repository lezen en (met je token) ontsleutelen.
  // status: 'ok' | 'missing' (bestaat niet) | 'locked' (niet te openen met deze token) | 'error'
  async function ghReadFull(file) {
    try {
      const r = await fetch(`${api(file)}?ref=${GH.branch}&t=${Date.now()}`, { headers: ghHeaders(), cache: 'no-store' });
      if (r.status === 404) return { status: 'missing', sha: null, data: null };
      if (!r.ok) return { status: 'error', sha: null, data: null };
      const j = await r.json();
      let parsed = JSON.parse(Vault.b64ToText(j.content));
      if (Vault.isLocked(parsed)) {
        if (!token()) return { status: 'locked', sha: j.sha, data: null };
        try { parsed = await Vault.unlock(parsed, token()); } catch (e) { return { status: 'locked', sha: j.sha, data: null }; }
      }
      return { status: 'ok', sha: j.sha, data: parsed };
    } catch (e) { return { status: 'error', sha: null, data: null }; }
  }
  async function ghRead(file) { return (await ghReadFull(file)).data; }
  // Versleuteld (met de token van nu) naar de repository schrijven. sha = de versie die je overschrijft (null = nieuw bestand).
  async function ghWrite(file, data, sha, message) {
    if (!token()) return false;
    try {
      const locked = await Vault.lock(data, token());
      const body = { message: message || `Update ${file} (versleuteld) ${new Date().toISOString()}`,
                     content: Vault.textToB64(JSON.stringify(locked, null, 2)), branch: GH.branch };
      if (sha) body.sha = sha;
      const r = await fetch(api(file), { method: 'PUT', headers: Object.assign(ghHeaders(), { 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
      return r.ok;
    } catch (e) { return false; }
  }
  // Is dit een geschikte token? Alleen fine-grained (github_pat_...), en GitHub moet hem kennen. Geeft een foutmelding of null.
  const TOKEN_HELP = 'Maak een fine-grained token op github.com/settings/personal-access-tokens/new:\n'
    + '• Repository access: Only select repositories → Investeringen\n'
    + '• Repository permissions: Contents → Read and write (verder niets)\n'
    + '• Expiration: maximaal 90 dagen\n\n'
    + 'Gebruik geen classic token (ghp_...): die opent al je repositories. De token staat in de browseropslag van dit apparaat, '
    + 'gebruik het dashboard dus alleen op je eigen apparaten.';
  async function checkToken(t) {
    t = String(t || '').trim();
    if (/^gh[opsur]_/.test(t)) return 'Dit is een classic token. Die geeft toegang tot al je repositories en je account. Maak een fine-grained token (github_pat_...) met alleen toegang tot Investeringen.';
    if (!/^github_pat_[A-Za-z0-9_]{20,}$/.test(t)) return 'Dit lijkt geen fine-grained GitHub-token (die begint met github_pat_).';
    try {
      const r = await fetch(`https://api.github.com/repos/${GH.owner}/${GH.repo}`, { headers: { Accept: 'application/vnd.github+json', Authorization: 'Bearer ' + t }, cache: 'no-store' });
      if (r.status === 401) return 'GitHub kent deze token niet (verlopen of niet volledig gekopieerd).';
      if (r.status === 404 || r.status === 403) return 'Deze token heeft geen toegang tot de repository Investeringen.';
    } catch (e) {}                                   // geen netwerk: niet tegenhouden
    return null;
  }

  // Portefeuille-state: GitHub is leidend, lokaal bekende aankoopprijzen/verkopen vullen ontbrekende aan
  function mergeState(remote, local) {
    const out = {};
    new Set([...Object.keys(remote || {}), ...Object.keys(local || {})]).forEach(id => {
      const r = (remote || {})[id], l = (local || {})[id];
      if (!r) { out[id] = l; return; }
      if (!l || typeof r !== 'object') { out[id] = r; return; }
      out[id] = { ...l, ...r };
      if (r.cost == null && l.cost != null) out[id].cost = l.cost;
      if (r.qty == null && l.qty != null) out[id].qty = l.qty;
      if (!(r.sells && r.sells.length) && l.sells && l.sells.length) out[id].sells = l.sells;
    });
    return out;
  }
  // Opgevolgde adviezen: per advies de laatst gewijzigde versie
  function mergeTrades(a, b) {
    const m = {};
    [...(a || []), ...(b || [])].forEach(t => { if (t && t.id && (!m[t.id] || (t.updated || '') > (m[t.id].updated || ''))) m[t.id] = t; });
    return Object.values(m).sort((x, y) => x.id.localeCompare(y.id));
  }
  async function loadState() {
    const local = lsGet('portfolio_state', {});
    const remote = await ghRead('state.json');
    if (!remote || typeof remote !== 'object') return local;
    const merged = mergeState(remote, local);
    if (JSON.stringify(merged) !== JSON.stringify(local)) lsSet('portfolio_state', merged);
    return merged;
  }
  // Portefeuille-state wijzigen vanaf elke pagina: eerst de nieuwste versie (lokaal + GitHub), dan de wijziging,
  // dan lokaal én versleuteld naar GitHub. Schrijft NIET naar GitHub als de versie daar niet te openen is
  // (andere token): anders zou je gegevens van een ander apparaat wissen. Geeft 'synced' | 'local' | 'error'.
  async function saveState(change) {
    const local = lsGet('portfolio_state', {});
    const remote = token() ? await ghReadFull('state.json') : { status: 'error' };
    const st = remote.status === 'ok' && remote.data && typeof remote.data === 'object' ? mergeState(remote.data, local) : local;
    change(st);
    lsSet('portfolio_state', st);
    if (!token()) return 'local';
    if (remote.status !== 'ok' && remote.status !== 'missing') return 'error';
    return (await ghWrite('state.json', st, remote.sha, 'Update portfolio state (versleuteld) ' + new Date().toISOString())) ? 'synced' : 'error';
  }
  async function loadTrades() {
    const local = lsGet('radar_trades', []);
    const remote = await ghRead('radar.json');
    if (!remote || !Array.isArray(remote.trades)) return Array.isArray(local) ? local : [];
    const merged = mergeTrades(remote.trades, Array.isArray(local) ? local : []);
    if (JSON.stringify(merged) !== JSON.stringify(local)) lsSet('radar_trades', merged);
    return merged;
  }

  // Je posities: van dit apparaat (zoals ingesteld op Investeringen), anders de openbare lijst uit portfolio.html.
  // Met de state (aankoopprijs, aantal, verkopen, beoordeling) erbij, op dezelfde manier als overal.
  const posId = p => (p.section || 'X') + '|' + p.ticker;
  async function positions() {
    let pos = lsGet('portfolio_positions', null);
    if (!pos || !pos.length) {
      try {
        const html = await (await fetch('portfolio.html?t=' + Date.now(), { cache: 'no-store' })).text();
        pos = [...html.matchAll(/\{[^{}]*?ticker:\s*'([^']+)'[^{}]*?\}/g)].map(m => {
          const o = m[0], g = re => (o.match(re) || [])[1];
          return { ticker: m[1], section: g(/section:\s*(?:'|")(.*?)(?:'|")/), label: g(/label:\s*(?:'|")(.*?)(?:'|")/),
                   watch: /watch:\s*true/.test(o), thresh: +(g(/thresh:\s*([0-9.]+)/) || 20) };
        });
      } catch (e) { pos = []; }
    }
    const st = await loadState();
    return pos.filter(p => p && p.ticker).map(p => {
      const s = st[posId(p)] || {};
      return { ...p, id: posId(p), cost: s.cost ?? p.cost ?? null, qty: s.qty ?? p.qty ?? 1, sells: s.sells || [], review: s.review || {} };
    });
  }

  // Een positie terugvinden in de Top 250-data (screener.json: refs + items): op euro-ticker, dollarticker,
  // de Amerikaanse notering die de Patroonradar vond (signals.json) of anders op de bedrijfsnaam (data.json).
  const normName = n => String(n || '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/)
    .filter(w => w && !['inc', 'corp', 'corporation', 'co', 'plc', 'nv', 'se', 'sa', 'ag', 'holdings', 'holding', 'group', 'class', 'the', 'ltd', 'a', 'com'].includes(w)).slice(0, 2).join(' ');
  function stockIndex(scr, sig, data) {
    const pool = ((scr && scr.refs) || []).concat((scr && scr.items) || []);
    const quotes = (data && data.quotes) || {};
    const usOf = {};
    ((sig && sig.items) || []).concat((sig && sig.errors) || []).forEach(i => { if (i.pattern_symbol) usOf[i.symbol.toUpperCase()] = i.pattern_symbol.toUpperCase(); });
    const find = ticker => {
      const t = String(ticker || '').toUpperCase(), us = usOf[t], qn = (quotes[t] || {}).name;
      const hit = s => !!s && pool.find(h => h.sym.toUpperCase() === s || (h.alt || []).some(a => String(a).toUpperCase() === s));
      return pool.find(h => (h.eu || '').toUpperCase() === t) || hit(t) || hit(us)
        || (qn ? pool.find(h => normName(h.name) === normName(qn)) : null) || null;
    };
    return { find, usOf, quotes };
  }
  // Tech-ETF's (voor de tech-blootstelling): volledig technologie
  const TECH_ETF = /^(QDVE|IUIT|XDWT|IITU|EQQQ|CNDX|SXLK|TNOW|SEMI|CHIP|SMH|SOXX|VVSM)\b/i;
  // Hoeveel van je portefeuille (koerswaarde) zit in tech en chips? held = [{p, value}], find = stockIndex(...).find
  function techExposure(held, find) {
    let tot = 0, tech = 0; const names = [];
    held.forEach(({ p, value }) => {
      if (!value || !isFinite(value)) return;
      tot += value;
      const it = p.section === 'Stocks' ? find(p.ticker) : null;
      const isTech = (it && (it.sector === 'Technology' || /Semiconductor/i.test(it.industry || ''))) || (p.section !== 'Stocks' && TECH_ETF.test(p.ticker));
      if (isTech) { tech += value; names.push({ n: (it && it.name) || p.label || p.ticker, v: value }); }
    });
    return tot ? { pct: tech / tot * 100, names: names.sort((a, b) => b.v - a.v).map(x => x.n) } : null;
  }

  // ── Winstnemingstrap: elke +50% een trede; trede 1,4,7.. = 10%, 2,5,8.. = 20%, 3,6,9.. = 30% verkopen.
  // Een afgevinkte trede gaat weer open als de koers 25% of meer onder je verkoopkoers zakt
  // (alleen zolang je nog boven dat winstniveau zit).
  const REOPEN_DROP = 0.25;
  const sellPctForStep = step => [10, 20, 30][(step - 1) % 3];
  const stepsReached = plPct => plPct < 50 ? 0 : Math.floor(plPct / 50);
  function sellStatus(sells, plPct, price) {
    sells = sells || [];
    const reached = stepsReached(plPct);
    const last = step => { const x = sells.filter(s => s.level === step); return x.length ? x[x.length - 1] : null; };
    const reopened = step => { const v = last(step); return !!(v && v.price != null && step <= reached && price <= v.price * (1 - REOPEN_DROP)); };
    const maxDone = sells.length ? Math.max(...sells.map(s => s.level)) : 0;
    let openStep = null;
    for (let step = 1; step <= maxDone; step++) if (last(step) && reopened(step)) { openStep = step; break; }
    const isHeropend = openStep != null;
    const nextStep = isHeropend ? openStep : maxDone + 1;
    return { reached, maxDone, isHeropend, nextStep, nextTargetPct: nextStep * 50, nextSellPct: sellPctForStep(nextStep),
             canRealise: isHeropend || reached >= nextStep, last, reopened };
  }

  // ── Koopdrempel: slim = halve jaarbeweeglijkheid tussen 8% en 35%, anders je vaste drempel
  const smartOn = () => { try { return localStorage.getItem('buy_mode') !== 'vast'; } catch (e) { return true; } };
  const thresholdFor = (p, q) => smartOn() && q && q.vol ? Math.round(Math.max(8, Math.min(35, q.vol * 0.5))) : (p.thresh || 20);

  // ── Opnieuw tonen als een andere pagina iets wijzigt, of als dit tabblad weer zichtbaar wordt
  const WATCH = ['portfolio_state', 'portfolio_positions', 'radar_trades', 'buy_mode', 'gh_token'];
  function onRefresh(fn) {
    let t = null;
    const later = () => { clearTimeout(t); t = setTimeout(fn, 150); };
    window.addEventListener('storage', e => { if (!e.key || WATCH.includes(e.key)) later(); });
    window.addEventListener('message', e => { if (e.origin === location.origin && e.data && e.data.type === 'dashboard-shown') later(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) later(); });
  }

  return { normName, stockIndex, techExposure, lsGet, lsSet, ghRead, ghReadFull, ghWrite, checkToken, TOKEN_HELP, mergeState, mergeTrades, loadState, saveState, loadTrades, positions, posId,
           sellPctForStep, stepsReached, sellStatus, smartOn, thresholdFor, onRefresh };
})();
