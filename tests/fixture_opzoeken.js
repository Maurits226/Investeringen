// Testdata voor de tab Opzoeken. Zolang je echte opzoeklijst leeg is, krijgen de paginatests (test_pages.js, test_xss.js)
// een lijst met drie beoordeelde aandelen (eigen posities uit screener.json), één in een andere valuta, één onbekende ticker
// en één die nog beoordeeld wordt, zodat ze ook de kaarten, de details en de wachtstand zien. Echte lijst: null (die gebruiken).
const fs = require('fs');
const path = require('path');

module.exports = function lookupFixture(root) {
  const read = f => { try { return JSON.parse(fs.readFileSync(path.join(root, f), 'utf8')); } catch (e) { return null; } };
  const lijst = read('opzoeklijst.json');
  if (lijst && Array.isArray(lijst.items) && lijst.items.length) return null;
  const scr = read('screener.json') || {};
  const refs = (scr.refs || []).filter(r => r && r.card && r.s12).slice(0, 3);
  const at = h => new Date(Date.now() - h * 3600e3).toISOString().slice(0, 19) + 'Z';
  const items = refs.map((r, i) => ({ sym: r.sym, alt: r.eu ? [r.eu] : [], name: r.name, at: at(i + 1) }))
    .concat([{ sym: 'TSM', alt: [], name: 'Taiwan Semiconductor Manufacturing', at: at(30) },
             { sym: 'ZZZZ.AS', alt: [], name: null, at: at(40) }]);
  const waiting = (scr.items || [])[0];                          // net toegevoegd: wacht op de run (met voorproefje uit de scan)
  if (waiting) items.unshift({ sym: waiting.sym, alt: waiting.eu ? [waiting.eu] : [], name: waiting.name, at: at(0) });
  const out = { updated: at(0.5), days: 30, n: (scr.items || []).length + refs.length, items: {} };
  refs.forEach((r, i) => { out.items[r.sym] = { at: at(i + 1), status: 'ok', it: r }; });
  out.items.TSM = { at: at(30), status: 'skip', why: 'rapporteert in TWD', name: 'Taiwan Semiconductor Manufacturing' };
  out.items['ZZZZ.AS'] = { at: at(40), status: 'fout', why: 'Yahoo kent deze ticker niet', name: null };
  return { 'opzoeklijst.json': { v: 1, days: 30, items }, 'opzoeken.json': out };
};
