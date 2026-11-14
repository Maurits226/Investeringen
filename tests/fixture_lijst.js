// Een vaste positielijst voor de browsertests, in de vorm van tickers.txt, en koersen voor de posities waar een test mee werkt.
// De echte tickers.txt verandert telkens als de beheerder op het dashboard een positie toevoegt of weghaalt, en daarmee
// verdwijnt ook de koers van die positie uit data.json. Een test die op die lijst of die koers leunt, faalt dan zonder dat
// er iets mis is (gebeurd toen Tesla werd weggehaald). Daarom krijgen de tests die een bepaalde positie nodig hebben:
//   lijst        de kop, de startlijst uit portfolio.html zonder BlackRock (kale regels) en één positie via het dashboard
//   metKoersen   data.json zoals die is, aangevuld met een testkoers voor de genoemde tickers als die er niet (meer) in staan
const fs = require('fs');
const path = require('path');

const testKoers = t => ({ ticker: t, price: 100, prevClose: 99, currency: 'EUR', name: 'Testbedrijf ' + t, high52: 120, low52: 60, vol: 30,
  sma50: 98, sma200: 90, wk: { from: '2025-11-10', low: Array.from({ length: 53 }, (_, i) => Math.round((60 + i * 0.7) * 100) / 100) },
  exchange: 'GER', exchangeFull: 'XETRA' });

module.exports = root => {
  const start = [...fs.readFileSync(path.join(root, 'portfolio.html'), 'utf8').matchAll(/\{ ticker: '([^']+)',\s*section: (?:'([^']*)'|"([^"]*)")[^{}]*\}/g)]
    .map(m => ({ ticker: m[1], section: m[2] || m[3], watch: m[2] === 'Watchlist', thresh: 20, cur: 'native' }));
  const via = 'SNDK, Sandisk Corporation, Stocks  # via dashboard';
  const lijst = ['# Eén ticker per regel. Regels met # worden genegeerd.',
    '# Xetra/Gettex (euro) = .DE  |  Amsterdam = .AS  |  NYSE/NASDAQ (dollar) = geen achtervoegsel',
    ...start.filter(p => p.ticker !== 'UU2.DE').map(p => p.ticker), via, ''].join('\n');
  // data.json als tekst, met een koers voor elk van deze tickers
  const metKoersen = tickers => {
    const d = JSON.parse(fs.readFileSync(path.join(root, 'data.json'), 'utf8'));
    d.quotes = d.quotes || {};
    tickers.forEach(t => { if (!d.quotes[t]) d.quotes[t] = testKoers(t); });
    return JSON.stringify(d);
  };
  // De echte lijst van de beheerder (tickers.txt) als posities; de rubriek staat in de regel zelf of komt uit de startlijst
  const echt = () => fs.readFileSync(path.join(root, 'tickers.txt'), 'utf8').split('\n').map(l => l.split('#')[0].trim()).filter(Boolean).map(l => {
    const [t, , sec] = l.split(',').map(x => x.trim());
    const ticker = t.split(/\s/)[0].toUpperCase(), d = start.find(p => p.ticker.toUpperCase() === ticker);
    const section = sec || (d && d.section) || 'Stocks';
    return { ticker, section, watch: section === 'Watchlist' };
  });
  return { start, via, lijst, metKoersen, echt };
};
