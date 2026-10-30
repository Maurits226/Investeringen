// Techradar: tegel en waarschuwingsbanner voor een terugval in Amerikaanse tech en chips. De cijfers komen uit
// tech.json (scripts/tech.py): signalen per dag, het niveau (rustig/opletten/verhoogd/hoog), de huidige stand onder
// de 52-weekse top en de nagerekende historie. TechWarn.tile(data) is de tegel naast de crashradar,
// TechWarn.banner(data, exposure) de waarschuwing; met { compact: true } één regel die je openklapt voor de uitleg
// (Vandaag). Elke pagina gebruikt TechWarn.css() eenmalig.
const TechWarn = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => v == null || v === '' || !isFinite(v) ? null : Number(v);
  const nf = (v, d = 1) => num(v) == null ? '—' : num(v).toLocaleString('nl-NL', { minimumFractionDigits: d, maximumFractionDigits: d });
  const pc = (v, d = 0) => num(v) == null ? '—' : nf(v, d) + '%';
  const under = v => num(v) == null ? '—' : '−' + nf(Math.abs(v), 1) + '%';        // −5,3% (onder de top)
  const signed = (v, d = 1) => num(v) == null ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + nf(Math.abs(v), d) + '%';
  const lift = v => num(v) == null ? '—' : '×' + nf(v, 2);                          // ×1,08: zelfde afronding als de grens ×1,10
  const MND = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
  const ymd = s => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || '')); return m ? [+m[1], +m[2], +m[3]] : null; };
  const dag = s => { const d = ymd(s); return d ? `${d[2]} ${MND[d[1] - 1]}` : '—'; };                // 14 okt
  const maand = s => { const d = ymd(s); return d ? `${MND[d[1] - 1]} ’${String(d[0]).slice(2)}` : '—'; };  // okt ’25

  // niveau en stand → kleur (vaste lijsten: klassen komen nooit rechtstreeks uit de data)
  const LV = { rustig: ['good', 'Rustig', 0], opletten: ['mid', 'Opletten', 1], verhoogd: ['bad', 'Verhoogd', 2], hoog: ['bad hot', 'Hoog', 3] };
  const ST = { normaal: ['good', 'normaal', 0], terugval: ['mid', 'terugval', 1], correctie: ['bad', 'correctie', 2], berenmarkt: ['bad', 'berenmarkt', 3] };
  const lvOf = d => LV[d && d.level && d.level.k] || LV.rustig;
  const stOf = k => ST[k] || ST.normaal;

  function stale(d) {
    const a = ymd(d && d.asof);
    return a ? (Date.now() - Date.UTC(a[0], a[1] - 1, a[2])) / 864e5 > 5 : true;
  }
  // Historie bij een vergelijkbare stand: zelfde niveau én net als nu bij de top (binnen 5%) of al in een daling.
  // Per episode (losse keren), want opeenvolgende dagen tellen dezelfde daling; met te weinig keren geen percentage.
  function matched(d) {
    const m = d && d.level && d.level.m, h = (d && d.horizon) || {};
    if (!m || num(m.n_ep) == null) return null;
    const top = num(h.top) || 5;
    return { m, ok: m.n_ep >= (num(h.min_ep) || 10) && num(m.hit_ep) != null, top: m.cond === 'top',
      waar: m.cond === 'top' ? `met de Nasdaq-100 net als nu binnen ${top}% van zijn top` : `met de Nasdaq-100 net als nu al ${top}% of meer onder zijn top` };
  }
  function histLine(d) {
    const x = matched(d), h = d.horizon || {};
    if (!x) return '';
    const m = x.m, from = String((d.base && d.base.from) || '').slice(0, 4) || '—';
    if (!x.ok) return `Historisch, ${esc(x.waar)}: te weinig vergelijkbare keren (${esc(nf(m.n_ep, 0))}) om de kans bij dit niveau te schatten.`;
    return `Historisch, ${esc(x.waar)}: bij dit niveau volgde in <b>${pc(m.hit_ep)}</b> van de keren binnen 2 maanden een daling van 10%+
      (normaal ${pc(m.base)}).<small>${esc(nf(m.n_ep, 0))} keer (episodes ≥${esc(h.ep_gap || 40)} handelsdagen uit elkaar) sinds ${esc(from)}
      · per dag ${pc(m.hit)} · ${esc(lift(m.lift_ep))} per keer, ${esc(lift(m.lift))} per dag</small>`;
  }

  // Nasdaq-100 over het laatste jaar met het 200-daags gemiddelde (gestippeld)
  function spark(sp) {
    const c = ((sp && sp.c) || []).map(num), s = ((sp && sp.s200) || []).map(num), n = c.length;
    if (n < 2 || c.some(v => v == null)) return '';
    const vals = c.concat(s.filter(v => v != null));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo || 1) * 0.08; lo -= pad; hi += pad;
    const W = 300, H = 60, X = i => (i / (n - 1) * W).toFixed(1), Y = v => ((hi - v) / (hi - lo) * H).toFixed(1);
    const line = c.map((v, i) => (i ? 'L' : 'M') + X(i) + ',' + Y(v)).join('');
    let ma = '', pen = false;
    s.forEach((v, i) => { if (v == null) { pen = false; return; } ma += (pen ? 'L' : 'M') + X(i) + ',' + Y(v); pen = true; });
    return `<svg class="tw-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Nasdaq-100 afgelopen jaar met 200-daags gemiddelde">
      <path d="${line}L${W},${H}L0,${H}Z" class="tw-sa"/>${ma ? `<path d="${ma}" class="tw-sm"/>` : ''}<path d="${line}" class="tw-sl"/></svg>`;
  }

  function sigRow(s) {
    const na = s.na || s.on == null;                       // geen bron of geen actuele waarde: nooit 'uit' tonen
    const cls = na ? 'na' : s.on ? 'on' : 'off';
    let st = na ? (s.oud ? 'geen actuele data' : 'geen data') : s.on ? (s.since ? 'aan sinds ' + dag(s.since) : 'aan') : 'uit';
    if (!na && s.opt && !s.in_mix) st += ' · telt niet mee';
    const weak = num(s.lift) != null && s.lift < 1.1;
    return `<li class="${cls}" title="${esc(s.uitleg)}"><i></i><span>${esc(s.name)}</span>
      <em>${esc(st)}${na ? '' : ` · <b class="${weak ? 'weak' : ''}" title="historisch ${esc(lift(s.lift))} de normale kans op 10%+ daling">${esc(lift(s.lift))}</b>`}</em></li>`;
  }

  function details(d, open) {
    const sigs = (d.signals || []).filter(s => !s.na);
    const cs = d.corr_sum || {}, b = d.base || {}, h = d.horizon || {}, minEp = num(h.min_ep) || 10, topPct = num(h.top) || 5;
    const cell = (a, b) => `${pc(a)}<small>${b}</small>`;
    // ×kans per dag, klein per episode; grijs onder ×1,10 of bij te weinig episodes
    const liftCell = (a, b, few) => `<span class="${few || (num(a) != null && a < 1.1) ? 'weak' : num(a) != null && a >= 1.5 ? 'hi' : ''}">${esc(lift(a))}</span><small>ep ${esc(lift(b))}</small>`;
    // per episode alleen tonen bij genoeg episodes ('rustig' is bv. één lange episode: 100% zegt dan niets)
    const epOk = x => num(x && x.n_ep) >= minEp;
    const topCell = t => { t = t || {}; return liftCell(t.lift, epOk(t) ? t.lift_ep : null, !epOk(t)); };
    const topEp = t => num(t && t.n_ep) == null ? '' : ` (bij top ${esc(nf(t.n_ep, 0))})`;
    const sRows = sigs.map(s => `<tr class="${s.on ? 'cur' : ''}"><td class="l">${esc(s.name)}<small>${esc(nf(s.n_days, 0))} d · ${esc(nf(s.n_ep, 0))} ep${topEp(s.top)} · sinds ${esc(String(s.from || '').slice(0, 4))}${s.opt ? (s.in_mix ? ' · optioneel, telt mee' : ' · optioneel, telt niet mee') : ''}</small></td>
      <td>${cell(s.hit, 'norm. ' + pc(s.base))}</td><td>${liftCell(s.lift, epOk(s) ? s.lift_ep : null)}</td><td>${topCell(s.top)}</td></tr>`).join('');
    const curK = d.level && d.level.k;
    const lRows = (d.levels || []).map(l => `<tr class="${l.k === curK ? 'cur' : ''}"><td class="l">${esc(l.label)}<small>${esc(l.rule)} · ${pc(l.share)} van de tijd · ${esc(nf(l.n_ep, 0))} ep${topEp(l.top)} · ≥15%: ${pc(l.hit15)}</small></td>
      <td>${cell(l.hit10, 'ep ' + (epOk(l) ? pc(l.hit10_ep) : '—'))}</td><td>${liftCell(l.lift, epOk(l) ? l.lift_ep : null)}</td><td>${topCell(l.top)}</td></tr>`).join('');
    const warnTxt = c => {
      if (c.nvt) return '<small>n.v.t.</small>';
      if (num(c.lead) == null) return '<span class="weak">geen</span>';
      if (c.al) return `<span class="ok">al ≥${esc(h.lead || 30)} d vóór top</span>`;
      if (c.lead > 0) return `<span class="ok">${esc(c.lead)} d vóór top</span>`;
      if (c.lead === 0) return '<span class="ok">op de top</span>';
      return c.voor10 > 0 ? `<span class="mid">na top · ${esc(c.voor10)} d vóór −10%</span>` : '<span class="weak">pas bij −10%</span>';
    };
    const golf = c => c.top === false;                       // golf binnen een lopende daling: telt niet mee in de som
    const cRows = (d.corrections || []).slice().reverse().map(c => `<tr class="${golf(c) ? 'golf' : ''}"><td class="l">${esc(maand(c.peak))} → ${esc(maand(c.trough))}<small>${esc(c.days)} handelsdagen omlaag${c.end ? '' : ' · loopt nog'}${golf(c) ? ' · golf in een daling' : ''}</small></td>
      <td class="dn">${under(c.depth)}</td><td class="l r">${warnTxt(c)}</td></tr>`).join('');
    const nShown = (d.corrections || []).length, nAll = (num(cs.n) || 0) + (num(cs.n_golf) || 0);
    const errs = (d.source_errors || []).map(e => `<li>${esc(e)}</li>`).join('');
    return `<details class="tw-det"${open ? ' open' : ''}><summary>Techradar · onderbouwing en historie<span class="tw-btn"></span></summary><div class="tw-dbody">
      <div class="tw-dk">Signalen <span>kans op ≥10% daling binnen ${esc(h.h10 || 40)} handelsdagen</span></div>
      <div class="tw-tw"><table class="tw-t"><tr><th>signaal</th><th>≥10%</th><th>×kans</th><th>bij top</th></tr>${sRows}</table></div>
      <div class="tw-dk">Niveaus <span>sinds ${esc(String(b.from || '').slice(0, 4) || '—')} · normaal ${pc(b.hit10)}, bij de top ${pc(b.hit10_top)} · ≥15% in ${esc(h.h15 || 60)} d: ${pc(b.hit15)}</span></div>
      <div class="tw-tw"><table class="tw-t"><tr><th>niveau</th><th>≥10%</th><th>×kans</th><th>bij top</th></tr>${lRows}</table></div>
      <div class="tw-dk">Correcties Nasdaq-100 <span>≥10% onder de top · nieuwste eerst</span></div>
      <div class="tw-tw"><table class="tw-t"><tr><th>top → bodem</th><th>daling</th><th>‘verhoogd’ of hoger</th></tr>${cRows}</table></div>
      <div class="tw-note">${num(cs.n) ? `Van de <b>${esc(cs.n)}</b> correcties sinds 2001 die bij de jaartop begonnen, gaf de radar bij <b>${esc(cs.pre)}</b> al vóór de top een nieuwe waarschuwing (‘verhoogd’ of hoger, binnen ${esc(h.lead || 30)} handelsdagen of al langer),
        en bij <b>${esc(cs.voor10)}</b> nog vóór de daling 10% was. Een waarschuwing telt hooguit voor één correctie; een waarschuwing uit de vorige daling telt niet${num(cs.n_golf) ? `, en ${esc(cs.n_golf)} golven binnen een lopende daling (grijs) tellen niet mee` : ''}${nShown < nAll ? ` (laatste ${esc(nShown)} getoond)` : ''}. ` : ''}
        ≥10% = de Nasdaq-100 sloot binnen ${esc(h.h10 || 40)} handelsdagen (±2 maanden) minstens één keer 10% lager dan op de signaaldag. <b>Dagen</b> telt elke signaaldag; <b>ep</b> (episode) alleen de eerste dag van een reeks.
        Episodes liggen minstens ${esc(h.ep_gap || 40)} handelsdagen uit elkaar, zodat één daling niet dubbel telt; daarom zijn ze eerlijker dan dagen. ×kans = hoeveel keer de normale kans; grijs (onder ×1,10, of minder dan ${esc(minEp)} episodes) = voegt niets toe of zegt te weinig.
        <b>Bij top</b> = alleen dagen waarop de Nasdaq-100 binnen ${esc(topPct)}% van zijn 52-weekse top stond, vergeleken met alle zulke dagen: dat toetst of de radar vóóraf waarschuwt. Midden in een daling zijn grote bewegingen sowieso waarschijnlijker.
        ≥15% = binnen ${esc(h.h15 || 60)} handelsdagen. Signalen gebruiken alleen koersen tot en met die dag. Een waarschuwing is geen voorspelling: ook bij ‘hoog’ volgde vaak géén correctie.</div>
      ${errs ? `<ul class="tw-err">${errs}</ul>` : ''}
      <div class="tw-note">Bronnen: Nasdaq-100, chipindex (SOX), angstindex Nasdaq (VXN) en S&amp;P 500 via Yahoo. Bijgewerkt ${esc(dag(d.updated))} ${esc(String(d.updated || '').slice(11, 16))} UTC. Geen beleggingsadvies.</div>
    </div></details>`;
  }

  // opts.details === false: zonder de uitklapbare onderbouwing (de pagina toont die dan zelf over de volle breedte, via details())
  function tile(d, opts = {}) {
    if (!d || !d.level || !d.state) return '';
    css();
    const L = lvOf(d), nd = d.ndx || {}, sx = d.sox, vx = d.vxn;
    const sigs = d.signals || [];
    const meter = (name, dd, st, sub) => `<div class="tw-m"><div class="tw-ml">${name} <b>${under(dd)}</b></div>
      <div class="tw-ms ${stOf(st)[0]}">${esc(stOf(st)[1])}</div><div class="tw-mx">${esc(sub)}</div></div>`;
    return `<div class="tw-cc ${L[0]}">
      <div class="tw-k"><i></i>Techradar · Nasdaq-100 &amp; chips</div>
      <div class="tw-main"><span class="tw-verdict">${L[1]}</span><span class="tw-num">${esc(nf(d.level.n_on, 0))}<small>/ ${esc(nf(d.level.n_max, 0))} signalen</small></span></div>
      <div class="tw-sub">${esc(d.level.text)}</div>
      <div class="tw-spark">${spark(d.spark)}<div class="tw-sk"><span>Nasdaq-100 · 1 jaar</span><span>${esc(nf(nd.close, 0))} · <i></i>200d ${esc(nf(nd.sma200, 0))} (${signed(nd.ext200)})</span></div></div>
      <div class="tw-ms2">
        ${meter('Nasdaq-100', nd.dd52, d.state.status, 'onder de jaartop')}
        ${sx ? meter('Chips (SOX)', sx.dd52, sx.status, sx.oud ? 'verouderd · t/m ' + dag(sx.date) : 'onder de jaartop') : `<div class="tw-m"><div class="tw-ml">Chips (SOX) <b>—</b></div><div class="tw-mx">geen data</div></div>`}
      </div>
      <ul class="tw-sigs">${sigs.map(sigRow).join('')}</ul>
      ${vx ? `<div class="tw-vx">Angstindex Nasdaq (VXN) <b>${esc(nf(vx.level, 1))}</b> · 5d ${signed(vx.chg5, 0)} · gem. 50d ${esc(nf(vx.sma50, 1))}${vx.oud ? ` · <b class="old">verouderd (t/m ${esc(dag(vx.date))})</b>` : ''}</div>` : ''}
      ${histLine(d) ? `<div class="tw-hist">${histLine(d)}</div>` : ''}
      ${opts.details === false ? '' : details(d)}
      <div class="tw-basis">${esc(d.state.text)} Data t/m ${esc(dag(d.asof))}${stale(d) ? ' <b class="old">(verouderd)</b>' : ''}.</div>
    </div>`;
  }

  // Compacte waarschuwing voor Vandaag; leeg zolang het rustig is en tech niet onder de top zakt. Zit de Nasdaq-100 al in
  // een correctie of berenmarkt, dan gaat die stand voor (de daling is er al) en is de radar een tweede zin.
  function banner(d, exposure, opts = {}) {
    if (!d || !d.level || !d.state) return '';
    const lk = LV[d.level.k] ? d.level.k : 'rustig', sk = ST[d.state.status] ? d.state.status : 'normaal';
    if (lk === 'rustig' && sk === 'normaal') return '';
    css();
    const L = LV[lk], S = ST[sk], nd = d.ndx || {}, lv = d.level;
    const inDal = S[2] >= 2;
    const tone = L[2] >= 2 || inDal ? 'bad' : 'mid';
    const angst = (d.signals || []).some(s => s.k === 'angst_stijgt' && s.on);
    const x = matched(d);
    // 'risico op een terugval' alleen als dit niveau bij een vergelijkbare stand historisch echt extra kans gaf
    const sterk = x && x.ok && num(x.m.lift) >= 1.2 && num(x.m.lift_ep) >= 1.2;
    const title = inDal ? `Tech in een ${S[1]}`
      : L[2] >= 2 ? `Techradar: ${L[1].toLowerCase()}${sterk ? ' risico op een terugval in tech en chips' : ''}`
      : L[2] >= 1 ? 'Techradar: opletten' : 'Tech zakt terug';
    const hist = x && x.ok && L[2] >= 1
      ? ` Historisch volgde bij dit niveau (${x.top ? 'dicht bij de top' : 'al onder de top'}) in ${pc(x.m.hit_ep)} van de keren binnen 2 maanden een daling van 10%+ (normaal ${pc(x.m.base)}).` : '';
    const radar = `Techradar: ${L[1].toLowerCase()} (${nf(lv.n_on, 0)} van ${nf(lv.n_max, 0)} signalen).`;
    const text = inDal ? `${d.state.text || ''}${L[2] >= 1 ? ' ' + radar + hist : ''}`
      : L[2] >= 1 ? `${lv.text || d.state.text || ''}${hist}` : (d.state.text || '');
    const exp = exposure && num(exposure.pct) > 0 ? (() => {
      const names = (exposure.names || []).map(String);
      const list = names.length ? ` (${esc(names.slice(0, 4).join(', '))}${names.length > 4 ? ` +${names.length - 4}` : ''})` : '';
      return `Je portefeuille zit voor <b>${pc(exposure.pct)}</b> in tech/chips${list}${exposure.pct >= 50 ? ': een tech-correctie raakt je hard.' : '.'}`;
    })() : '';
    const tips = [];
    if (inDal) {                                             // één lijn: koopladder, niets extra zolang de angst stijgt
      tips.push((S[2] >= 3 ? 'Berenmarkt: koop alleen volgens je koopladder en spreid de treden, met geld dat je jaren kunt missen'
        : 'De correctie is bezig: koop alleen volgens je koopladder en spreid de treden, niet alles in één keer')
        + (angst ? '; niets extra zolang de angst stijgt.' : '.'));
    } else {
      if (L[2] >= 1 && angst) tips.push('Geen nieuwe tech bijkopen zolang de angst stijgt: wacht tot de angstindex weer zakt.');
      else if (L[2] >= 2) tips.push('Geen haast met nieuwe tech-aankopen; liever gespreid in delen dan alles tegelijk.');
      tips.push('Houd cash klaar voor de koopladder (−10%, −20%, −30% onder de top).');
    }
    tips.push('Check per positie je these (en je stop): is er iets aan het bedrijf veranderd? Een koersdaling alleen is nooit een reden om een goed bedrijf te verkopen.');
    if (exposure && num(exposure.pct) != null && exposure.pct >= 50 && tips.length < 4) tips.push('Zit je zwaar in tech, stuur nieuwe inleg dan liever naar andere sectoren dan te verkopen.');
    const sxOud = d.sox && d.sox.oud ? ' · verouderd' : '';
    if (opts.compact) {
      // één regel: niveau en hoeveel van je portefeuille het raakt; de uitleg en wat je kunt doen pas na openklappen
      const short = inDal ? `Tech in een ${S[1]}` : L[2] >= 1 ? `Techradar ${L[1].toLowerCase()}` : 'Tech zakt terug';
      const raakt = exposure && num(exposure.pct) > 0 ? `${pc(exposure.pct)} van je portefeuille in tech` : `${nf(lv.n_on, 0)} van ${nf(lv.n_max, 0)} signalen`;
      return `<details class="tw-ban tw-mini ${tone}" data-tw="banner"${opts.open ? ' open' : ''}>
        <summary><i></i><b>${esc(short)}</b><span class="tw-mi">${esc(raakt)}</span><span class="tw-mb">Wat te doen</span></summary>
        <div class="tw-pills"><span class="tw-pill ${L[0].split(' ')[0]}">radar ${esc(L[1].toLowerCase())} · ${esc(nf(lv.n_on, 0))}/${esc(nf(lv.n_max, 0))} signalen</span>
          <span class="tw-pill ${S[0]}">Nasdaq-100 ${under(nd.dd52)} · ${esc(S[1])}</span>${d.sox ? `<span class="tw-pill ${stOf(d.sox.status)[0]}">chips ${under(d.sox.dd52)} · ${esc(stOf(d.sox.status)[1])}${sxOud}</span>` : ''}</div>
        ${text ? `<div class="tw-bt">${esc(text)}</div>` : ''}
        ${exp ? `<div class="tw-bt">${exp}</div>` : ''}
        <ul class="tw-bl">${tips.map(t => `<li>${esc(t)}</li>`).join('')}</ul>
        <div class="tw-bf">Techradar · data t/m ${esc(dag(d.asof))}${stale(d) ? ' (verouderd)' : ''} · hulpmiddel, geen beleggingsadvies</div>
      </details>`;
    }
    return `<div class="tw-ban ${tone}" role="status" data-tw="banner">
      <div class="tw-bh"><i></i><b>${esc(title)}</b></div>
      <div class="tw-pills"><span class="tw-pill ${L[0].split(' ')[0]}">radar ${esc(L[1].toLowerCase())} · ${esc(nf(lv.n_on, 0))}/${esc(nf(lv.n_max, 0))} signalen</span>
        <span class="tw-pill ${S[0]}">Nasdaq-100 ${under(nd.dd52)} · ${esc(S[1])}</span>${d.sox ? `<span class="tw-pill ${stOf(d.sox.status)[0]}">chips ${under(d.sox.dd52)} · ${esc(stOf(d.sox.status)[1])}${sxOud}</span>` : ''}</div>
      ${text ? `<div class="tw-bt">${esc(text)}</div>` : ''}
      ${exp ? `<div class="tw-bt">${exp}</div>` : ''}
      <ul class="tw-bl">${tips.map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      <div class="tw-bf">Techradar · data t/m ${esc(dag(d.asof))}${stale(d) ? ' (verouderd)' : ''} · hulpmiddel, geen beleggingsadvies</div>
    </div>`;
  }

  let cssDone = false;
  function css() {
    if (cssDone || typeof document === 'undefined') return;
    cssDone = true;
    const st = document.createElement('style');
    st.textContent = `
      .tw-cc { position: relative; overflow: hidden; border-radius: 14px; padding: 16px 18px 14px; min-width: 0;
        background: linear-gradient(180deg, var(--panel, #1a1c1e), var(--ink, #141517)); border: 1px solid var(--line, #2a2c2f);
        display: flex; flex-direction: column; gap: 10px; --mark: #2dd4bf; --tone: var(--muted, #83888d); color: var(--text, #eceef0); }
      .tw-cc::before { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 2px; background: var(--mark); }
      .tw-cc::after { content: ''; position: absolute; right: -60px; top: -60px; width: 200px; height: 200px; border-radius: 50%; pointer-events: none;
        background: radial-gradient(circle, color-mix(in srgb, var(--tone) 16%, transparent), transparent 70%); }
      .tw-cc.good, .tw-ban.good { --tone: var(--up, #3ecf8e); } .tw-cc.mid, .tw-ban.mid { --tone: var(--warn, #f5b544); }
      .tw-cc.bad, .tw-ban.bad { --tone: var(--down, #f0595f); } .tw-cc.hot { border-color: rgba(240,89,95,.5); }
      .tw-k { display: flex; align-items: center; gap: 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .9px; color: var(--muted, #83888d); }
      .tw-k i { width: 7px; height: 7px; border-radius: 50%; background: var(--mark); box-shadow: 0 0 0 3px rgba(45,212,191,.22); }
      .tw-main { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
      .tw-verdict { font-size: 22px; font-weight: 700; letter-spacing: -.4px; color: var(--tone); line-height: 1.1; }
      .tw-num { font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); font-size: 22px; font-weight: 600; color: var(--tone); white-space: nowrap; }
      .tw-num small { font-size: 11px; font-weight: 500; color: var(--muted, #83888d); margin-left: 4px; }
      .tw-sub { font-size: 12.5px; color: var(--muted, #83888d); margin-top: -4px; line-height: 1.45; }
      .tw-spark { position: relative; }
      .tw-svg { display: block; width: 100%; height: 56px; }
      .tw-sa { fill: rgba(45,212,191,.08); stroke: none; }
      .tw-sl { fill: none; stroke: var(--mark); stroke-width: 1.6; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .tw-sm { fill: none; stroke: var(--muted, #83888d); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
      .tw-sk { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; font-size: 11px; color: var(--muted, #83888d);
        font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); margin-top: 3px; }
      .tw-sk i { display: inline-block; width: 10px; border-top: 1px dashed var(--muted, #83888d); vertical-align: middle; margin-right: 3px; }
      .tw-ms2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .tw-m { background: rgba(255,255,255,.025); border: 1px solid var(--line, #2a2c2f); border-radius: 10px; padding: 8px 10px; min-width: 0; }
      .tw-ml { font-size: 11px; color: var(--muted, #83888d); display: flex; justify-content: space-between; gap: 6px; flex-wrap: wrap; }
      .tw-ml b { color: var(--text, #eceef0); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); font-weight: 600; }
      .tw-ms { font-size: 12.5px; font-weight: 600; margin-top: 4px; }
      .tw-ms.good, .tw-pill.good { color: var(--up, #3ecf8e); } .tw-ms.mid, .tw-pill.mid { color: var(--warn, #f5b544); } .tw-ms.bad, .tw-pill.bad { color: var(--down, #f0595f); }
      .tw-mx { font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); margin-top: 2px; }
      .tw-sigs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
      .tw-sigs li { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; min-width: 0; }
      .tw-sigs li i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: #3a3e43; position: relative; top: -1px; }
      .tw-sigs li.on i { background: var(--warn, #f5b544); box-shadow: 0 0 0 3px rgba(245,181,68,.18); }
      .tw-sigs li.na { color: var(--muted, #83888d); } .tw-sigs li.na i { background: transparent; box-shadow: inset 0 0 0 1px #5a5f64; }
      .tw-sigs li.off span { color: #c8cbcf; }
      .tw-sigs li span { min-width: 0; overflow-wrap: anywhere; } .tw-sigs li.on span { font-weight: 600; }
      .tw-sigs li em { margin-left: auto; font-style: normal; font-size: 11px; color: var(--muted, #83888d); text-align: right;
        font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); white-space: nowrap; padding-left: 6px; }
      .tw-sigs li.on em { color: var(--warn, #f5b544); }
      .tw-sigs li em b { font-weight: 500; color: var(--text, #eceef0); } .tw-sigs li em b.weak { color: var(--muted, #83888d); }
      .tw-vx { font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); }
      .tw-vx b { color: var(--text, #eceef0); font-weight: 600; }
      .tw-hist { font-size: 12px; line-height: 1.5; color: #c8cbcf; background: rgba(255,255,255,.025); border: 1px solid var(--line, #2a2c2f); border-radius: 10px; padding: 8px 10px; }
      .tw-hist b { color: var(--tone); } .tw-hist small { display: block; font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); margin-top: 3px; }
      .tw-det { border: 1px solid var(--line, #2a2c2f); border-radius: 10px; background: rgba(255,255,255,.015); min-width: 0; }
      .tw-det > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; padding: 9px 10px; font-size: 12.5px; min-height: 40px; }
      .tw-det > summary::-webkit-details-marker { display: none; }
      .tw-btn { margin-left: auto; font-size: 11px; color: var(--mark); border: 1px solid rgba(45,212,191,.45); padding: 3px 9px; border-radius: 8px; white-space: nowrap; }
      .tw-btn::after { content: 'tonen ▾'; } .tw-det[open] .tw-btn::after { content: 'verbergen ▴'; }
      .tw-dbody { padding: 0 10px 10px; min-width: 0; }
      .tw-dk { font-size: 11px; text-transform: uppercase; letter-spacing: .9px; color: var(--muted, #83888d); margin: 10px 0 4px; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 8px; }
      .tw-dk span { text-transform: none; letter-spacing: 0; font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); }
      .tw-tw { overflow-x: auto; -webkit-overflow-scrolling: touch; }
      .tw-t { width: 100%; border-collapse: collapse; font-size: 11.5px; }
      .tw-t th { text-align: right; font-weight: 500; color: var(--muted, #83888d); font-size: 11px; padding: 3px 0 3px 14px; white-space: nowrap; }
      .tw-t th:first-child { text-align: left; padding-left: 0; }
      .tw-t td { padding: 4px 0 4px 14px; border-top: 1px dashed #26282b; vertical-align: top; text-align: right; white-space: nowrap;
        font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); }
      .tw-t td.l { text-align: left; white-space: normal; font-family: inherit; padding-left: 0; overflow-wrap: anywhere; }
      .tw-t td.l.r { text-align: right; padding-left: 14px; }
      .tw-t td small { display: block; font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); }
      .tw-t tr.cur td { color: var(--warn, #f5b544); } .tw-t td.dn { color: var(--down, #f0595f); }
      .tw-t .hi { color: var(--warn, #f5b544); font-weight: 600; } .tw-t .weak { color: var(--muted, #83888d); }
      .tw-t .ok { color: var(--up, #3ecf8e); } .tw-t .mid { color: var(--warn, #f5b544); } .tw-t tr.golf td { opacity: .55; }
      .tw-vx .old { color: var(--warn, #f5b544); font-weight: 500; }
      .tw-note { font-size: 11px; color: var(--muted, #83888d); line-height: 1.55; margin-top: 8px; } .tw-note b { color: var(--text, #eceef0); font-weight: 600; }
      .tw-err { margin: 8px 0 0 16px; font-size: 11px; color: var(--down, #f0595f); line-height: 1.5; overflow-wrap: anywhere; }
      .tw-basis { font-size: 11px; color: var(--muted, #83888d); line-height: 1.5; border-top: 1px solid var(--line, #2a2c2f); padding-top: 9px; margin-top: auto; }
      .tw-basis .old { color: var(--warn, #f5b544); font-weight: 500; }
      .tw-ban { --tone: var(--warn, #f5b544); position: relative; border: 1px solid var(--line, #2a2c2f); border-left: 3px solid var(--tone); border-radius: 12px;
        background: var(--panel, #1a1c1e); background: linear-gradient(90deg, color-mix(in srgb, var(--tone) 9%, var(--panel, #1a1c1e)), var(--panel, #1a1c1e) 60%);
        padding: 11px 14px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 6px; color: var(--text, #eceef0); min-width: 0; }
      .tw-bh { display: flex; align-items: baseline; gap: 8px; font-size: 14px; line-height: 1.35; }
      .tw-bh i { width: 8px; height: 8px; border-radius: 50%; background: var(--tone); box-shadow: 0 0 0 3px rgba(245,181,68,.18); flex-shrink: 0; position: relative; top: -1px; }
      .tw-bh b { font-weight: 700; color: var(--tone); min-width: 0; }
      .tw-pills { display: flex; gap: 5px; flex-wrap: wrap; }
      .tw-pill { font-size: 11px; font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); color: var(--muted, #83888d); border: 1px solid var(--line, #2a2c2f);
        padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
      .tw-bt { font-size: 12.5px; color: #c8cbcf; line-height: 1.5; } .tw-bt b { color: var(--text, #eceef0); }
      .tw-bl { margin: 0 0 0 18px; padding: 0; font-size: 12.5px; line-height: 1.5; color: var(--text, #eceef0); }
      .tw-bl li::marker { color: var(--tone); }
      .tw-bf { font-size: 11px; color: var(--muted, #83888d); font-family: var(--mono, 'IBM Plex Mono', ui-monospace, monospace); }
      /* compact (Vandaag): één regel, de rest na openklappen */
      .tw-mini { padding: 0; gap: 0; background: var(--panel, #1a1c1e); }
      .tw-mini > summary { list-style: none; cursor: pointer; display: flex; align-items: center; flex-wrap: wrap; gap: 4px 10px; padding: 10px 14px; min-height: 44px; }
      .tw-mini > summary::-webkit-details-marker { display: none; }
      .tw-mini > summary i { width: 8px; height: 8px; border-radius: 50%; background: var(--tone); flex-shrink: 0; }
      .tw-mini > summary b { font-size: 13.5px; font-weight: 700; color: var(--tone); }
      .tw-mi { font-size: 12.5px; color: var(--muted, #83888d); }
      .tw-mb { margin-left: auto; font-size: 12px; color: var(--muted, #83888d); white-space: nowrap; }
      .tw-mb::after { content: ' ▾'; } .tw-mini[open] .tw-mb::after { content: ' ▴'; }
      .tw-mini > summary:hover .tw-mb { color: var(--text, #eceef0); }
      .tw-mini[open] > summary { border-bottom: 1px solid var(--line, #2a2c2f); }
      .tw-mini > :not(summary) { margin: 0 14px; }
      .tw-mini > .tw-pills { margin-top: 10px; } .tw-mini > .tw-bt, .tw-mini > .tw-bl { margin-top: 8px; }
      .tw-mini > .tw-bl { margin-left: 32px; } .tw-mini > .tw-bf { margin: 8px 14px 12px; }
      @media (max-width: 480px) {
        .tw-cc { padding: 14px 13px 12px; } .tw-verdict, .tw-num { font-size: 20px; }
        .tw-ms2 { gap: 8px; } .tw-m { padding: 7px 8px; }
        .tw-sigs li { flex-wrap: wrap; } .tw-sigs li em { margin-left: auto; }
        .tw-dbody { padding: 0 8px 8px; } .tw-t { font-size: 11px; } .tw-t td, .tw-t th, .tw-t td.l.r { padding-left: 10px; }
      }`;
    document.head.appendChild(st);
  }

  return { tile, details, banner, css, esc };
})();
