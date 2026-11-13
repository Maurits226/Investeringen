// Rekenregels uit shared.js zonder browser (draait mee in tests/test_static.py; lokaal: node tests/test_rules.js).
// Winstnemingstrap: een verkochte trede gaat pas weer open na 25% daling onder die verkoop, en het verkoopsignaal
// komt pas terug als je winst (op je eventueel aangepaste aankoopprijs) dat niveau weer haalt.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const store = {};
const ctx = { localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
  window: { addEventListener() {} }, document: { addEventListener() {}, visibilityState: 'visible' }, fetch: async () => ({ ok: false, status: 404 }), console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'shared.js'), 'utf8') + '\nthis.Shared = Shared;', ctx);
const S = ctx.Shared;

const fails = [];
const check = (ok, msg) => { if (!ok) { fails.push(msg); console.log('  FOUT', msg); } };

// Aankoop 100, verkocht op +50% (150), +100% (200) en +150% (250, op 1-6-2026, een maandag)
const sells = () => [
  { level: 1, pct: 10, date: '2-3-2026', price: 150 },
  { level: 2, pct: 20, date: '6-4-2026', price: 200 },
  { level: 3, pct: 30, date: '1-6-2026', price: 250 },
];
const pl = (price, cost) => (price / cost - 1) * 100;
// Laagste slotkoers per week vanaf maandag 25-5-2026 (week 0); week 1 = de verkoopweek van trede 3
const wk = lows => ({ from: '2026-05-25', low: lows });

let st = S.sellStatus(sells(), pl(260, 100), 260, wk([240, 245, 250, 255]));
check(!st.isHeropend && st.nextStep === 4 && !st.canRealise, `geen daling: volgende trede 4, geen signaal (${JSON.stringify(st)})`);

// Daling tot 187 (25% onder 250) in de week na de verkoop: trede 3 weer open, maar nog geen signaal op +100%
st = S.sellStatus(sells(), pl(200, 100), 200, wk([240, 248, 187, 195]));
check(st.isHeropend && st.nextStep === 3 && !st.canRealise, `na 25% daling: trede 3 weer open, nog geen signaal (${JSON.stringify(st)})`);
// Precies 25% telt mee, 24,8% niet
check(S.sellStatus(sells(), 0, 300, wk([240, 248, 187.5])).isHeropend, 'precies 25% onder de verkoop telt mee');
check(!S.sellStatus(sells(), 0, 300, wk([240, 248, 188])).isHeropend, '24,8% onder de verkoop is niet genoeg');

// Bijgekocht in de daling: aankoopprijs 140. Weer +150% op die prijs (350): verkoopsignaal voor trede 3 terug
st = S.sellStatus(sells(), pl(350, 140), 350, wk([240, 248, 187, 195, 300, 350]));
check(st.isHeropend && st.nextStep === 3 && st.canRealise && st.nextSellPct === 30, `weer +150%: opnieuw 30% verkopen op trede 3 (${JSON.stringify(st)})`);
// Koers terug op 260 maar met aankoopprijs 140 is dat +86%: geen signaal
check(!S.sellStatus(sells(), pl(260, 140), 260, wk([240, 248, 187, 260])).canRealise, 'op +86% na bijkopen: nog geen signaal');

// De daling zat alleen in de verkoopweek zelf (dagen vóór de verkoop): telt niet
check(!S.sellStatus(sells(), 0, 300, wk([240, 180, 250, 260])).isHeropend, 'laagste koers in de verkoopweek telt niet mee');
// Zonder weekdata telt de koers van nu
check(S.sellStatus(sells(), 0, 180, null).isHeropend, 'zonder weekdata: koers van nu 25% onder de verkoop = weer open');
// Vastgelegd (rearm) blijft open, ook als de daling uit de weekdata is gevallen
const flagged = sells(); flagged[2].rearm = '1-7-2026';
check(S.sellStatus(flagged, 0, 300, wk([260, 270])).isHeropend, 'vastgelegde heropening blijft open');

// Opnieuw gerealiseerd op trede 3: die verkoop is de nieuwe maatstaf, volgende is trede 4
const again = sells().concat([{ level: 3, pct: 30, date: '5-10-2026', price: 350 }]);
st = S.sellStatus(again, pl(355, 140), 355, wk([240, 248, 187, 195, 300, 350]));
check(!st.isHeropend && st.nextStep === 4 && !st.canRealise, `na opnieuw realiseren: volgende trede 4 (${JSON.stringify(st)})`);

// Daling tot 140: treden 2 (200) en 3 (250) weer open, trede 1 (150) niet; de laagste heropende eerst
st = S.sellStatus(sells(), pl(210, 100), 210, wk([240, 248, 140]));
check(st.nextStep === 2 && st.isHeropend && st.canRealise && !st.reopened(1) && st.reopened(3), `laagste heropende trede eerst (${JSON.stringify(st)})`);

// Gewone trap zonder verkopen: +50% = trede 1 (10%), +100% = trede 2 (20%)
st = S.sellStatus([], 55, 155, null);
check(st.canRealise && st.nextStep === 1 && st.nextSellPct === 10, 'trede 1 bij +50%');
check(!S.sellStatus([{ level: 1, pct: 10, date: '2-3-2026', price: 150 }], 99, 199, null).canRealise, 'trede 2 pas bij +100%');
// Oude registraties met het niveau als tekst
check(S.sellStatus([{ level: '1', pct: 10, date: '2-3-2026', price: 150 }], 99, 199, null).nextStep === 2, 'niveau als tekst');

// Vastleggen: newRearms geeft de verkopen die nog geen vlag hebben, daarna niets meer
const mine = sells();
const nw = S.newRearms(mine, 200, wk([240, 248, 187]));
check(nw.length === 1 && nw[0] === mine[2], 'newRearms vindt de heropende verkoop');
nw.forEach(s => { s.rearm = '24-10-2026'; });
check(S.newRearms(mine, 200, wk([240, 248, 187])).length === 0, 'newRearms daarna leeg');

// Samenvoegen: een vlag gaat nooit verloren, van welke kant hij ook komt
const id = 'Stocks|TEST';
const withFlag = { [id]: { cost: 140, sells: mine } }, without = { [id]: { cost: 140, sells: sells() } };
check(S.mergeState(without, withFlag)[id].sells[2].rearm === '24-10-2026', 'mergeState: lokale vlag blijft (GitHub leidend)');
check(S.mergeState(withFlag, without)[id].sells[2].rearm === '24-10-2026', 'mergeState: vlag van GitHub blijft');
check(S.keepRemoteExtras(without, withFlag)[id].sells[2].rearm === '24-10-2026', 'keepRemoteExtras: vlag van GitHub komt mee');
check(!('rearm' in S.mergeState(without, without)[id].sells[2]), 'geen vlag uit het niets');

// Kooptrap: vaste drempel 20% (geen beweeglijkheid in de koers) = treden op -20, -30 en -40% onder de top
const P20 = { thresh: 20 };
const at = (drop, extra) => ({ high52: 100, price: 100 - drop, ...extra });
let L = S.buyLadder(P20, at(15), []);
check(L.reached === 0 && !L.alert && L.levels.join() === '20,30,40' && Math.abs(L.toNext - 5) < 1e-9, `15% onder de top: nog geen trede (${JSON.stringify(L)})`);
L = S.buyLadder(P20, at(25), []);
check(L.alert && L.next === 1 && L.reached === 1, 'op -25%: trede 1 kopen');
L = S.buyLadder(P20, at(35), []);
check(L.alert && L.next === 1 && L.reached === 2, 'op -35% zonder aankopen: eerst trede 1');
const b1 = [{ level: 1, date: '20-10-2026', price: 79, top: 100 }];
L = S.buyLadder(P20, at(35), b1);
check(L.alert && L.next === 2 && L.done === 1, 'trede 1 gekocht, op -35%: trede 2');
L = S.buyLadder(P20, at(25), b1);
check(!L.alert && L.done === 1 && Math.abs(L.toNext - 5) < 1e-9, 'trede 1 gekocht, op -25%: wachten tot -30%');
L = S.buyLadder(P20, at(45), [{ level: 3, date: '1-11-2026', price: 59, top: 100 }]);
check(!L.alert && L.done === 3 && L.toNext === null, 'alle drie gekocht: geen signaal meer');
L = S.buyLadder(P20, { high52: 120, price: 90 }, b1);
check(L.alert && L.next === 1 && L.done === 0, 'nieuwe top (120 > 100): trap begint opnieuw');
L = S.buyLadder({ thresh: 30 }, at(50), []);
check(L.levels.join() === '30,45,60' && L.reached === 2, 'drempel 30%: treden -30, -45, -60');
check(S.buyLadder(P20, { high52: 0, price: 10 }, []) === null, 'geen top: geen trap');
// Valt nog: deze week de laagste weekslot van 5 weken én onder het 50-daags gemiddelde
const wkLows = lows => ({ from: '2026-10-05', low: lows });
check(S.stillFalling({ price: 70, sma50: 85, wk: wkLows([95, 90, 88, 84, 72]) }), 'nieuwe bodem en onder 50d: valt nog');
check(!S.stillFalling({ price: 78, sma50: 85, wk: wkLows([95, 90, 75, 84, 77]) }), 'geen nieuwe bodem deze week: valt niet meer');
check(!S.stillFalling({ price: 90, sma50: 85, wk: wkLows([95, 90, 88, 84, 72]) }), 'boven 50d: valt niet');
check(!S.stillFalling({ price: 70, sma50: 85, wk: wkLows([84, 72]) }), 'te weinig weken: geen oordeel');
check(S.buyLadder(P20, at(30, { sma50: 85, wk: wkLows([95, 90, 88, 84, 72]) }), []).falling, 'trap geeft valt nog door');
// Samenvoegen: gekochte treden van dit apparaat blijven als GitHub er (nog) geen heeft
check(S.mergeState({ [id]: { cost: 90 } }, { [id]: { cost: 90, buys: b1 } })[id].buys.length === 1, 'mergeState: lokale aankopen blijven');

// Risico: slechte maand (1 op 20) uit de jaarbeweeglijkheid, groep die samen beweegt, oververhit
check(Math.abs(S.badMonth(1.0) - 37.84) < 0.05 && Math.abs(S.badMonth(0.3) - 13.27) < 0.05, `slechte maand: 100% beweeglijk = -37,8%, 30% = -13,3% (${S.badMonth(1)}, ${S.badMonth(0.3)})`);
let seed = 7;
const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 - 0.5; };
const walk = (n, start) => { const out = [start]; for (let i = 1; i < n; i++) out.push(out[i - 1] * (1 + rnd() * 0.12)); return out; };
const same = walk(53, 50), other = walk(53, 100);
const wkq = (low, extra) => ({ price: low[low.length - 1], wk: { from: '2025-11-03', low }, ...extra });
const A = wkq(same, { vol: 100, sma200: same[52] / 1.6 }), B = wkq(same.map(v => v * 2), { vol: 100 }), Cq = wkq(other, { vol: 30, sma200: other[52] / 1.6 });
let RV = S.riskView([{ id: 'A', name: 'Alfa', q: A, qty: 1 }, { id: 'B', name: 'Beta', q: B, qty: 1 }, { id: 'C', name: 'Gamma', q: Cq, qty: 1 }]);
check(RV.equal && RV.badEur == null && RV.group && RV.group.n === 2 && RV.group.ids.sort().join() === 'A,B', `zonder aantallen: gelijke gewichten, groep Alfa+Beta (${JSON.stringify(RV.group)})`);
check(Math.abs(RV.group.share - 200 / 3) < 0.01 && RV.warn, 'groep 2 van 3 = 67%: boven de 40%');
check(Math.abs(RV.group.bad - S.badMonth(1)) < 0.01 && RV.bad < RV.group.bad, 'groep die samen beweegt zakt als één aandeel; het geheel minder');
check(RV.hot.length === 1 && RV.hot[0].id === 'A' && Math.round(RV.hot[0].ext) === 60, 'oververhit: alleen de beweeglijke positie 60% boven 200d');
RV = S.riskView([{ id: 'A', name: 'Alfa', q: A, qty: 10 }, { id: 'B', name: 'Beta', q: B, qty: 1 }, { id: 'C', name: 'Gamma', q: Cq, qty: 20 }]);
const vA = A.price * 10, vB = B.price, vC = Cq.price * 20;
check(!RV.equal && Math.abs(RV.total - (vA + vB + vC)) < 1e-6 && RV.badEur > 0, 'met aantallen: bedragen in euro');
check(Math.abs(RV.group.share - (vA + vB) / (vA + vB + vC) * 100) < 1e-6 && RV.warn === RV.group.share > 40, `groepsaandeel naar waarde (${RV.group.share.toFixed(1)}%)`);
check(S.riskView([]) === null, 'geen posities: geen risicobeeld');

// Samenvoegen met tijdstempel: per positie wint de nieuwste wijziging, van welke kant ook
const oldR = { [id]: { cost: 180, qty: 1, t: 1000 } }, newL = { [id]: { cost: 250, qty: 30, t: 2000 } };
check(S.mergeState(oldR, newL)[id].cost === 250 && S.mergeState(oldR, newL)[id].qty === 30, 'mergeState: net aangepast hier wint van een oudere GitHub-versie');
check(S.mergeState(newL, { [id]: { cost: 180, qty: 1, t: 1000 } })[id].cost === 250, 'mergeState: later op een ander apparaat aangepast wint');
check(S.mergeState({ [id]: { cost: 180 } }, { [id]: { cost: 250 } })[id].cost === 180, 'mergeState: zonder tijdstempels blijft GitHub leidend');
check(S.keepRemoteExtras(newL, oldR)[id].cost === 250, 'keepRemoteExtras: eigen nieuwere wijziging blijft');
check(S.keepRemoteExtras({ [id]: { cost: 180, t: 1000 } }, newL)[id].cost === 250, 'keepRemoteExtras: elders later aangepast komt mee');
const rvOld = { text: 'oud', updated: '2026-10-01' }, rvNew = { text: 'nieuw', updated: '2026-11-01' };
check(S.mergeState({ [id]: { cost: 180, t: 1000, review: rvNew } }, { [id]: { cost: 250, t: 2000, review: rvOld } })[id].review.text === 'nieuw',
  'nieuwste beoordeling blijft, ook als de andere kant de nieuwste prijs heeft');

// Posities op elk apparaat: wat via het dashboard is toegevoegd (tickers.txt, '# via dashboard') komt terug in de lijst
const fixed = [{ ticker: 'AMD.DE', section: 'Stocks', thresh: 20 }];
const dash = (list, exact) => ({ list, exact });
const SNDK = { ticker: 'SNDK', section: 'Stocks', label: 'Sandisk Corporation' }, ARMW = { ticker: 'ARM', section: 'Watchlist', label: 'Arm Holdings plc' };
let M = S.mergeDashboard(fixed, dash([SNDK, ARMW], false));
const sn = M.list.find(p => p.ticker === 'SNDK'), ar = M.list.find(p => p.ticker === 'ARM');
check(M.changed && M.list.length === 3 && sn && sn.section === 'Stocks' && sn.label === 'Sandisk Corporation' && sn.sync === 'txt' && !sn.watch,
  `ontbrekende Sandisk aangevuld (${JSON.stringify(M.list)})`);
check(ar && ar.watch && ar.thresh === 20, 'watchlist-regel wordt een watchlist-positie');
M = S.mergeDashboard(M.list, dash([SNDK, ARMW], false));
check(!M.changed && M.list.length === 3, 'tweede keer: niets te doen');
M = S.mergeDashboard(M.list, dash([ARMW], false));
check(!M.changed && M.list.some(p => p.ticker === 'SNDK'), 'zonder token (versie van de website): niets weghalen');
M = S.mergeDashboard(M.list, dash([ARMW], true));
check(M.changed && !M.list.some(p => p.ticker === 'SNDK') && M.list.some(p => p.ticker === 'AMD.DE'), 'met token: elders verwijderd = hier weg, vaste lijst blijft');
const ownAdd = [...fixed, { ticker: 'ZETA', section: 'Stocks', thresh: 20, added: 1 }];
M = S.mergeDashboard(ownAdd, dash([], true));
check(!M.changed && M.list.length === 2, 'net zelf toegevoegd (nog niet in tickers.txt gezien): blijft staan');
M = S.mergeDashboard(ownAdd, dash([{ ticker: 'ZETA', section: 'Stocks', label: null }], true));
check(M.changed && M.list.find(p => p.ticker === 'ZETA').sync === 'txt' && M.list.length === 2, 'eigen toevoeging gezien in tickers.txt: gemarkeerd, niet dubbel');
check(S.mergeDashboard(fixed, null).list === fixed, 'tickers.txt niet te lezen: lijst ongewijzigd');
// tickers.txt is de gedeelde lijst: wat er niet meer in staat is verwijderd en verdwijnt hier ook, behalve een eigen
// toevoeging die nog niet is weggeschreven. 'all' = alle tickers in tickers.txt, 'fixed' = de startlijst.
{
  const FX = new Set(['AMD.DE', 'UU2.DE', 'MSF.DE', 'TL0.DE', 'NVD.DE', 'ASML.AS']);
  const D = (tickers, dashList, exact) => ({ all: new Set(tickers), list: dashList || [], exact: !!exact });
  const P = t => ({ ticker: t, section: 'Stocks', thresh: 20 });
  const tick = r => r.list.map(p => p.ticker).join(',');
  // BlackRock (startlijst) staat niet meer in tickers.txt: weg met de echte versie
  let R = S.mergeDashboard([P('AMD.DE'), P('UU2.DE')], D(['AMD.DE'], [], true), { fixed: FX });
  check(R.changed && tick(R) === 'AMD.DE' && R.list[0].sync === 'txt', `positie uit de startlijst die niet meer in tickers.txt staat moet weg (${tick(R)})`);
  // zonder startlijst weten we het niet zeker: laten staan
  R = S.mergeDashboard([P('AMD.DE'), P('UU2.DE')], D(['AMD.DE'], [], true), {});
  check(tick(R) === 'AMD.DE,UU2.DE', 'zonder startlijst: een nooit geziene positie blijft staan');
  // eerder hier in tickers.txt gezien en nu weg: verwijderd, ook zonder startlijst
  R = S.mergeDashboard([P('AMD.DE'), { ...P('ORCL'), sync: 'txt' }], D(['AMD.DE'], [], true), {});
  check(tick(R) === 'AMD.DE', 'eerder gezien in tickers.txt en nu weg: verwijderd');
  // net zelf toegevoegd en nog niet gezien: blijft staan, ook als de ticker in de startlijst zit
  R = S.mergeDashboard([P('AMD.DE'), { ...P('MSF.DE'), added: 1 }, { ...P('IBM'), added: 1 }], D(['AMD.DE'], [], true), { fixed: FX });
  check(tick(R) === 'AMD.DE,MSF.DE,IBM', `net toegevoegd en nog niet in tickers.txt: blijft staan (${tick(R)})`);
  // eigen toevoeging van vroeger die nooit is weggeschreven (geen startlijst, nooit gezien): blijft staan
  R = S.mergeDashboard([P('AMD.DE'), P('XYZ')], D(['AMD.DE'], [], true), { fixed: FX });
  check(tick(R) === 'AMD.DE,XYZ', 'eigen toevoeging die nooit in tickers.txt stond: blijft staan');
  // versie van de website, zonder token: ook weg; met token alleen op de echte versie afgaan
  R = S.mergeDashboard([P('AMD.DE'), P('NVD.DE'), P('UU2.DE')], D(['AMD.DE', 'NVD.DE'], [], false), { fixed: FX });
  check(tick(R) === 'AMD.DE,NVD.DE', 'zonder token gaat de versie van de website door');
  store.gh_token = 'github_pat_x';
  R = S.mergeDashboard([P('AMD.DE'), P('NVD.DE'), P('UU2.DE')], D(['AMD.DE', 'NVD.DE'], [], false), { fixed: FX });
  check(tick(R) === 'AMD.DE,NVD.DE,UU2.DE', 'met token niets weghalen op de versie van de website');
  delete store.gh_token;
  // zou meer dan de helft (en meer dan 3) verdwijnen op de versie van de website: niets weghalen, volgorde blijft
  const veel = ['AMD.DE', 'UU2.DE', 'MSF.DE', 'TL0.DE', 'NVD.DE', 'ASML.AS'].map(P);
  R = S.mergeDashboard(veel, D(['AMD.DE'], [], false), { fixed: FX });
  check(tick(R) === 'AMD.DE,UU2.DE,MSF.DE,TL0.DE,NVD.DE,ASML.AS', `te veel tegelijk weg op de versie van de website: niets doen (${tick(R)})`);
  R = S.mergeDashboard(veel, D(['AMD.DE'], [], true), { fixed: FX });
  check(tick(R) === 'AMD.DE', 'de echte versie telt altijd');
  // lege lijst: nooit iets weghalen; elders toegevoegd komt erbij
  R = S.mergeDashboard(veel, D([], [], true), { fixed: FX });
  check(R.list.length === 6, 'lege tickers.txt: niets weghalen');
  R = S.mergeDashboard([P('AMD.DE')], D(['AMD.DE', 'SNDK'], [{ ticker: 'SNDK', section: 'Stocks', label: 'Sandisk Corporation' }], false), { fixed: FX });
  check(tick(R) === 'AMD.DE,SNDK' && R.list[1].sync === 'txt', 'elders via het dashboard toegevoegd: komt erbij');
  // een regel uit de startlijst die hier ontbreekt komt niet vanzelf terug (die is hier verwijderd)
  R = S.mergeDashboard([P('AMD.DE')], D(['AMD.DE', 'TL0.DE'], [], true), { fixed: FX });
  check(tick(R) === 'AMD.DE', 'een hier verwijderde positie uit de startlijst komt niet vanzelf terug');
  check(S.needsFixed([P('AMD.DE'), P('UU2.DE')], D(['AMD.DE'])) && !S.needsFixed([P('AMD.DE')], D(['AMD.DE']))
    && !S.needsFixed([P('AMD.DE'), { ...P('IBM'), added: 1 }], D(['AMD.DE'])), 'de startlijst alleen ophalen als er een onbesliste positie is');
}

// Stresstest: per positie de gevoeligheid (beta) voor de Nasdaq-100 en chips, samengesteld gerekend en gewogen naar waarde
{
  const near = (a, b, e = 0.01) => Math.abs(a - b) < e;
  check(near(S.shockPct(1, -20), -20) && near(S.shockPct(2, -20), -36) && near(S.shockPct(0, -30), 0) && S.shockPct(5, -30) > -100,
    'schok samengesteld: beta 1 = de index, beta 2 bij −20% = −36%, nooit meer dan alles');
  const q = (price) => ({ price });
  // 100 x €10 met beta 2, 50 x €20 met beta 1, één positie zonder meting (€1000)
  const held = [{ id: 'a', name: 'A', q: q(10), qty: 100, st: { ndx: 2, sox: 1, corr: -30 } },
    { id: 'b', name: 'B', q: q(20), qty: 50, st: { ndx: 1, corr: -10 } }, { id: 'c', name: 'C', q: q(10), qty: 100 }];
  const T = S.stressTest(held, { from: '2026-10-06', to: '2026-11-05', pct: -14.7 });
  const n20 = T.scen.find(x => x.f === 'ndx' && x.pct === -20), sx = T.scen.find(x => x.f === 'sox');
  check(T.scen.length === 4 && near(n20.loss, -28) && near(n20.eur, -840) && near(n20.cover, 66.67) && n20.n === 2,
    `Nasdaq −20%: (−36% + −20%) / 2 = −28% over de gemeten posities, toegepast op het geheel (${JSON.stringify(n20 && [n20.loss, n20.eur, n20.cover])})`);
  check(n20.pos[0].id === 'a' && near(n20.pos[0].pct, -36) && near(n20.pos[0].eur, -360) && near(n20.pos[0].part, -12),
    'grootste bijdrage voorop: positie, verlies in procent, euro en procentpunten van het geheel');
  check(sx && sx.n === 1 && near(sx.loss, -30) && near(sx.cover, 33.33), 'chips −30%: alleen posities met een chipmeting tellen, de rest als gemiddelde');
  check(T.last && near(T.last.loss, -20) && T.last.from === '2026-10-06' && near(T.last.pct, -14.7), `herhaling van de laatste correctie: gemiddelde van −30% en −10% (${JSON.stringify(T.last && T.last.loss)})`);
  const E = S.stressTest(held.map(x => ({ ...x, qty: 1 })), null);
  check(E.equal && E.total === null && E.scen[1].eur === null && near(E.scen[1].loss, -28) && E.last === null, 'zonder aantallen: elke positie even zwaar, geen bedragen; zonder correctie geen herhaling');
  check(S.stressTest([], null) === null && S.stressTest([{ id: 'x', name: 'X', q: q(5), qty: 1 }], null) === null, 'zonder posities of zonder metingen: geen stresstest');
  const fx = S.stressTest([{ id: 'u', name: 'U', q: q(10), qty: 10, rate: 0.5, st: { ndx: 1 } }, { id: 'e', name: 'E', q: q(10), qty: 10, st: { ndx: 3 } }], null);
  check(near(fx.scen[0].loss, (50 * -10 + 100 * S.shockPct(3, -10)) / 150), 'dollarpositie telt mee in euro (rate)');
}

(async () => {
  // tickers.txt lezen (zonder token: de versie van de website)
  ctx.fetch = async u => ({ ok: u === 'tickers.txt', text: async () => '# kop\nAMD.DE\nSNDK, Sandisk Corporation, Stocks  # via dashboard\nLLY.DE, Eli Lilly and Company, Watchlist  # via dashboard\nbad ticker!, X, Stocks # via dashboard\n' });
  const parsed = await S.dashboardTickers();
  check(parsed && parsed.all && typeof parsed.all.has === 'function' && [...parsed.all].join(',') === 'AMD.DE,SNDK,LLY.DE,BAD', `alle tickers uit tickers.txt zoals de scripts ze lezen: het eerste woord van elke regel (${parsed && [...parsed.all]})`);
  check(parsed && !parsed.exact && parsed.list.map(x => `${x.section}|${x.ticker}|${x.label}`).join(';') === 'Stocks|SNDK|Sandisk Corporation;Watchlist|LLY.DE|Eli Lilly and Company',
    `tickers.txt gelezen: alleen geldige regels via het dashboard (${JSON.stringify(parsed)})`);

  ctx.fetch = async () => ({ ok: true, text: async () => '<!DOCTYPE html><html><body>AMD.DE Service Unavailable</body></html>' });
  check(await S.dashboardTickers() === null, 'een foutpagina in plaats van tickers.txt is geen lijst');

  console.log(fails.length ? `REGELS: ${fails.length} fout(en)` : 'REGELS: alles in orde');
  process.exit(fails.length ? 1 : 0);
})();
