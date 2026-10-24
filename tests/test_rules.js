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

console.log(fails.length ? `REGELS: ${fails.length} fout(en)` : 'REGELS: alles in orde');
process.exit(fails.length ? 1 : 0);
