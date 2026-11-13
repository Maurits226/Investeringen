#!/usr/bin/env python3
"""
Bewijs per pijler van de marktrang, en de gewichten die dat bewijs volgen. Rekenwerk voor screener.py: geen netwerk.

Waarom: de marktrang telt vijf pijlers op (scorecard, kwaliteit, groei, waardering, momentum). Welke daarvan zeggen iets
over het rendement daarna? Twee bronnen:

  1. Teruggerekend. Uit de maandkoersen (5 jaar, met dividend) en de jaarcijfers in de cache rekenen we per maandeinde
     uit hoe elk bedrijf er tóén voor stond, alleen met wat op dat moment bekend was: jaarcijfers tellen pas 4 maanden
     na het einde van het boekjaar, en alleen bedrijven die toen al groot genoeg waren voor het universum doen mee.
     Per pijler vergelijken we binnen de sector (zoals de marktrang zelf) en meten we wat er 1, 6 en 12 maanden later
     gebeurde: de samenhang tussen de rang en het rendement (in euro, t.o.v. het sectorgemiddelde), en het verschil
     tussen het beste en het slechtste vijfde deel.
     Beperkingen, die er eerlijk bij horen: de voorspelde cijfers van analisten (verwachte K/W, verwachte groei,
     bijstellingen) en de kwalitatieve oordelen (moat, CEO) zijn niet terug te halen, dus de pijlers zijn hier een
     benadering; de scorecard zelf is niet terug te rekenen; en bedrijven die sindsdien uit het universum zijn gevallen
     ontbreken (daarom meten we ook apart bij bedrijven die toen ruim boven de grens zaten, waar dat nauwelijks speelt).
  2. Live. Elke maand leggen we de echte stand van alle bedrijven vast (alle pijlers, scorecard, waardering,
     koopmoment) en volgen we wat er daarna gebeurt. Dat is de zuiverste meting, maar die groeit met één maand per maand.

Gewichten. Een pijler krijgt pas meer gewicht (×1,25) als hij het over een half jaar duidelijk goed deed (in beide
groepen bedrijven) én ook werkte in maanden dat de beurs daalde: anders loopt de score achter de laatste beweging aan
(wat in een stijgende beurs werkt, valt in een daling vaak het hardst). Minder gewicht (×0,75) alleen als hij in
stijgende én dalende maanden tegenwerkte. Eenmaal toegekend vervalt het pas als het bewijs duidelijk is verzwakt.
"""
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict

VERSION = 1
LAG = 4                      # jaarcijfers zijn bekend vanaf 4 maanden na het einde van het boekjaar
MIN_PEERS = 15               # minder bedrijven in een sector: vergelijken met de hele markt (zoals de marktrang)
HS = (1, 6, 12)              # maanden vooruit
H_MAIN = 6                   # hierop wordt beslist: een half jaar
CAP = {"VS": 2e9, "EU": 1e9}  # grens van het universum (zelfde als de brede scan), in de valuta van de koers
BIG = 5                      # 'ruim boven de grens': toen minstens 5x zo groot (valt zelden nog uit het universum)
MIN_ROWS = 200               # minder bedrijven in een maand: die maand telt niet
MIN_N = 100                  # minder bedrijven met een pijler in een maand: geen meting
MIN_MONTHS = 24              # minder maanden bewijs: gewicht blijft wat het was
MIN_SIDE = 6                 # minstens zoveel stijgende en zoveel dalende maanden
T_ON, T_OFF = 2.0, 1.5       # duidelijk bewijs vanaf t = 2; eenmaal toegekend vervalt het pas onder 1,5
SIDE_ON, SIDE_OFF = 0.01, -0.01   # in stijgende en in dalende maanden: samenhang minstens +0,01; vervalt pas onder -0,01
MULT_UP, MULT_DOWN = 1.25, 0.75
PIJLERS = [("F", "Scorecard"), ("Q", "Kwaliteit"), ("G", "Groei"), ("V", "Waardering"), ("M", "Momentum")]
# kenmerk: hoger = beter. De benadering van elke pijler uit wat terug te halen is.
PIL = {"Q": [("gp_assets", True), ("roic", True), ("fcf_margin", True), ("cash_conv", True), ("dilution", False)],
       "G": [("rev_yoy", True), ("ni_g", True)],
       "V": [("fcf_yield", True), ("ev_ebitda", False), ("pe", False)],
       "M": [("chg12", True), ("near_high", True)]}
ANNUAL = {"rev": ("annualTotalRevenue", "annualOperatingRevenue"), "gp": ("annualGrossProfit",), "ta": ("annualTotalAssets",),
          "oi": ("annualOperatingIncome",), "cl": ("annualCurrentLiabilities",), "fcf": ("annualFreeCashFlow",),
          "ocf": ("annualOperatingCashFlow",), "ni": ("annualNetIncome",), "sh": ("annualDilutedAverageShares",),
          "ebitda": ("annualEBITDA",), "debt": ("annualTotalDebt",),
          "cash": ("annualCashCashEquivalentsAndShortTermInvestments", "annualCashAndCashEquivalents")}


def mno(datestr):
    """'2025-12-31' -> maandnummer (jaar x 12 + maand - 1), zelfde telling als screener.month_no."""
    return int(datestr[:4]) * 12 + int(datestr[5:7]) - 1


def ym(m):
    return f"{m // 12}-{m % 12 + 1:02d}"


def reeks(pm):
    """Maandreeks uit de cache ({"m": eerste maand, "c": [...]}) als {maandnummer: waarde}."""
    return {pm["m"] + i: v for i, v in enumerate(pm.get("c") or []) if v} if pm else {}


class Aandeel:
    """Eén bedrijf uit de cache: maandkoersen, jaarcijfers per boekjaar en het aantal aandelen van nu."""
    __slots__ = ("sym", "region", "sector", "m0", "c", "a", "A", "sh_now", "sh_last", "cur", "fcur", "fys")

    def __init__(self, sym, c):
        pm, ts = c["pm"], c.get("ts") or {}
        self.sym, self.region, self.sector = sym, c.get("region"), c.get("sector") or "?"
        self.m0, self.c = pm["m"], pm["c"]
        self.a = pm.get("a") or pm["c"]              # met dividend, als die reeks er is
        self.A = {}
        for key, names in ANNUAL.items():
            rows = next((ts[n] for n in names if ts.get(n)), [])
            self.A[key] = {mno(d): v for d, v in rows if v is not None}
        self.fys = sorted(self.A["rev"])
        px, mc = c.get("price"), c.get("mcap")
        self.sh_now = mc / px if mc and px else None
        self.sh_last = self.A["sh"][max(self.A["sh"])] if self.A["sh"] else None
        self.cur = "USD" if self.region == "VS" else "EUR"
        self.fcur = c.get("fcur") or self.cur

    def px(self, m, adj=False):
        i = m - self.m0
        s = self.a if adj else self.c
        return s[i] if 0 <= i < len(s) else None


def aandelen(cache):
    """Alle bedrijven met jaarcijfers en maandkoersen (niet overgeslagen) uit de twee regio's."""
    return [Aandeel(s, c) for s, c in cache.items() if c.get("ts") and not c.get("skip") and c.get("region") in CAP
            and len((c.get("pm") or {}).get("c") or []) >= 13]


def kenmerken(s, m, fx):
    """De kenmerken van één bedrijf op maandeinde m, alleen uit wat toen bekend was. None zonder koers.
    '_mc' = de beurswaarde van toen (koers van toen x aantal aandelen van nu, geschaald met de verwatering sindsdien)."""
    c = s.px(m)
    if not c:
        return None
    out, A = {}, s.A
    if s.sh_now:
        out["_mc"] = c * s.sh_now                    # nog geen jaarcijfers bekend: het aantal aandelen van nu
    known = [k for k in s.fys if k + LAG <= m]
    if known:
        k, k1 = known[-1], (known[-2] if len(known) > 1 else None)
        g = lambda key, kk=k: A[key].get(kk) if kk is not None else None  # noqa: E731
        rev, gp, ta, oi, cl, fcf, ocf, ni, sh = (g(x) for x in ("rev", "gp", "ta", "oi", "cl", "fcf", "ocf", "ni", "sh"))
        if gp is not None and ta:
            out["gp_assets"] = gp / ta * 100
        if oi is not None and ta and cl is not None and ta - cl > 0:
            out["roic"] = oi * 0.79 / (ta - cl) * 100
        if fcf is not None and rev:
            out["fcf_margin"] = fcf / rev * 100
        if ocf is not None and ni and ni > 0:
            out["cash_conv"] = min(3.0, ocf / ni)
        sh1, rev1, ni1 = g("sh", k1), g("rev", k1), g("ni", k1)
        if sh and sh1:
            out["dilution"] = (sh / sh1 - 1) * 100
        if rev and rev1 and rev1 > 0:
            out["rev_yoy"] = (rev / rev1 - 1) * 100
        if ni is not None and ni1 and ni1 > 0:
            out["ni_g"] = max(-100.0, min(300.0, (ni / ni1 - 1) * 100))
        shares = s.sh_now * (sh / s.sh_last) if s.sh_now and sh and s.sh_last else s.sh_now
        k_fx = 1.0                                   # jaarcijfers naar de valuta van de koers
        if s.fcur != s.cur:
            f = fx.get(m)
            k_fx = None if s.fcur not in ("USD", "EUR") or not f else (f if s.fcur == "EUR" else 1 / f)
        if shares and k_fx:
            mc = c * shares
            out["_mc"] = mc
            if fcf is not None:
                out["fcf_yield"] = fcf * k_fx / mc * 100
            eb, debt, cash = g("ebitda"), g("debt") or 0, g("cash") or 0
            if eb and eb > 0:
                out["ev_ebitda"] = (mc + (debt - cash) * k_fx) / (eb * k_fx)
            if ni is not None:
                out["pe"] = mc / (ni * k_fx) if ni > 0 else 9999.0     # verlies = slechtste waardering (zoals de marktrang)
    c12 = s.px(m - 12)
    if c12:
        out["chg12"] = (c / c12 - 1) * 100
        win = [v for v in (s.px(j) for j in range(m - 11, m + 1)) if v]
        if len(win) >= 10:
            out["near_high"] = c / max(win) * 100
        last10 = [s.px(j) for j in range(m - 9, m + 1)]
        if all(last10):
            out["above"] = 100.0 if c > sum(last10) / 10 else 0.0       # boven het 10-maands gemiddelde (±200 dagen)
    return out


def percentielen(rows, key, higher):
    """Percentiel (0-100) binnen de sector, of binnen de hele markt als de sector te klein is: {rijnummer: percentiel}."""
    vals = [(i, r[key]) for i, r in enumerate(rows) if key in r]
    groups = defaultdict(list)
    for i, v in vals:
        groups[rows[i]["_sec"]].append(v)
    groups = {g: sorted(v) for g, v in groups.items()}
    allv = sorted(v for _, v in vals)
    out = {}
    for i, v in vals:
        g = groups[rows[i]["_sec"]]
        ref = g if len(g) >= MIN_PEERS else allv
        lo, hi = bisect_left(ref, v), bisect_right(ref, v)
        p = (lo + 0.5 * (hi - lo)) / len(ref) * 100
        out[i] = p if higher else 100 - p
    return out


def rangen(xs):
    """Rangnummers (gelijke waarden delen hun gemiddelde rang)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def correlatie(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va and vb else None


def meet(xs, ys):
    """Samenhang tussen rang en rendement (rangcorrelatie) en het verschil tussen het beste en slechtste vijfde deel."""
    ic = correlatie(rangen(xs), rangen(ys))
    order = sorted(range(len(xs)), key=lambda j: xs[j])
    q = len(xs) // 5
    spread = sum(ys[j] for j in order[-q:]) / q - sum(ys[j] for j in order[:q]) / q if q else None
    return ic, spread


def neutraal(ret, sector_of):
    """Rendement t.o.v. het gemiddelde van de eigen sector: {sleutel: rendement - sectorgemiddelde}."""
    sec = defaultdict(list)
    for i, x in ret.items():
        sec[sector_of(i)].append(x)
    avg = {k: sum(v) / len(v) for k, v in sec.items()}
    return {i: x - avg[sector_of(i)] for i, x in ret.items()}


def terugrekenen(cache, ref_pm):
    """Per pijler en horizon de maandmetingen: {(pijler, h): {maand: (ic, spread, n, ic_groot)}} plus het bereik.
    ic_groot = dezelfde meting bij alleen de bedrijven die toen minstens BIG x de grens van het universum waard waren."""
    fx, sp = reeks((ref_pm or {}).get("fx")), reeks((ref_pm or {}).get("sp"))
    S = aandelen(cache)
    if not fx or not S:
        return {"res": {}, "sp": sp, "n": len(S), "last": None}
    last = max(fx)                                    # laatste afgesloten maand
    res = defaultdict(dict)
    for m in range(min(s.m0 for s in S) + 12, last):
        rows = []
        for s in S:
            x = kenmerken(s, m, fx)
            if not x:
                continue
            mc = x.get("_mc")
            if mc is not None and mc < CAP[s.region]:
                continue                              # toen te klein voor het universum
            x["_sec"], x["_s"], x["_big"] = s.sector, s, mc is not None and mc >= BIG * CAP[s.region]
            rows.append(x)
        if len(rows) < MIN_ROWS:
            continue
        pil = {}
        for p, ms in PIL.items():
            per = [percentielen(rows, k, h) for k, h in ms]
            vals = {}
            for i in range(len(rows)):
                vs = [d[i] for d in per if i in d]
                if p == "M" and "above" in rows[i]:
                    vs.append(rows[i]["above"])
                if vs and (p != "Q" or len(vs) >= 3):
                    vals[i] = sum(vs) / len(vs)
            pil[p] = vals
        for h in HS:
            if m + h > last:
                continue
            ret = {}
            for i, r in enumerate(rows):
                s = r["_s"]
                a0, a1 = s.px(m, True), s.px(m + h, True)
                if not a0 or not a1:
                    continue
                x = a1 / a0 - 1
                if s.region == "VS" and fx.get(m) and fx.get(m + h):
                    x = (1 + x) * fx[m] / fx[m + h] - 1                 # dollarrendement naar euro
                ret[i] = x * 100
            rel = neutraal(ret, lambda i: rows[i]["_sec"])
            for p, vals in pil.items():
                idx = [i for i in vals if i in rel]
                if len(idx) < MIN_N:
                    continue
                ic, spread = meet([vals[i] for i in idx], [rel[i] for i in idx])
                big = [i for i in idx if rows[i]["_big"]]
                icb = meet([vals[i] for i in big], [rel[i] for i in big])[0] if len(big) >= MIN_N else None
                if ic is not None:
                    res[(p, h)][m] = (ic, spread, len(idx), icb)
    return {"res": dict(res), "sp": sp, "n": len(S), "last": last}


def gemiddelde(xs, lag=0):
    """(gemiddelde, t-waarde, aantal). De standaardfout houdt rekening met overlap van de vensters (Newey-West, lag)."""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None), None, n
    mu = sum(xs) / n
    g0 = sum((x - mu) ** 2 for x in xs) / n
    v = g0
    for L in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - L / (lag + 1)) * sum((xs[t] - mu) * (xs[t - L] - mu) for t in range(L, n)) / n
    v = max(v, g0)                                    # overlap maakt een meting nooit nauwkeuriger
    se = math.sqrt(v / n)
    return mu, (mu / se if se else None), n


def samenvatting(R, live=None):
    """Per pijler wat de metingen samen zeggen. live = {(pijler, h): {maand: (ic, spread, n)}} uit de vastgelegde
    maandstanden: waar die er zijn, gaan ze vóór de teruggerekende maand."""
    res, sp = R.get("res") or {}, R.get("sp") or {}
    r1 = lambda x, d=1: None if x is None else round(x, d)  # noqa: E731
    out = {}
    for p, _ in PIJLERS:
        e = {}
        for h in HS:
            d = dict(res.get((p, h)) or {})
            lv = (live or {}).get((p, h)) or {}
            d.update({m: (v[0], v[1], v[2], None) for m, v in lv.items()})
            ms = sorted(d)
            if not ms:
                continue
            ics, sps = [d[m][0] for m in ms], [d[m][1] for m in ms if d[m][1] is not None]
            mu, t, n = gemiddelde(ics, h - 1)
            big = [d[m][3] for m in ms if d[m][3] is not None]
            mub, tb, _ = gemiddelde(big, h - 1) if big else (None, None, 0)
            e[h] = {"n": n, "van": ym(ms[0]), "tot": ym(ms[-1]), "ic": r1(mu, 3), "t": r1(t), "pos": r1(100 * sum(1 for x in ics if x > 0) / n, 0),
                    "spread": r1(sum(sps) / len(sps), 2) if sps else None,
                    "spread_pos": r1(100 * sum(1 for x in sps if x > 0) / len(sps), 0) if sps else None,
                    "ic_groot": r1(mub, 3), "t_groot": r1(tb), "live": sum(1 for m in ms if m in lv)}
            if h == 1:                                # werkte het ook in maanden dat de beurs daalde?
                up = [d[m][0] for m in ms if sp.get(m) and sp.get(m + 1) and sp[m + 1] > sp[m]]
                dn = [d[m][0] for m in ms if sp.get(m) and sp.get(m + 1) and sp[m + 1] <= sp[m]]
                e["op"] = {"n": len(up), "ic": r1(sum(up) / len(up), 3) if up else None}
                e["neer"] = {"n": len(dn), "ic": r1(sum(dn) / len(dn), 3) if dn else None}
        out[p] = e
    return out


def steun(e, was=0):
    """+1 = duidelijk bewijs dat de pijler helpt, -1 = dat hij tegenwerkt, 0 = niet duidelijk. 'was' = de vorige uitkomst:
    eenmaal toegekend vervalt steun pas als het bewijs onder T_OFF zakt (geen heen-en-weer)."""
    h, op, neer = e.get(H_MAIN) or {}, e.get("op") or {}, e.get("neer") or {}
    if (h.get("n") or 0) < MIN_MONTHS or h.get("t") is None or (op.get("n") or 0) < MIN_SIDE or (neer.get("n") or 0) < MIN_SIDE:
        return 0
    tg = h.get("t_groot")
    for sign in (1, -1):
        grens, kant = (T_OFF, SIDE_OFF) if was == sign else (T_ON, SIDE_ON)
        if (sign * h["t"] >= grens and (tg is None or sign * tg >= grens)
                and sign * (op.get("ic") or 0) >= kant and sign * (neer.get("ic") or 0) >= kant):
            return sign
    return 0


def gewichten(base, S, prev=None):
    """De gewichten van de marktrang: het vaste gewicht keer 1,25 (duidelijk bewijs), 0,75 (werkt tegen) of 1, daarna
    weer samen 100%. Geeft (gewichten, steun per pijler). prev = de steun van de vorige keer (voor de hysterese)."""
    st = {p: steun(S.get(p) or {}, (prev or {}).get(p, 0)) for p in base}
    raw = {p: base[p] * (MULT_UP if st[p] > 0 else MULT_DOWN if st[p] < 0 else 1.0) for p in base}
    tot = sum(raw.values())
    return {p: round(v / tot, 4) for p, v in raw.items()}, st


def oordeel(p, e, st):
    """Eén zin per pijler voor op de pagina."""
    h = e.get(H_MAIN) or {}
    if not h:
        return "Niet terug te rekenen; de meting loopt sinds de maandstanden worden vastgelegd." if p == "F" else "Nog geen meting."
    if h["n"] < MIN_MONTHS:
        return f"Nog te weinig maanden ({h['n']} van de {MIN_MONTHS}) om er gewicht aan te hangen."
    op, neer = (e.get("op") or {}).get("ic"), (e.get("neer") or {}).get("ic")
    if st > 0:
        return "Hielp duidelijk, ook in maanden dat de beurs daalde: telt zwaarder."
    if st < 0:
        return "Werkte tegen, in stijgende en in dalende maanden: telt lichter."
    if h.get("t") is not None and abs(h["t"]) < T_ON:
        if op is not None and neer is not None and op < 0 < neer:
            return "Geen duidelijk voordeel over een half jaar; hielp wel in maanden dat de beurs daalde. Gewicht blijft."
        if op is not None and neer is not None and neer < 0 < op:
            return "Geen duidelijk voordeel over een half jaar; hielp alleen in stijgende maanden. Gewicht blijft."
        return "Geen duidelijk voordeel of nadeel. Gewicht blijft."
    if op is not None and neer is not None and (op > 0) != (neer > 0):
        return ("Deed het goed in stijgende maanden, maar niet in dalende: gewicht blijft." if op > 0
                else "Deed het goed in dalende maanden, maar niet in stijgende: gewicht blijft.")
    return "Bewijs niet eenduidig tussen de twee groepen bedrijven. Gewicht blijft."


# ── Live: de maandstand vastleggen en volgen ─────────────────────────────
COLS = ["sym", "region", "sector", "px", "F", "Q", "G", "V", "M", "card", "val", "koop", "gap", "surv"]
VAL = ["gunstig", "normaal", "duur", "zeer duur"]
KOOP = ["zone", "bijna"]
KEEP = 14                    # zoveel maandstanden bewaren (12 maanden volgen plus speling)
DAYS = {1: 30, 6: 182, 12: 365}


def stand(items, month, date, fx):
    """De maandstand van alle bedrijven: alleen wat nodig is om later te meten (compact, één rij per bedrijf)."""
    secs = sorted({it.get("sector") or "?" for it in items})
    rows = []
    for it in items:
        pil, card, s12 = it.get("pillars") or {}, it.get("card") or {}, it.get("s12") or {}
        val, koop = (it.get("val") or {}).get("lvl"), (it.get("koop") or {}).get("status")
        if not it.get("native"):
            continue
        rows.append([it["sym"], 0 if it.get("region") == "VS" else 1, secs.index(it.get("sector") or "?"), it["native"]]
                    + [None if pil.get(k) is None else round(pil[k], 1) for k in "FQGVM"]
                    + [card.get("s"), VAL.index(val) if val in VAL else None, KOOP.index(koop) if koop in KOOP else None,
                       None if not it.get("gap") else int(bool(it["gap"].get("ok"))), int(bool(s12.get("survivor")))])
    return {"month": month, "date": date, "fx": round(fx, 4), "sectors": secs, "cols": COLS, "rows": rows}


def groepen(rows):
    """De groepen die we live volgen (naast de pijlers): sleutel -> (naam, test op een rij)."""
    c = {k: i for i, k in enumerate(COLS)}
    return [
        ("card100", "Scorecard 100+", lambda r: r[c["card"]] is not None and r[c["card"]] >= 100),
        ("card75", "Scorecard 75–100", lambda r: r[c["card"]] is not None and 75 <= r[c["card"]] < 100),
        ("card0", "Scorecard onder 75", lambda r: r[c["card"]] is not None and r[c["card"]] < 75),
        ("koop", "Koopmoment (in de zone)", lambda r: r[c["koop"]] == 0),
        ("surv", "Survivor (alle 12 stappen)", lambda r: r[c["surv"]] == 1),
        ("gap", "Find the gap", lambda r: r[c["gap"]] == 1 and r[c["card"]] is not None and r[c["card"]] >= 90),
        ("duur", "Zeer duur", lambda r: r[c["val"]] == 3),
        ("gunstig", "Gunstig gewaardeerd", lambda r: r[c["val"]] == 0),
    ]


def volg(coh, px_now, fx_now):
    """Wat deed deze maandstand sindsdien? Rendement per bedrijf in euro; per pijler de samenhang en het verschil tussen het
    beste en slechtste vijfde deel (t.o.v. de sector), per groep het gemiddelde t.o.v. alle bedrijven van die stand."""
    c = {k: i for i, k in enumerate(COLS)}
    ret = {}
    for i, r in enumerate(coh["rows"]):
        p1 = px_now(r[c["sym"]])
        if not p1 or not r[c["px"]]:
            continue
        x = p1 / r[c["px"]] - 1
        if r[c["region"]] == 0 and fx_now and coh.get("fx"):
            x = (1 + x) * coh["fx"] / fx_now - 1      # dollarrendement naar euro
        ret[i] = x * 100
    if len(ret) < MIN_N:
        return None
    rel = neutraal(ret, lambda i: coh["rows"][i][c["sector"]])
    out = {"n": len(ret), "gem": round(sum(ret.values()) / len(ret), 2), "pijlers": {}, "groepen": {}}
    for p in "FQGVM":
        idx = [i for i in rel if coh["rows"][i][c[p]] is not None]
        if len(idx) >= MIN_N:
            ic, spread = meet([coh["rows"][i][c[p]] for i in idx], [rel[i] for i in idx])
            if ic is not None:
                out["pijlers"][p] = [round(ic, 3), round(spread, 2), len(idx)]
    for key, _, test in groepen(coh["rows"]):
        xs = [ret[i] for i in ret if test(coh["rows"][i])]
        if xs:
            out["groepen"][key] = [len(xs), round(sum(xs) / len(xs), 2), round(100 * sum(1 for x in xs if x > out["gem"]) / len(xs))]
    return out


def live_bijwerken(log, prev_live, px_now, fx_now, today):
    """Alle maandstanden volgen: de stand van nu ('nu') en vastgezet zodra 1, 6 en 12 maanden om zijn.
    prev_live = wat de vorige run bewaarde ({maand: {"nu":…, "1":…, "6":…, "12":…}}): vastgezette uitslagen blijven staan."""
    from datetime import date as _date
    out = {}
    for coh in log:
        days = (today - _date.fromisoformat(coh["date"])).days
        e = dict((prev_live or {}).get(coh["month"]) or {})
        e["date"], e["days"] = coh["date"], days
        if days >= 7:
            now = volg(coh, px_now, fx_now)
            if now:
                e["nu"] = now
                for h, d in DAYS.items():
                    if days >= d and str(h) not in e:
                        e[str(h)] = now                # vastzetten bij de eerste run na de termijn
        out[coh["month"]] = e
    return out


def live_reeks(live):
    """De vastgezette live-uitslagen als maandmetingen per pijler: {(pijler, h): {maand: (ic, spread, n)}}.
    Een stand van begin november hoort bij maandeinde oktober."""
    out = defaultdict(dict)
    for month, e in (live or {}).items():
        m = mno(month + "-01") - 1
        for h in HS:
            for p, v in ((e.get(str(h)) or {}).get("pijlers") or {}).items():
                out[(p, h)][m] = tuple(v)
    return dict(out)
