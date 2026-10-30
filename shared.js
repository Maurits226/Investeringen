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

  // Beoordeling (notities, afgevinkte punten, kwartaalcheck): de laatst gewijzigde versie wint, waar die ook vandaan komt
  const newerReview = (a, b) => !a ? b : !b ? a : ((b.updated || '') > (a.updated || '') ? b : a);
  // Een vastgelegde heropende winsttrede (rearm, zie sellStatus) gaat bij samenvoegen niet verloren, van welke kant hij ook komt
  const sellKey = s => [s.level, s.date, s.price].join('|');
  function withRearms(sells, other) {
    if (!Array.isArray(sells) || !Array.isArray(other)) return sells;
    const flag = new Map(other.filter(s => s && s.rearm).map(s => [sellKey(s), s.rearm]));
    const add = s => s && !s.rearm && flag.has(sellKey(s));
    return sells.some(add) ? sells.map(s => add(s) ? { ...s, rearm: flag.get(sellKey(s)) } : s) : sells;
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
      if (out[id].sells) out[id].sells = withRearms(withRearms(out[id].sells, l.sells), r.sells);
      const rv = newerReview(r.review, l.review);
      if (rv) out[id].review = rv;
    });
    return out;
  }
  // Voor een pagina die zijn eigen state uploadt (Investeringen): eigen wijzigingen houden, maar nieuwere
  // beoordelingen en posities die alleen op GitHub staan (van een ander apparaat of tabblad) overnemen
  function keepRemoteExtras(local, remote) {
    const out = { ...(local || {}) };
    Object.entries(remote || {}).forEach(([id, r]) => {
      if (!r || typeof r !== 'object') return;
      if (!out[id]) { out[id] = r; return; }
      const rv = newerReview(out[id].review, r.review);
      if (rv && rv !== out[id].review) out[id] = { ...out[id], review: rv };
      const sv = withRearms(out[id].sells, r.sells);
      if (sv !== out[id].sells) out[id] = { ...out[id], sells: sv };
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
  // Opslaan gaat één voor één (een notitie en meteen daarna een vinkje mogen elkaar niet overschrijven);
  // is de versie op GitHub intussen veranderd, dan nog één keer opnieuw lezen, samenvoegen en schrijven.
  async function saveOnce(change) {
    const local = lsGet('portfolio_state', {});
    const remote = token() ? await ghReadFull('state.json') : { status: 'error' };
    const st = remote.status === 'ok' && remote.data && typeof remote.data === 'object' ? mergeState(remote.data, local) : local;
    change(st);
    lsSet('portfolio_state', st);
    if (!token()) return 'local';
    if (remote.status !== 'ok' && remote.status !== 'missing') return 'error';
    return (await ghWrite('state.json', st, remote.sha, 'Update portfolio state (versleuteld) ' + new Date().toISOString())) ? 'synced' : 'error';
  }
  let saveQueue = Promise.resolve(), saving = 0;
  function saveState(change) {
    saving++;
    const run = saveQueue.then(async () => {
      const res = await saveOnce(change);
      return res === 'error' && token() ? saveOnce(change) : res;
    });
    saveQueue = run.catch(() => {}).finally(() => { saving--; });
    return run;
  }
  const whenSaved = () => saveQueue;              // klaar met alle lopende opslagen (dan pas opnieuw inlezen)
  const isSaving = () => saving > 0;
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
        const html = await (await fetch('portfolio.html', { cache: 'no-cache' })).text();
        pos = [...html.matchAll(/\{[^{}]*?ticker:\s*'([^']+)'[^{}]*?\}/g)].map(m => {
          const o = m[0], g = re => (o.match(re) || [])[1];
          // 'ETF\'s' bevat een apostrof: tussen dubbele aanhalingstekens lezen we tot de dubbele, en andersom
          const q = key => { const x = o.match(new RegExp(key + `:\\s*(?:'((?:[^'\\\\]|\\\\.)*)'|"([^"]*)")`)); return x ? (x[1] != null ? x[1].replace(/\\(.)/g, '$1') : x[2]) : undefined; };
          return { ticker: m[1], section: q('section'), label: q('label'),
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
  // ── Top 250 in twee delen (screener.json met "fmt": 2): de lijst is licht, de details per aandeel (volledige scorecard,
  // 12 stappen, cijfers) staan in 16 shards (screener_det/00..15.json) en komen pas binnen als je een aandeel openklapt.
  // Eén download per shard; ?t=<updated> zorgt dat een nieuwe run nooit oude details geeft. Oude screener.json
  // (zonder fmt): de items zijn al volledig. Eigen posities (refs) zijn altijd volledig.
  let detVer = null, shards = {}, shardJobs = {};
  function detShard(sym, data) {
    const n = (data && data.det && +data.det.n) || 16;
    return String([...String(sym)].reduce((a, ch) => a + ch.codePointAt(0), 0) % n).padStart(2, '0');
  }
  const detUrl = (sym, data) => {
    const p = data.det && /^[\w-]+\/$/.test(data.det.path || '') ? data.det.path : 'screener_det/';
    return p + detShard(sym, data) + '.json?t=' + encodeURIComponent(data.updated || '');
  };
  // volledig = oud formaat, of het item heeft al de scorecard-criteria of de cijfers (die ontbreken in de lichte lijst)
  const isFull = (it, data) => !it || !data || data.fmt !== 2 || !!(it.card && it.card.crit) || it.mp !== undefined;
  function shardCache(data) {
    if (detVer !== data.updated) { detVer = data.updated; shards = {}; shardJobs = {}; }   // nieuwe run: oude shards weg
    return shards;
  }
  function loadShard(sym, data) {
    const url = detUrl(sym, data);
    if (shardCache(data)[url]) return Promise.resolve(shards[url]);
    if (!shardJobs[url]) {
      const jobs = shardJobs;
      jobs[url] = fetch(url).then(r => r.ok ? r.json() : null).catch(() => null).then(j => {
        delete jobs[url];                                // mislukt: niet onthouden, opnieuw proberen haalt hem opnieuw
        if (!j || !j.items || typeof j.items !== 'object') return null;
        if (jobs === shardJobs) shards[url] = j;
        return j;
      });
    }
    return shardJobs[url];
  }
  const withDetail = (it, det) => det ? Object.assign({}, it, det) : it;
  // De details van één aandeel (null: oud formaat, niet gevonden of niet te laden)
  async function screenerDetail(sym, data) {
    if (!sym || !data || data.fmt !== 2) return null;
    const sh = await loadShard(sym, data);
    return (sh && sh.items[sym]) || null;
  }
  // Het volledige item (lijst + details). null = de details konden niet geladen worden.
  async function fullItem(it, data) {
    if (isFull(it, data)) return it;
    const sh = await loadShard(it.sym, data);
    return sh ? withDetail(it, sh.items[it.sym]) : null;
  }
  // Zonder te wachten: het volledige item als het er al is (shard binnen), anders null
  function fullItemNow(it, data) {
    if (isFull(it, data)) return it;
    const sh = shardCache(data)[detUrl(it.sym, data)];
    return sh ? withDetail(it, sh.items[it.sym]) : null;
  }

  // ── These-bewaking (these.json, wekelijks per eigen aandeel: scorecard, afvallende stappen, kwartaalcijfers).
  // Vandaag en Beoordeling gebruiken allebei theseAlerts(), zodat ze nooit iets anders zeggen. Geen these.json: geen meldingen.
  async function loadThese() {
    try {
      const r = await fetch('these.json', { cache: 'no-cache' });
      const j = r.ok ? await r.json() : null;
      return j && j.stocks && typeof j.stocks === 'object' ? j : null;
    } catch (e) { return null; }
  }
  const ISO_D = /^\d{4}-\d\d-\d\d/;
  const dayNum = d => Math.floor(Date.parse(String(d).slice(0, 10) + 'T00:00:00Z') / 86400000);   // dagen sinds 1970 (UTC)
  const weekOf = d => { const n = dayNum(d); return n - (n + 3) % 7; };                           // maandag van de ISO-week
  const dayTxt = (d, today) => new Date(String(d).slice(0, 10) + 'T12:00:00Z').toLocaleDateString('nl-NL',
    Object.assign({ day: 'numeric', month: 'short', timeZone: 'UTC' }, String(d).slice(0, 4) !== String(today).slice(0, 4) ? { year: 'numeric' } : {}));
  const ptsTxt = v => Number.isInteger(+v) ? String(v) : (+v).toLocaleString('nl-NL', { maximumFractionDigits: 1 });
  const stepName = n => (typeof Steps !== 'undefined' && Steps.NAMES[n]) || '';
  // geldige momentopnames, oud → nieuw
  const theseHist = e => ((e && Array.isArray(e.hist)) ? e.hist : []).filter(h => h && ISO_D.test(h.d || ''))
    .slice().sort((a, b) => a.d < b.d ? -1 : a.d > b.d ? 1 : 0);
  const todayOf = t => ISO_D.test(String(t || '')) ? String(t).slice(0, 10) : new Date().toISOString().slice(0, 10);
  // Scorecard-verloop over de laatste 13 weken (zonder de huidige week) plus de stand van nu.
  // short = minder dan 2 weken historie: dan nog geen trend en geen trendmeldingen.
  function theseTrend(ref, entry, today) {
    const hist = theseHist(entry);
    if (!hist.length) return null;
    const t = todayOf(today), tn = dayNum(t), wk = weekOf(t);
    const cur = ref ? (ref.card && ref.card.s != null ? ref.card.s : null) : hist[hist.length - 1].s ?? null;
    const win = hist.filter(h => h.s != null && tn - dayNum(h.d) <= 91 && tn >= dayNum(h.d) && weekOf(h.d) !== wk);
    const pts = win.map(h => ({ d: h.d.slice(0, 10), s: +h.s }));
    if (cur != null) pts.push({ d: t, s: +cur });
    const base = win[0] || null;
    return { pts, cur, first: hist[0].d.slice(0, 10), short: tn - dayNum(hist[0].d) < 14,
             delta: base && cur != null ? Math.round((cur - base.s) * 10) / 10 : null, since: base ? base.d.slice(0, 10) : null,
             max: win.length ? Math.max(...win.map(h => +h.s)) : null };
  }
  // Meldingen voor één eigen aandeel. ref = item uit screener.json (refs), entry = these.stocks[ref.sym], review = je beoordeling.
  // Geeft [{kind, sev, text, title, since, ...}]: kind 'drop' (scorecard 10+ lager dan het hoogste punt in 13 weken),
  // 'newfail' (valt nu af bij een stap die 7+ dagen geleden nog niet faalde; stap 12 telt niet), 'earn' (nieuwe kwartaalcijfers
  // na je laatste check) of 'due' (geen cijferdatum bekend: 100 dagen niet gecheckt). sev 3 rood, 2 oranje, 1 let op, 0 routine.
  // text past in de kaart van het aandeel; title noemt de naam (voor lijsten zoals Vandaag).
  function theseAlerts(ref, entry, review, today) {
    const out = [], t = todayOf(today), tn = dayNum(t);
    const name = (ref && ref.name) || (entry && entry.name) || '';
    const hist = theseHist(entry), tr = theseTrend(ref, entry, t);
    const checked = review && ISO_D.test(review.checked || '') ? review.checked.slice(0, 10) : null;
    if (tr && !tr.short) {
      const win = tr.pts.slice(0, -1);
      if (tr.cur != null && tr.max != null && tr.max - tr.cur >= 10) {
        const at = win.filter(p => p.s === tr.max).pop();
        out.push({ kind: 'drop', sev: tr.max - tr.cur >= 20 ? 3 : 2, since: at.d, from: tr.max, to: tr.cur,
          text: `Scorecard ${ptsTxt(tr.max)} → ${ptsTxt(tr.cur)} (sinds ${dayTxt(at.d, t)})`,
          title: `Scorecard ${name} ${ptsTxt(tr.max)} → ${ptsTxt(tr.cur)} (sinds ${dayTxt(at.d, t)})` });
      }
    }
    // Nieuwe afvaller: een stap (niet 12, waardering) die eerder gehaald werd en nu faalt. De melding blijft staan tot je
    // na het begin ervan een kwartaalcheck afvinkt: een these die breekt, mag niet na een week vanzelf verdwijnen.
    // Faalde de stap al vanaf de eerste weekfoto, dan is hij niet 'nieuw' (dat staat al in het oordeel).
    const withF = hist.filter(h => Array.isArray(h.f));            // f kan null zijn (geen cijfers die week)
    const fails = ref ? ((ref.s12 && ref.s12.list) || []).filter(x => x.s === 'fail').map(x => +x.n) : ((withF[withF.length - 1] || {}).f || []);
    fails.filter(n => n !== 12).sort((a, b) => a - b).forEach(n => {
      let i = withF.length - 1;
      while (i >= 0 && withF[i].f.includes(n)) i--;                 // terug tot de laatste week waarin de stap nog gehaald werd
      if (i < 0) return;
      const since = withF[i + 1] ? withF[i + 1].d.slice(0, 10) : t;
      if (checked && checked >= since) return;
      const txt = `valt nu af bij stap ${n} (${stepName(n)})`;
      out.push({ kind: 'newfail', sev: [8, 11].includes(n) ? 3 : 2, step: n, since, text: `${txt} · sinds ${dayTxt(since, t)}`, title: `${name} ${txt}` });
    });
    const le = entry && ISO_D.test(entry.last_earn || '') && entry.last_earn.slice(0, 10) <= t ? entry.last_earn.slice(0, 10) : null;
    if (le) {
      if (!checked || checked < le) out.push({ kind: 'earn', sev: 1, since: le,
        text: `Kwartaalcijfers gepubliceerd op ${dayTxt(le, t)} — nog niet gecheckt`, title: `Kwartaalcijfers ${name} (${dayTxt(le, t)}): kwartaalcheck` });
    } else if (!checked || tn - dayNum(checked) > 100) {
      out.push({ kind: 'due', sev: 0, since: checked, text: checked ? `kwartaalcheck: ${tn - dayNum(checked)} d geleden` : 'kwartaalcheck: nog nooit gedaan',
        title: `Kwartaalcheck ${name}` });
    }
    return out.sort((a, b) => b.sev - a.sev);
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
  // Een afgevinkte trede gaat weer open als de koers ná die verkoop 25% of meer onder je verkoopkoers is gezakt
  // (laagste slotkoers per week uit data.json, dus ook als je het dashboard toen niet opende). Het verkoopsignaal komt
  // pas terug als je winst dat niveau weer haalt, gerekend op je aankoopprijs: koop je in de daling bij en pas je de
  // aankoopprijs aan, dan verkoop je die trede opnieuw bij +50/100/150% op die nieuwe prijs.
  // Investeringen legt een heropende trede vast in je state (rearm), zodat hij open blijft als die daling later
  // uit het jaar koersdata valt.
  const REOPEN_DROP = 0.25;
  const sellPctForStep = step => [10, 20, 30][(step - 1) % 3];
  const stepsReached = plPct => plPct < 50 ? 0 : Math.floor(plPct / 50);
  // Verkoopdatum ('24-10-2026' zoals nl-NL hem schrijft, of ISO) → '2026-10-24'
  function isoDay(d) {
    const s = String(d || '').trim(), m = /^(\d{1,2})[-./](\d{1,2})[-./](\d{4})$/.exec(s);
    return m ? `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}` : /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : null;
  }
  // Laagste koers ná de week van de verkoop (wk = { from: maandag van week 0, low: [laagste slotkoers per week] })
  // en de koers van nu. De verkoopweek zelf telt niet mee: daarin zitten ook de dagen vóór je verkoop.
  function lowSince(wk, date, price) {
    let low = price > 0 ? +price : Infinity;
    const day = isoDay(date), t0 = wk ? Date.parse(wk.from + 'T00:00:00Z') : NaN;
    if (!day || !isFinite(t0) || !Array.isArray(wk.low)) return low;
    const first = Math.max(0, Math.floor((Date.parse(day + 'T00:00:00Z') - t0) / 6048e5) + 1);
    for (let i = first; i < wk.low.length; i++) if (wk.low[i] > 0 && wk.low[i] < low) low = wk.low[i];
    return low;
  }
  function sellStatus(sells, plPct, price, wk) {
    sells = (sells || []).filter(s => s && +s.level > 0);
    const reached = stepsReached(plPct);
    const last = step => { const x = sells.filter(s => +s.level === +step); return x.length ? x[x.length - 1] : null; };
    // Weer open: vastgelegd, of de koers zakte na de laatste verkoop op deze trede 25% onder die verkoopkoers
    const reopened = step => { const v = last(step);
      return !!(v && (v.rearm || (v.price > 0 && lowSince(wk, v.date, price) <= v.price * (1 - REOPEN_DROP)))); };
    const maxDone = sells.length ? Math.max(...sells.map(s => +s.level)) : 0;
    let openStep = null;
    for (let step = 1; step <= maxDone; step++) if (reopened(step)) { openStep = step; break; }
    const isHeropend = openStep != null;
    const nextStep = isHeropend ? openStep : maxDone + 1;
    return { reached, maxDone, isHeropend, nextStep, nextTargetPct: nextStep * 50, nextSellPct: sellPctForStep(nextStep),
             canRealise: reached >= nextStep, last, reopened };
  }
  // Verkopen die volgens de koersdata weer open zijn, maar nog niet als 'rearm' in je state staan
  function newRearms(sells, price, wk) {
    const S = sellStatus(sells, 0, price, wk), out = [];
    for (let step = 1; step <= S.maxDone; step++) { const v = S.last(step); if (v && !v.rearm && S.reopened(step)) out.push(v); }
    return out;
  }

  // ── Koopdrempel: slim = halve jaarbeweeglijkheid tussen 8% en 35%, anders je vaste drempel
  const smartOn = () => { try { return localStorage.getItem('buy_mode') !== 'vast'; } catch (e) { return true; } };
  const thresholdFor = (p, q) => smartOn() && q && q.vol ? Math.round(Math.max(8, Math.min(35, q.vol * 0.5))) : (p.thresh || 20);

  // ── Aandeel zoeken (positie toevoegen). zoek.json komt uit de Top 250-run: elk bedrijf dat de scan kent, met zijn
  // noteringen in euro en dollar. Per bedrijf gegroepeerd, de handigste notering voorop: de thuisbeurs van een
  // Europees bedrijf, dan Xetra (.DE), dan de Amerikaanse hoofdbeurs, dan Frankfurt/Düsseldorf en Wenen.
  // OTC-noteringen (PNK) en Milanese dubbelnoteringen van buitenlandse aandelen (1ASML.MI) laten we weg.
  const MARKET = { DE: 'Xetra', F: 'Frankfurt', DU: 'Düsseldorf', SG: 'Stuttgart', MU: 'München', BE: 'Berlijn', HM: 'Hamburg',
    AS: 'Amsterdam', PA: 'Parijs', BR: 'Brussel', MI: 'Milaan', MC: 'Madrid', LS: 'Lissabon', HE: 'Helsinki', IR: 'Dublin', VI: 'Wenen', AT: 'Athene' };
  const US_EXCH = { NMS: 'NASDAQ', NGM: 'NASDAQ', NCM: 'NASDAQ', NYQ: 'NYSE', ASE: 'NYSE American', PCX: 'NYSE Arca', BTS: 'Cboe' };
  function listing(sym, exch) {
    const m = /\.([A-Z]{1,3})$/.exec(sym);
    if (!m) return exch === 'PNK' || exch === 'OTC' ? null : { sym, cur: '$', market: US_EXCH[exch] || 'VS', rank: 3 };
    if (!MARKET[m[1]] || (m[1] === 'MI' && /^\d/.test(sym))) return null;
    const rank = m[1] === 'DE' ? 2 : m[1] === 'VI' ? 5 : ['F', 'DU', 'SG', 'MU', 'BE', 'HM'].includes(m[1]) ? 4 : 1;
    return { sym, cur: '€', market: MARKET[m[1]], rank };
  }
  // Hele naam zonder rechtsvorm: 'Bank of America' en 'Bank of Hawaii' blijven twee bedrijven
  const LEGAL = new Set(['inc', 'incorporated', 'corp', 'corporation', 'co', 'company', 'plc', 'n', 'v', 'nv', 'se', 'sa', 'ag', 'spa', 'ab', 'asa', 'oyj',
    'as', 'a', 'b', 's', 'kgaa', 'holding', 'holdings', 'group', 'class', 'the', 'ltd', 'limited', 'com', 'adr']);
  const fold = s => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const coKey = n => fold(n).replace(/[^a-z0-9]+/g, ' ').split(' ').filter(w => w && !LEGAL.has(w)).join(' ');
  let zoekP = null;
  function searchIndex() {
    if (!zoekP) zoekP = fetch('zoek.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).catch(() => null).then(d => {
      if (!d || !Array.isArray(d.rows)) { zoekP = null; return []; }
      const groups = new Map();
      d.rows.forEach(([name, sym, eu, exch, sector, mcap]) => {
        const key = coKey(name) || sym;
        let g = groups.get(key);
        if (!g) groups.set(key, g = { name, sector: null, mcap: 0, hay: fold(name), list: [], scan: null, first: sym });
        if (!g.scan && sector) g.scan = sym;        // de notering die de Top 250 scant (heeft cijfers en een sector)
        g.sector = g.sector || sector;
        g.mcap = Math.max(g.mcap, mcap || 0);
        [listing(sym, exch), eu && listing(eu)].forEach(l => { if (l && !g.list.some(x => x.sym === l.sym)) g.list.push(l); });
      });
      const out = [...groups.values()].filter(g => g.list.length);
      out.forEach(g => { g.scan = g.scan || g.first; });
      out.forEach(g => g.list.sort((a, b) => a.rank - b.rank));
      return out;
    });
    return zoekP;
  }
  // Best passende bedrijven voor wat je typt: ticker exact > naam begint ermee > elk woord begint ermee > ergens in de naam > ticker begint ermee
  function searchStocks(groups, q, limit = 8) {
    const f = fold(q).trim(), up = String(q || '').trim().toUpperCase();
    if (f.length < 2) return [];
    const words = f.split(/\s+/);
    const hit = g => {
      const base = s => s.sym.split('.')[0];
      if (g.list.some(s => s.sym === up || base(s) === up)) return 100;
      const nw = g.hay.split(/[^a-z0-9]+/);
      if (g.hay.startsWith(f)) return /[^a-z0-9]/.test(g.hay.charAt(f.length) || ' ') ? 85 : 80;   // 'novo' = heel woord: Novo Nordisk voor Novozymes
      if (words.every(w => nw.includes(w))) return 70;
      if (words.every(w => nw.some(x => x.startsWith(w)))) return 60;
      if (f.length >= 3 && g.hay.includes(f)) return 30;
      if (up.length >= 2 && g.list.some(s => s.sym.startsWith(up))) return 20;
      return 0;
    };
    return groups.map(g => ({ g, s: hit(g) })).filter(x => x.s).sort((a, b) => b.s - a.s || b.g.mcap - a.g.mcap).slice(0, limit).map(x => x.g);
  }

  // tickers.txt is openbaar (geen bedragen) en bepaalt van welke tickers de workflows koersen ophalen. Met je token zet het
  // dashboard er een nieuwe positie in als 'TICKER, Naam, Categorie  # via dashboard'; verwijderen doet het alleen bij zulke regels.
  const TICKER_OK = /^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$/;
  const txtSym = line => (line.trim().split(/[\s,;|#]/)[0] || '').toUpperCase();
  const plain = s => String(s || '').replace(/[,;|#\t\r\n]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60);
  // Een openbaar tekstbestand in de repository wijzigen met je token: nieuwste versie lezen, change(oud) → nieuw (null = niets),
  // terugschrijven. Tussendoor gewijzigd (409/422): opnieuw lezen en nog één keer. create: het bestand mag nog niet bestaan.
  async function editRepoFile(file, change, message, create = false) {
    if (!token()) return 'no-token';
    for (let i = 0; i < 2; i++) {
      try {
        const r = await fetch(`${api(file)}?ref=${GH.branch}&t=${Date.now()}`, { headers: ghHeaders(), cache: 'no-store' });
        if (!r.ok && !(create && r.status === 404)) return 'error';
        const j = r.ok ? await r.json() : null, old = j ? Vault.b64ToText(j.content) : '', next = change(old);
        if (next == null || next === old) return 'same';
        const body = { message, content: Vault.textToB64(next), branch: GH.branch };
        if (j) body.sha = j.sha;
        const w = await fetch(api(file), { method: 'PUT', headers: Object.assign(ghHeaders(), { 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
        if (w.ok) return 'ok';
        if (w.status !== 409 && w.status !== 422) return 'error';
      } catch (e) { return 'error'; }
    }
    return 'error';
  }
  const editTickers = (change, message) => editRepoFile('tickers.txt', change, message);
  function addTicker(sym, name, section) {
    sym = String(sym || '').toUpperCase();
    if (!TICKER_OK.test(sym)) return Promise.resolve('error');
    return editTickers(txt => txt.split('\n').some(l => !l.trim().startsWith('#') && txtSym(l) === sym) ? null
      : txt.replace(/\s*$/, '\n') + `${sym}, ${plain(name) || sym}, ${plain(section) || 'Stocks'}  # via dashboard\n`,
      `Ticker toegevoegd via het dashboard: ${sym}`);
  }
  function removeTicker(sym) {
    sym = String(sym || '').toUpperCase();
    if (!TICKER_OK.test(sym)) return Promise.resolve('error');
    return editTickers(txt => {
      const lines = txt.split('\n'), keep = lines.filter(l => !(txtSym(l) === sym && /#\s*via dashboard\s*$/.test(l)));
      return keep.length === lines.length ? null : keep.join('\n');
    }, `Ticker verwijderd via het dashboard: ${sym}`);
  }

  // ── Stock opzoeken (tab Stock opzoeken): opzoeklijst.json is openbaar (alleen tickers, naam en datum, geen bedragen). Een wijziging
  // start de Top 250-run, die elk aandeel uit de lijst scoort (opzoeken.json). Nieuwste bovenaan; na LOOK_DAYS dagen valt
  // een aandeel er vanzelf uit (de run negeert het dan, en de volgende wijziging haalt het weg), verwijderen kan altijd.
  const LOOK_DAYS = 30, LOOK_MAX = 50;
  const lookFresh = e => !!e && TICKER_OK.test(e.sym) && Date.now() - Date.parse(e.at) < LOOK_DAYS * 864e5;
  const lookName = s => String(s || '').replace(/[\u0000-\u001f]+/g, ' ').trim().slice(0, 80) || null;
  function lookEdit(fn, message) {
    return editRepoFile('opzoeklijst.json', old => {
      let d = null;
      try { d = JSON.parse(old || 'null'); } catch (e) { d = null; }
      const items = (d && Array.isArray(d.items) ? d.items : []).filter(lookFresh);
      const next = fn(items).slice(0, LOOK_MAX);
      return JSON.stringify({ v: 1, days: LOOK_DAYS, items: next }, null, 1) + '\n';
    }, message, true);
  }
  // sym = de notering die je zoekt, alt = andere noteringen van hetzelfde bedrijf (de run kiest er een die de scan al kent)
  function lookAdd(sym, alt, name) {
    sym = String(sym || '').trim().toUpperCase();
    if (!TICKER_OK.test(sym)) return Promise.resolve('error');
    alt = [...new Set((alt || []).map(a => String(a || '').toUpperCase()))].filter(a => a !== sym && TICKER_OK.test(a)).slice(0, 8);
    const at = new Date().toISOString().slice(0, 19) + 'Z';
    return lookEdit(items => [{ sym, alt, name: lookName(name), at }].concat(items.filter(e => e.sym !== sym)), `Stock opzoeken: ${sym}`);
  }
  function lookRemove(sym) {
    sym = String(sym || '').trim().toUpperCase();
    return lookEdit(items => items.filter(e => e.sym !== sym), `Stock opzoeken: ${sym} verwijderd`);
  }
  const lookClean = () => lookEdit(items => items, 'Stock opzoeken: aandelen ouder dan 30 dagen weggehaald');

  // ── Opnieuw tonen als een andere pagina iets wijzigt, of als dit tabblad weer zichtbaar wordt
  const WATCH = ['portfolio_state', 'portfolio_positions', 'radar_trades', 'buy_mode', 'gh_token'];
  function onRefresh(fn) {
    let t = null;
    const later = () => { clearTimeout(t); t = setTimeout(fn, 150); };
    window.addEventListener('storage', e => { if (!e.key || WATCH.includes(e.key)) later(); });
    window.addEventListener('message', e => { if (e.origin === location.origin && e.data && e.data.type === 'dashboard-shown') later(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) later(); });
  }

  return { normName, stockIndex, detShard, screenerDetail, fullItem, fullItemNow, loadThese, theseTrend, theseAlerts, techExposure, lsGet, lsSet, ghRead, ghReadFull, ghWrite, checkToken, TOKEN_HELP, mergeState, keepRemoteExtras, whenSaved, isSaving, mergeTrades, loadState, saveState, loadTrades, positions, posId,
           sellPctForStep, stepsReached, sellStatus, newRearms, smartOn, thresholdFor, onRefresh, searchIndex, searchStocks, addTicker, removeTicker,
           editRepoFile, TICKER_OK, LOOK_DAYS, lookFresh, lookAdd, lookRemove, lookClean };
})();
