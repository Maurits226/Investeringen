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

  // Bestand uit de repository lezen en (met je token) ontsleutelen. null = niet beschikbaar.
  async function ghRead(file) {
    try {
      const headers = { Accept: 'application/vnd.github+json' };
      if (token()) headers.Authorization = 'Bearer ' + token();
      const r = await fetch(`https://api.github.com/repos/${GH.owner}/${GH.repo}/contents/${file}?ref=${GH.branch}&t=${Date.now()}`, { headers, cache: 'no-store' });
      if (!r.ok) return null;
      let parsed = JSON.parse(Vault.b64ToText((await r.json()).content));
      if (Vault.isLocked(parsed)) {
        if (!token()) return null;
        parsed = await Vault.unlock(parsed, token());
      }
      return parsed;
    } catch (e) { return null; }
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
  async function loadTrades() {
    const local = lsGet('radar_trades', []);
    const remote = await ghRead('radar.json');
    if (!remote || !Array.isArray(remote.trades)) return Array.isArray(local) ? local : [];
    const merged = mergeTrades(remote.trades, Array.isArray(local) ? local : []);
    if (JSON.stringify(merged) !== JSON.stringify(local)) lsSet('radar_trades', merged);
    return merged;
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
    window.addEventListener('message', e => { if (e.data && e.data.type === 'dashboard-shown') later(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) later(); });
  }

  return { lsGet, lsSet, ghRead, mergeState, mergeTrades, loadState, loadTrades,
           sellPctForStep, stepsReached, sellStatus, smartOn, thresholdFor, onRefresh };
})();
