// Gedeelde weergave van de scorecard en de 12 stappen ('How to Find the Best Stocks', Tom Nash),
// zodat Top 250, Koopmoment en Beoordeling precies hetzelfde laten zien. De cijfers komen uit
// screener.json (scripts/screener.py: card en s12). Elke pagina gebruikt Steps.css() eenmalig.
const Steps = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nl = v => String(v ?? '').replace(/(\d)\.(\d)/g, '$1,$2');   // 24.4 → 24,4 (getallen uit screener.py)
  const nf = (v, d = 1) => v == null || !isFinite(v) ? '—' : Number(v).toLocaleString('nl-NL', { minimumFractionDigits: d, maximumFractionDigits: d });
  const sc = v => nf(v, Number.isInteger(+v) ? 0 : 1);   // scorecard: 115, of 113,6 uit een oudere screener.json

  const NAMES = ['', 'De seculiere trend', 'Sanity check', 'Pre-screen', 'Definieer het bedrijf', 'Marktpotentieel',
    '125-punten scorecard', 'Omzetmotor en 5 jaar', 'Loan Officer Test', 'Concurrentievoordeel', 'Management',
    'Vernietig iets moois', 'Waardering'];
  const SUB = ['', 'Waar wordt een nieuw spel gespeeld?', 'De No B***t Rule: 2 van 3', 'Hier sterven de meeste bedrijven',
    'Bewijs dat je het begrijpt', 'Hoe groot is de prijs?', '13 categorieën, 0/5/10', 'Vier regels uit het jaarverslag',
    'Lees de balans als een bankier', 'Benoem de moat en test hem', 'Onder de indruk van de CEO, of eruit',
    'Probeer de these te slopen', 'Betaal niet te veel'];
  // Wat je per stap zelf beoordeelt (vast; screener.json levert alleen de extra punten per bedrijf).
  // Afvinken gaat op volgorde: eerst deze, dan de extra's. Voeg nieuwe punten daarom achteraan toe.
  const MANUAL = {
    1: ['Wind mee van regelgeving of overheid', 'Adoptie die je in de echte wereld ziet',
        'Het verhaal van de markt vs. wat het bedrijf wordt (geen gat = geen edge)'],
    3: ['Geen grote juridische of regelgevingskwestie'],
    4: ['[Bedrijf] levert X dat Y helpt Z te bereiken, en rekent [verdienmodel]', 'Welke pijn lossen we op?',
        'Wie heeft die pijn, en waarom?', 'Waarom kiest de klant ons en niet de concurrent?'],
    5: ['Verkoopt aan een brede markt, geen niche', 'Marktomvang zelf gecheckt', 'Markt groot genoeg voor de groei waarop je inzet'],
    7: ['Software: netto dollarretentie 120%+', "Lees bedrijfsresultaat, niet nettowinst (en mijd 'adjusted EBITDA')"],
    9: ['Welke van de zes: netwerkeffect, technologie/patenten, overstapkosten, schaal, regelgeving, merk',
        'Wordt de moat sterker of zwakker?', 'Kopieert een concurrent met 20 engineers dit binnen een jaar?'],
    10: ['10+ jaar in de rol, liefst oprichter', 'Gedisciplineerde kapitaalallocatie', 'Eerlijke antwoorden, ook als het tegenzit',
         'Een leider voor wie goede mensen willen werken', 'Resultaten, geen excuses'],
    11: ['Schrijf 3-5 redenen op waarom je these fout is', 'Lees de bearish analyse (Seeking Alpha)',
         'Fraude of boekhoudschandaal, zelfs een vermoeden? Accountantswissel?', 'Kun je met de bear case leven?'],
    12: ['Zelfs de bear case moet aantrekkelijk zijn']
  };
  const STATUS = { pass: ['ok', 'gehaald'], fail: ['no', 'valt af'], check: ['chk', 'twijfel'], manual: ['man', 'zelf beoordelen'] };

  // Scorecard volgens de gids (stap 6): 0 · 5 · 10 punten
  const CARD = {
    cashdebt: ['Cash vs. schuld', 'meer schuld dan cash 0 · meer cash 5 · 2x+ cash 10'],
    omzet: ['Omzetgroei', 'onder 10% 0 · 10-20% 5 · boven 20% 10'],
    marge: ['Operationele marge', 'onder 10% 0 · 10-25% 5 · boven 25% 10'],
    short: ['Short interest', 'boven 10% 0 · 5-10% 5 · onder 5% 10'],
    inst: ['Institutioneel bezit', 'onder 30% 0 · 30-60% 5 · boven 60% 10'],
    schaal: ['Omzet groeit harder dan kosten', 'nee 0 · ja 10'],
    sp5: ['5 jaar t.o.v. S&P 500', 'slechter 0 · 10-20 pp beter 5 · 20+ pp beter 10'],
    moat: ['Moat*', 'geen 0 · zwak 5 · sterk 10'],
    ceo: ['CEO*', 'clown 0 · redelijk 5 · superster 10'],
    recessie: ['Recessiebestendig, prijsmacht', 'geen 0 · enigszins 5 · sterk 10'],
    trend: ['Seculiere trend*', 'verstoord 0 · deels 5 · precies op de trend 10'],
    cultuur: ['Cultuur en missie*', 'geen 0 · enigszins 5 · sterk 10'],
    talent: ['Talent*', 'laag 0 · gemiddeld 5 · hoog 10']
  };
  const LEVEL = { top: ['Uitzonderlijk', 'lvl-top'], watch: ['Watchlist · investeerbaar', 'lvl-watch'], low: ['Absoluut niet', 'lvl-low'] };
  // Sectornamen van Yahoo in het Nederlands (één lijst voor alle pagina's)
  const SECTOR_NL = { Technology: 'Technologie', Healthcare: 'Gezondheidszorg', 'Financial Services': 'Financieel',
    'Consumer Cyclical': 'Cyclische consumptie', 'Consumer Defensive': 'Basisconsumptie', Industrials: 'Industrie',
    'Communication Services': 'Communicatie', Energy: 'Energie', Utilities: 'Nutsbedrijven', 'Real Estate': 'Vastgoed', 'Basic Materials': 'Grondstoffen' };

  function cardValue(c) {
    if (c.nodata) return '<small>geen data · neutraal</small>';
    const v = c.v;
    switch (c.k) {
      case 'cashdebt': return v == null ? 'geen schuld' : nf(v, 1) + '× schuld';
      case 'short': case 'inst': return v == null ? '—' : nf(v, c.k === 'short' ? 1 : 0) + '%';
      case 'schaal': return v == null ? '—' : (v > 0 ? '+' : '') + nf(v, 1) + ' pp';
      case 'sp5': return v == null ? '—' : (v > 0 ? '+' : '') + nf(v, 0) + ' pp' + (c.r != null ? ` <small>${c.r > 0 ? '+' : ''}${nf(c.r, 0)}% vs ${c.sp > 0 ? '+' : ''}${nf(c.sp, 0)}%</small>` : '');
      case 'moat': case 'ceo': case 'recessie': case 'trend': case 'cultuur': case 'talent':
        return `<small>${c.src === 'oordeel' ? 'oordeel' : 'schatting'}${c.raw != null ? ' ' + c.raw + '/10' : ''}</small>`;
      default: return v == null ? '—' : (v > 0 ? '+' : '') + nf(v, 1) + '%';
    }
  }

  // Scorecard-ring zoals in Kansen portefeuille: boog naar verhouding van 125, groen 100+, oranje 75-100, rood eronder.
  // size in px (58 = groot zoals de Top 250, 48-52 = kaart, 34 = in een lijst); het getal staat in het midden.
  // Op een lichtere ondergrond (--panel-2) zet de pagina --ring-track: var(--line), anders valt de lege boog weg.
  const ringColor = s => s >= 100 ? 'var(--up, #3ecf8e)' : s >= 75 ? 'var(--warn, #f5b544)' : 'var(--down, #f0595f)';
  function ring(score, size = 58) {
    if (score == null || !isFinite(score)) return `<span class="s12-ring na" style="--rs:${+size}px"><b>—</b></span>`;
    const r = 24, c = 2 * Math.PI * r, f = Math.max(0, Math.min(125, +score)) / 125;
    return `<span class="s12-ring" style="--rs:${+size}px" role="img" aria-label="Scorecard ${Math.round(score)} van 125"><svg viewBox="0 0 58 58" aria-hidden="true">
        <circle class="trk" cx="29" cy="29" r="${r}" fill="none" stroke-width="5"/>
        <circle cx="29" cy="29" r="${r}" fill="none" stroke="${ringColor(+score)}" stroke-width="5" stroke-linecap="round" stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}"/>
      </svg><b>${Math.round(score)}</b></span>`;
  }

  function cardTable(card) {
    if (!card || card.s == null) return `<div class="s12-note">Te weinig cijfers voor de scorecard (${card ? card.n : 0}/13).</div>`;
    const L = LEVEL[card.lvl] || LEVEL.low;
    // De schaal (s12-cs) staat los van de naam, zodat hij op een telefoon de hele breedte kan gebruiken
    const rows = card.crit.map(c => `<div class="s12-crow ${c.nodata ? 'na' : ''}">
        <div class="s12-cn">${esc((CARD[c.k] || [c.k])[0])}</div>
        <div class="s12-cv">${cardValue(c)}</div>
        <div class="s12-pts p${c.p}">${c.p}</div>
        <small class="s12-cs">${esc((CARD[c.k] || ['', ''])[1])}</small></div>`).join('');
    return `<div class="s12-chead">${ring(card.s, 48)}<span class="s12-cnum"><small>van 125</small></span><span class="s12-lvl ${L[1]}">${L[0]}</span></div>
      <div class="s12-bar"><i style="left:${Math.min(100, card.s / 1.25).toFixed(1)}%"></i></div>
      ${rows}
      <div class="s12-note">* Subjectief: oordeel uit kwaliteit.json of een strenge schatting uit de cijfers. 100-125 uitzonderlijk · 75-100 watchlist · onder 75 absoluut niet.
        ${card.nodata && card.nodata.length ? `Geen data (telt neutraal): ${card.nodata.map(k => esc((CARD[k] || [k])[0])).join(', ')}.` : ''}</div>`;
  }

  // 12 vakjes: groen gehaald, rood valt af, oranje twijfel, grijs zelf beoordelen
  function dots(s12) {
    if (!s12) return '';
    return `<span class="s12-dots" role="img" aria-label="${esc(failText(s12))}">${s12.list.map(s => `<i class="${(STATUS[s.s] || STATUS.manual)[0]}" title="${+s.n}. ${esc(NAMES[s.n])}: ${(STATUS[s.s] || STATUS.manual)[1]}${s.note ? ' · ' + esc(s.note) : ''}"></i>`).join('')}</span>`;
  }
  function failText(s12) {
    if (!s12) return 'nog niet beoordeeld';
    if (s12.survivor) return 'overleeft alle stappen';
    if (s12.fail === 12 && s12.nfail === 1) return 'overleeft stap 1-11 · alleen (nog) te duur (stap 12)';
    const f = s12.list.find(s => s.n === s12.fail);
    return `valt af bij stap ${+s12.fail}: ${(NAMES[s12.fail] || '').toLowerCase()}${f && f.note ? ' · ' + f.note.replace(/^[^:]+:\s*/, '') : ''}`;
  }

  // Oordeel voor een positie die je al hebt ('Once a stock survives'): de 12 stappen zijn gemaakt om te KOPEN.
  // Voor wat je al hebt telt of de these breekt: een zwakke balans (stap 8), meerdere rode vlaggen tegelijk of de
  // hype-val (stap 11), of een scorecard onder 75. Eén rode vlag of afvallen bij de strenge koopfilters (stap 2, 3, 7,
  // 9, 10) = kritisch bekijken: de automatische vlaggen zijn grove signalen, geen bewijs. Prijs is nooit een reden.
  const HOLD_HARD = [8];
  const nFlags = x => x && x.note ? x.note.replace(/^[^:]+:\s*/, '').split(', ').length : 0;
  const hardFail = x => HOLD_HARD.includes(x.n) || (x.n === 11 && (nFlags(x) >= 2 || /hype-val/.test(x.note || '')));
  const failNames = ns => ns.map(n => `${n} (${(NAMES[n] || '').toLowerCase()})`).join(', ');
  function verdict(it) {
    const s = it && it.s12, c = it && it.card;
    if (!s || !c || c.s == null) return { cls: 'v-na', label: 'Nog niet beoordeeld', text: 'Er zijn nog geen cijfers voor dit aandeel.' };
    const fails = s.list.filter(x => x.s === 'fail' && x.n !== 12);
    const hard = fails.filter(hardFail).map(x => x.n);
    const soft = fails.filter(x => !hardFail(x)).map(x => x.n);
    const note = n => { const x = s.list.find(y => y.n === n); return x && x.note ? ` (${x.note.replace(/^[^:]+:\s*/, '')})` : ''; };
    if (c.s < 75 || hard.length) return { cls: 'v-bad', label: 'These herzien',
      text: `${c.s < 75 ? `Scorecard ${sc(c.s)}: onder 75, volgens de gids 'absoluut niet'. ` : ''}${hard.length ? `Valt af bij stap ${failNames(hard)}${note(hard[0])}. ` : ''}Breekt de these langzaam, bouw dan geleidelijk af (de gids: over ±18 maanden). Bij fraude: direct eruit. Een koersdaling alleen is nooit een reden.` };
    if (soft.length) return { cls: 'v-mid', label: 'Kritisch bekijken',
      text: `Als nieuwe aankoop zou het afvallen bij stap ${failNames(soft)}${note(soft[0])}. Dat breekt je these niet vanzelf: lees de kwartaalcijfers en kijk of er iets wezenlijks is veranderd.${c.s < 100 ? ` Scorecard ${sc(c.s)}: watchlist-niveau.` : ''}` };
    const val = s.list.find(x => x.n === 12);
    return { cls: 'v-good', label: 'Behouden',
      text: `Scorecard ${sc(c.s)}${c.s >= 100 ? ' (uitzonderlijk)' : ' (watchlist-niveau)'} en geen afvaller in de bedrijfsstappen.${val && val.s === 'fail' ? ' Wel duur: bijkopen pas bij een betere prijs, maar dat is geen reden om te verkopen.' : ''} Check elk kwartaal, herhaal de scorecard jaarlijks.` };
  }

  // Oordeel als nieuwe aankoop (Survivors, Opzoeken): de 12 stappen zijn een koopfilter, elke afvaller telt.
  function buyVerdict(it) {
    const s = it && it.s12, c = it && it.card;
    if (!s || !c || c.s == null) return { cls: 'v-na', label: 'Niet te beoordelen', text: 'Te weinig cijfers voor de scorecard en de 12 stappen.' };
    const val = s.list.find(x => x.n === 12) || {};
    if (s.survivor && val.s === 'pass') return { cls: 'v-good', label: 'Survivor · koopwaardig', text: 'Overleeft alle 12 stappen en zelfs de bear-case DCF ligt boven de koers. Volgens de gids: koop langzaam (vast bedrag, vaste data) en schrijf je exit op vóór je koopt.' };
    if (s.survivor) return { cls: 'v-good', label: 'Survivor', text: 'Overleeft alle 12 stappen; de waardering is redelijk maar niet ruim (bear-case DCF onder de koers). Koop langzaam en schrijf je exit vooraf op.' };
    if (s.nfail === 1 && s.fail === 12) return { cls: 'v-mid', label: 'Bijna · te duur', text: 'Overleeft stap 1-11, maar de koers ligt boven de basis-DCF. Wacht op een betere prijs (zie Koopmoment); de kwaliteit is er al.' };
    if (!s.nfail) return { cls: 'v-mid', label: 'Geen afvaller · zelf beoordelen', text: 'Geen stap valt automatisch af, maar bij banken, brokers en verzekeraars zijn de balans (stap 8) en de waardering (stap 12) niet uit de cijfers te halen: beoordeel die zelf.' };
    const f = s.list.find(x => x.n === s.fail), why = f && f.note ? `: ${f.note.replace(/^[^:]+:\s*/, '')}` : '';
    const where = `stap ${failNames([s.fail])}${why}${s.nfail > 1 ? `; in totaal ${s.nfail} afvallers` : ''}`;
    return c.s < 75
      ? { cls: 'v-bad', label: 'Absoluut niet', text: `Scorecard ${sc(c.s)}: onder 75, volgens de gids 'absoluut niet'. Eerste afvaller: ${where}.` }
      : { cls: 'v-bad', label: `Valt af bij stap ${+s.fail}`, text: `Eerste afvaller: ${where}. Volgens de gids: zakt een bedrijf voor één stap, dan valt het af, hoe mooi het verhaal ook klinkt.` };
  }

  // Volledige 12 stappen. opts.ticks = {n: [index, ...]} en opts.tickable = true geven afvinkbare punten
  function panel(s12, opts = {}) {
    if (!s12) return '<div class="s12-note">De 12 stappen worden berekend bij de volgende run van de Top 250.</div>';
    const ticks = opts.ticks || {};
    const mark = ok => ok === true ? '<b class="ok">✓</b>' : ok === false ? '<b class="no">✕</b>' : '<b class="na">?</b>';
    return s12.list.map(s => {
      const st = STATUS[s.s] || STATUS.manual;
      const checks = (s.c || []).map(([t, ok, v]) => `<li>${mark(ok)}<span>${esc(t)}</span>${v != null ? `<em>${esc(nl(v))}</em>` : ''}</li>`).join('');
      const man = (MANUAL[s.n] || []).concat(s.m || []).map((t, i) => {
        const on = (ticks[s.n] || []).includes(i);
        return opts.tickable
          ? `<li class="man"><label><input type="checkbox" data-step="${+s.n}" data-i="${i}"${on ? ' checked' : ''}><span>${esc(t)}</span></label></li>`
          : `<li class="man"><b class="na">○</b><span>${esc(t)}</span></li>`;
      }).join('');
      let extra = '';
      if (s.n === 4 && s.about) extra = `<div class="s12-about">${esc(s.about)}…</div>`;
      if (s.n === 9 && s.types) extra = `<div class="s12-note">Moat-types: ${esc(s.types)}</div>`;
      if (s.n === 12 && s.dcf) {
        const p = s.price, d = s.dcf;
        const cell = (k, lbl) => `<div class="s12-dcf ${d[k] != null && p ? (d[k] >= p ? 'up' : 'down') : ''}"><small>${lbl}</small><b>${d[k] == null ? '—' : nf(d[k], 2)}</b></div>`;
        extra = `<div class="s12-dcfrow">${cell('bull', 'bull 8%')}${cell('base', 'basis 10%')}${cell('bear', 'bear 12%')}<div class="s12-dcf"><small>koers</small><b>${nf(p, 2)}</b></div></div>
          <div class="s12-note">DCF: omzetgroei ${s.g == null ? '—' : nf(s.g, 1) + '%'} per jaar (3-jaarsgroei ${s.actual == null ? '—' : nf(s.actual, 1) + '%'}, ingeperkt), vrije-kasstroommarge ${s.margin == null ? '—' : nf(s.margin, 1) + '%'}, eindgroei 4%.
            Omgekeerde DCF: de koers rekent op <b>${s.implied == null ? '—' : nf(s.implied, 1) + '%'}</b> groei per jaar${s.actual != null && s.implied != null ? (s.implied < s.actual ? ' — minder dan het bedrijf nu groeit: daar zit de kans.' : ' — meer dan het bedrijf nu groeit: geen marge.') : '.'}
            ${s.mult ? `Juiste multiple: ${esc(s.mult[0])} ${esc(nl(s.mult[1]))}${s.mult[2] ? ' (' + esc(nl(s.mult[2])) + ')' : ''}.` : ''}</div>`;
      }
      return `<details class="s12-step ${st[0]}"${opts.open || s.s === 'fail' ? ' open' : ''}>
        <summary><span class="s12-n">${String(s.n).padStart(2, '0')}</span><span class="s12-t">${esc(NAMES[s.n])}<small>${esc(SUB[s.n])}</small></span><span class="s12-st ${st[0]}">${st[1]}</span></summary>
        ${s.note ? `<div class="s12-flag">${esc(s.note)}</div>` : ''}
        ${checks || man ? `<ul class="s12-list">${checks}${man}</ul>` : ''}${extra}</details>`;
    }).join('');
  }

  let cssDone = false;
  function css() {
    if (cssDone) return;
    cssDone = true;
    const st = document.createElement('style');
    st.textContent = `
      .s12-dots { display: inline-flex; gap: 3px; flex-wrap: wrap; }
      .s12-dots i { width: 11px; height: 11px; border-radius: 3px; background: #3a3e43; display: inline-block; }
      .s12-dots i.ok { background: var(--up, #3ecf8e); } .s12-dots i.no { background: var(--down, #f0595f); }
      .s12-dots i.chk { background: var(--warn, #f5b544); } .s12-dots i.man { background: transparent; box-shadow: inset 0 0 0 1px #5a5f64; }
      .s12-chead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
      .s12-ring { position: relative; display: inline-block; width: var(--rs, 58px); height: var(--rs, 58px); flex-shrink: 0; vertical-align: middle; }
      .s12-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); display: block; }
      .s12-ring .trk { stroke: var(--ring-track, var(--panel-2, #202224)); }
      .s12-ring b { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace);
        font-weight: 600; font-size: max(11.5px, calc(var(--rs, 58px) * .28)); line-height: 1; color: var(--text, #eceef0); }
      .s12-ring.na { border-radius: 50%; box-shadow: inset 0 0 0 calc(var(--rs, 58px) * .086) var(--ring-track, var(--panel-2, #202224)); }
      .s12-ring.na b { color: var(--muted, #83888d); }
      .s12-cnum { font-family: var(--mono, monospace); font-size: 24px; font-weight: 600; }
      .s12-cnum small { font-size: 12px; color: var(--muted, #83888d); font-weight: 400; }
      .s12-lvl { font-size: 12px; font-weight: 600; } .lvl-top { color: var(--up, #3ecf8e); } .lvl-watch { color: var(--warn, #f5b544); } .lvl-low { color: var(--down, #f0595f); }
      .s12-bar { position: relative; height: 8px; border-radius: 5px; margin-bottom: 10px;
        background: linear-gradient(90deg, rgba(240,89,95,.35) 0 60%, rgba(245,181,68,.35) 60% 80%, rgba(62,207,142,.35) 80%); }
      .s12-bar i { position: absolute; top: -3px; width: 3px; height: 14px; border-radius: 2px; background: var(--text, #eceef0); transform: translateX(-1px); }
      .s12-crow { display: grid; grid-template-columns: minmax(0,1fr) auto 30px; gap: 1px 10px; align-items: center; padding: 6px 0; border-bottom: 1px solid #232528; }
      .s12-crow.na .s12-cn, .s12-crow.na .s12-cv { color: var(--muted, #83888d); }
      .s12-cn { font-size: 13px; min-width: 0; }
      /* naam, waarde en punten op één regel, de schaal eronder over de volle breedte */
      .s12-cs { grid-column: 1 / -1; display: block; font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, monospace); min-width: 0; }
      .s12-cv { font-family: var(--mono, monospace); font-size: 12.5px; text-align: right; } .s12-cv small { color: var(--muted, #83888d); font-size: 11px; }
      .s12-pts { font-family: var(--mono, monospace); font-size: 12px; font-weight: 600; text-align: center; border-radius: 6px; padding: 2px 0;
        color: var(--muted, #83888d); background: rgba(255,255,255,.05); }   /* andere punten dan 0/5/10 (oudere data) blijven een pil */
      .s12-pts.p10 { color: var(--up, #3ecf8e); background: rgba(62,207,142,.12); } .s12-pts.p5 { color: var(--warn, #f5b544); background: rgba(245,181,68,.1); }
      .s12-pts.p0 { color: var(--down, #f0595f); background: rgba(240,89,95,.1); }
      .s12-note { font-size: 11.5px; color: var(--muted, #83888d); line-height: 1.55; margin-top: 8px; } .s12-note b { color: var(--text, #eceef0); }
      .s12-step { border: 1px solid var(--line, #2a2c2f); border-radius: 10px; margin-bottom: 6px; background: rgba(255,255,255,.015); }
      .s12-step > summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: 28px minmax(0,1fr) auto; gap: 10px; align-items: center; padding: 8px 10px; min-height: 40px; }
      .s12-step > summary::-webkit-details-marker { display: none; }
      .s12-step.no { border-color: rgba(240,89,95,.45); } .s12-step.ok { border-color: rgba(62,207,142,.25); }
      .s12-n { font-family: var(--mono, monospace); font-size: 12px; color: var(--muted, #83888d); }
      .s12-t { font-size: 13px; min-width: 0; } .s12-t small { display: block; font-size: 11px; color: var(--muted, #83888d); margin-top: 1px; }
      .s12-st { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line, #2a2c2f); white-space: nowrap; }
      .s12-st.ok { color: var(--up, #3ecf8e); border-color: rgba(62,207,142,.45); } .s12-st.no { color: var(--down, #f0595f); border-color: rgba(240,89,95,.45); }
      .s12-st.chk { color: var(--warn, #f5b544); border-color: rgba(245,181,68,.45); } .s12-st.man { color: var(--muted, #83888d); }
      .s12-flag { margin: 0 10px 6px 48px; font-size: 12px; color: var(--down, #f0595f); }
      .s12-list { list-style: none; margin: 0 10px 10px 48px; padding: 0; display: flex; flex-direction: column; gap: 5px; }
      .s12-list li { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; line-height: 1.4; }
      .s12-list li b { width: 14px; flex-shrink: 0; font-family: var(--mono, monospace); font-size: 12px; }
      .s12-list li b.ok { color: var(--up, #3ecf8e); } .s12-list li b.no { color: var(--down, #f0595f); } .s12-list li b.na { color: var(--muted, #83888d); }
      .s12-list li em { margin-left: auto; font-style: normal; font-family: var(--mono, monospace); font-size: 11.5px; color: var(--muted, #83888d); white-space: nowrap; padding-left: 8px; }
      .s12-list li.man { color: #c8cbcf; } .s12-list label { display: flex; gap: 8px; align-items: baseline; cursor: pointer; }
      .s12-list input[type=checkbox] { width: 16px; height: 16px; accent-color: var(--up, #3ecf8e); flex-shrink: 0; position: relative; top: 2px; }
      .s12-about { margin: 0 10px 10px 48px; font-size: 12px; color: #c8cbcf; line-height: 1.5; font-style: italic; }
      .s12-dcfrow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0 10px 0 48px; }
      .s12-dcf { border: 1px solid var(--line, #2a2c2f); border-radius: 8px; padding: 6px 8px; min-width: 0; }
      .s12-dcf small { display: block; font-size: 11px; color: var(--muted, #83888d); } .s12-dcf b { font-family: var(--mono, monospace); font-size: 13px; }
      .s12-dcf.up b { color: var(--up, #3ecf8e); } .s12-dcf.down b { color: var(--down, #f0595f); }
      .s12-step .s12-note { margin: 8px 10px 10px 48px; }
      .v-good { color: var(--up, #3ecf8e); } .v-mid { color: var(--warn, #f5b544); } .v-bad { color: var(--down, #f0595f); } .v-na { color: var(--muted, #83888d); }
      @media (max-width: 560px) {
        .s12-flag, .s12-list, .s12-about, .s12-dcfrow, .s12-step .s12-note { margin-left: 10px; }
        .s12-dcfrow { grid-template-columns: repeat(2, 1fr); }
        /* tekst blijft naast het vinkje; een lange waarde schuift rechts uitgelijnd naar de regel eronder */
        .s12-list li { flex-wrap: wrap; } .s12-list li > span { flex: 1 1 0; min-width: 60%; }
        .s12-cv small { display: block; }
      }
      /* afvinken met de vinger: de hele regel is een tikvlak van minstens 40px, met een groter vakje */
      @media (pointer: coarse) { .s12-list label { padding: 8px 0; min-height: 40px; align-items: center; }
        .s12-list label input[type=checkbox] { width: 20px; height: 20px; top: 0; } }`;
    document.head.appendChild(st);
  }

  return { NAMES, SUB, CARD, LEVEL, SECTOR_NL, MANUAL, ring, esc, dots, failText, verdict, buyVerdict, cardTable, panel, css };
})();
