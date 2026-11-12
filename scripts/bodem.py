#!/usr/bin/env python3
"""
Bodemsignaal — schrijft bodem.json: is het dal van een dip in de Nasdaq-100 waarschijnlijk bereikt, en wanneer weten we dat?

Waarom: de techradar (tech.py) waarschuwt vóór een terugval. Dit is de andere kant: staat de Nasdaq-100 5% of meer onder
zijn 52-weekse top, dan is de vraag of het dal er al is.

Wat de historie laat zien (nagerekend op alle dalen sinds 1986, elke run opnieuw):
  * Het sterkste houvast is hoe lang het dal al standhoudt en hoe diep de dip is. Elke dag zonder lager slot maakt de kans
    groter dat het dal bereikt is; in een berenmarkt (20% of meer onder de top) is die kans veel kleiner dan in een
    gewone correctie. Daaruit volgt de kans bij de huidige stand, en vanaf welke datum het dal 'waarschijnlijk bereikt' is
    als het blijft standhouden.
  * De termijnstructuur van de angstindex (VIX voor 1 maand tegenover VIX3M voor 3 maanden): staat de korte boven de
    lange, dan is er paniek; zakt hij er weer onder, dan geldt dat op de beurs als bodemsignaal. Nagerekend sinds 2006
    was het dal op dat moment in ruim de helft van de gevallen bereikt: iets vaker dan bij een even oud dal zonder dat
    signaal, maar geen zekerheid. Het signaal staat daarom apart, met zijn eigen trefkans, en telt niet mee in de kans.
  * Andere bekende bodemsignalen (angstpiek voorbij, RSI terug uit uitverkocht, weer boven het 20-daags gemiddelde)
    voegden niets toe aan de leeftijd van het dal en zitten er daarom niet in.

'Het dal is bereikt' = de Nasdaq-100 sloot daarna niet meer lager dan dat dal tot hij terug was op zijn 52-weekse top.
Per dal telt één keer (de dag dat het die leeftijd bereikt), zodat een lange daling niet zwaarder weegt dan een korte.
Een uitkomst op dag X gebruikt alleen koersen t/m dag X; de kansen komen uit dalen waarvan de afloop bekend is.

Alleen standaardbibliotheek; ophalen en rekenhulpen komen uit tech.py. Draait in de lus van de Patroonradar, hooguit
eens per FRESH_MIN minuten.
"""
import json
import os
import sys
import time
import traceback
from collections import deque
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tech as T  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "bodem.json")
FRESH_MIN = 55               # niet vaker opnieuw rekenen dan eens per zoveel minuten
VERSION = 1                  # nieuwe versie van dit script = meteen opnieuw rekenen
BUDGET_S = 150               # na zoveel seconden ophalen de overige bronnen overslaan
MIN_HIST = 2500              # termijnstructuur met minder dagen historie (±10 jaar): geen trefkans noemen
MIN_N = 10                   # minder vergelijkbare dalen: geen percentage noemen
SRC = {"ndx": ("^NDX", "Nasdaq-100 (^NDX)"), "vix": ("^VIX", "Angstindex 1 maand (^VIX)"),
       "v3m": ("^VIX3M", "Angstindex 3 maanden (^VIX3M)")}

# Vaste, ronde grenzen
DIP = 5.0                    # dip: 5% of meer onder de 52-weekse top (zelfde grens als 'terugval' in de techradar)
H = 60                       # tweede maatstaf: geen lager slot in de 60 handelsdagen (±3 maanden) erna
AGES = [1, 2, 3, 5, 7, 10, 15, 20, 30, 40]     # leeftijd van het dal in handelsdagen
KLASSEN = [                  # sleutel, naam, van, tot (% onder de top, het dal)
    ("terugval", "terugval (5–10%)", 5.0, 10.0),
    ("correctie", "correctie (10–20%)", 10.0, 20.0),
    ("berenmarkt", "berenmarkt (20% of meer)", 20.0, 101.0),
]
P_MAAK, P_OK = 50.0, 70.0    # vanaf 50%: bodem in de maak; vanaf 70%: dal waarschijnlijk bereikt
INV_WIN, INV_MIN, NORM_DAYS = 15, 2, 2   # termijnstructuur: >= 2 dagen omgekeerd in 15 handelsdagen, nu 2 dagen normaal
LEVELS = {                   # sleutel: (naam, tekst)
    "geen_dip": ("Geen dip", "De Nasdaq-100 staat binnen 5% van zijn 52-weekse top."),
    "loopt": ("Daling loopt nog", "Vandaag een nieuw dal: de daling is nog bezig."),
    "geen": ("Nog geen bodem", "Het dal is nog te vers: vaker wel dan niet volgde nog een lager dal."),
    "maak": ("Bodem in de maak", "Het dal houdt stand, maar is nog niet bevestigd."),
    "bereikt": ("Dal waarschijnlijk bereikt", "Het dal houdt lang genoeg stand: meestal bleek dit het dal van de dip."),
    "onbekend": ("Te weinig vergelijkbare dalen", "Voor deze stand zijn er te weinig vergelijkbare dalen om een kans te noemen."),
}


def klasse_of(depth):
    """Klasse van een dip naar de diepte van het dal (positief getal: % onder de top); None onder de 5%."""
    return next((k for k, _, lo, hi in KLASSEN if lo <= depth < hi), None)


def level_of(p):
    return "onbekend" if p is None else "bereikt" if p >= P_OK else "maak" if p >= P_MAAK else "geen"


# ── Rekenhulpen (dag i gebruikt alleen dag 0..i) ─────────────────────────
def dal_reeks(xs, n=252):
    """Per dag: (index van de 52-weekse top, index van het laagste slot sinds die top). De top = het hoogste slot van de
    laatste n dagen (bij gelijke stand de laatste dag); staat de koers op zijn top, dan is het dal die dag zelf."""
    top, low, q, lo, prev = [0] * len(xs), [0] * len(xs), deque(), 0, None
    for i, x in enumerate(xs):
        while q and xs[q[-1]] <= x:
            q.pop()
        q.append(i)
        while q[0] <= i - n:
            q.popleft()
        t = q[0]
        if t != prev:                 # nieuwe top (hoger slot, of de oude viel uit het venster): dal opnieuw bepalen
            lo, prev = t, t
            for j in range(t + 1, i + 1):
                if xs[j] <= xs[lo]:
                    lo = j
        elif x <= xs[lo]:
            lo = i
        top[i], low[i] = t, lo
    return top, low


def afloop(xs, top, low):
    """Per dag de afloop van het dal van dat moment (alleen de uitkomst, om achteraf te tellen):
    was = True als de koers daarna terugkwam op de top van toen zonder lager slot, False bij een lager slot, None als
    dat nog niet beslist is. h60 = geen lager slot in de H handelsdagen erna (None als die nog niet om zijn)."""
    n, was, h60, memo = len(xs), [None] * len(xs), [None] * len(xs), {}
    q = deque()                                      # voortschrijdend minimum over i+1..i+H
    for i in range(n - 1, -1, -1):
        while q and q[0] > i + H:
            q.popleft()
        if i + H < n:
            h60[i] = xs[q[0]] >= xs[low[i]]
        while q and xs[q[-1]] >= xs[i]:
            q.pop()
        q.append(i)
    for i in range(n):
        key = (top[i], low[i])
        if key not in memo:
            lo, hi, res = xs[low[i]], xs[top[i]], None
            for j in range(low[i] + 1, n):
                if xs[j] < lo:
                    res = False
                    break
                if xs[j] >= hi:
                    res = True
                    break
            memo[key] = res
        was[i] = memo[key]
    return was, h60


def dalen(low, dd_low, age, start=252):
    """Alle dalen van 5% of meer onder de top die 'age' handelsdagen standhielden: per dal de dag waarop het die
    leeftijd bereikte (één keer per dal)."""
    seen, out = set(), []
    for i in range(start, len(low)):
        if dd_low[i] <= -DIP and i - low[i] == age and low[i] not in seen:
            seen.add(low[i])
            out.append(i)
    return out


def pct(xs):
    xs = [v for v in xs if v is not None]
    return (T.r(100 * sum(1 for v in xs if v) / len(xs)), len(xs)) if xs else (None, 0)


def median(xs):
    xs = sorted(xs)
    k = len(xs)
    return None if not k else xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2


def quart(xs, q):
    """Kwartiel (lineair) van een lijst; None als die leeg is."""
    xs = sorted(xs)
    if not xs:
        return None
    p = (len(xs) - 1) * q
    a = int(p)
    return xs[a] + (xs[min(a + 1, len(xs) - 1)] - xs[a]) * (p - a)


def werkdag(d, k):
    """De datum k werkdagen na d (weekenden overgeslagen; beursvrije dagen kennen we niet vooruit, dus 'rond' die datum)."""
    while k > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            k -= 1
    return d


def termijn(ratio):
    """Per dag: is de termijnstructuur 'weer normaal' (de laatste NORM_DAYS slotkoersen VIX onder VIX3M, in de INV_WIN
    handelsdagen daarvoor minstens INV_MIN keer erboven)? None zonder (genoeg) data."""
    out = [None] * len(ratio)
    for i in range(INV_WIN + NORM_DAYS, len(ratio)):
        nu = ratio[i - NORM_DAYS + 1:i + 1]
        eerder = [v for v in ratio[i - NORM_DAYS - INV_WIN + 1:i - NORM_DAYS + 1] if v is not None]
        if all(v is not None for v in nu) and len(eerder) >= INV_WIN - INV_WIN // 5:
            out[i] = all(v < 1.0 for v in nu) and sum(1 for v in eerder if v > 1.0) >= INV_MIN
    return out


# ── Alles op een rij ─────────────────────────────────────────────────────
def reeks(rows):
    """Alle dagreeksen op de kalender van de Nasdaq-100."""
    dates = [d for d, _ in rows["ndx"]]
    ndx = [c for _, c in rows["ndx"]]
    n = len(ndx)
    vix, v3m = (T.align(dates, rows.get(k) or []) for k in ("vix", "v3m"))
    top, low = dal_reeks(ndx)
    was, h60 = afloop(ndx, top, low)
    ratio = [vix[i] / v3m[i] if vix[i] and v3m[i] else None for i in range(n)]
    return {"dates": dates, "ndx": ndx, "vix": vix, "v3m": v3m, "top": top, "low": low, "was": was, "h60": h60,
            "dd": [(ndx[i] / ndx[top[i]] - 1) * 100 for i in range(n)],          # nu t.o.v. de 52-weekse top
            "dd_low": [(ndx[low[i]] / ndx[top[i]] - 1) * 100 for i in range(n)],  # het dal t.o.v. die top
            "ratio": ratio, "term": termijn(ratio)}


def oplopend(ps, ws):
    """Reeks laten oplopen (isotone regressie, gewogen): waar een latere waarde lager uitvalt dan een eerdere, nemen we
    die buren samen tot hun gewogen gemiddelde. Zo zakt een kans niet door toeval in een kleine groep."""
    blocks = []                                      # [waarde, gewicht, aantal]
    for p, w in zip(ps, ws):
        blocks.append([p, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, l2 = blocks.pop()
            v1, w1, l1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, l1 + l2])
    return [v for v, _, k in blocks for _ in range(k)]


def kans_tabel(R):
    """Per klasse (diepte van het dal) en leeftijd: hoe vaak bleek dit het dal (en hield het 60 dagen stand)?
    'p' loopt op met de leeftijd (zie oplopend); 'ruw' is het getelde percentage van die ene groep."""
    out = {}
    for age in AGES:
        ds = dalen(R["low"], R["dd_low"], age)
        for k, _, lo, hi in KLASSEN:
            part = [i for i in ds if lo <= -R["dd_low"][i] < hi]
            p, n = pct([R["was"][i] for i in part])
            p60, n60 = pct([R["h60"][i] for i in part])
            out[(k, age)] = {"age": age, "p": p, "ruw": p, "n": n, "p60": p60, "n60": n60}
    for k, _, _, _ in KLASSEN:
        cells = [out[(k, a)] for a in AGES if out[(k, a)]["n"]]
        for c, v in zip(cells, oplopend([c["ruw"] for c in cells], [c["n"] for c in cells])):
            c["p"] = T.r(v)
    return out


def build(rows, errors):
    R = reeks(rows)
    dates, ndx, top, low, dd, dd_low, ratio, term = (R[k] for k in ("dates", "ndx", "top", "low", "dd", "dd_low", "ratio", "term"))
    n, last = len(ndx), len(ndx) - 1
    iso = lambda i: dates[i].isoformat()  # noqa: E731
    errors = list(errors)
    tab = kans_tabel(R)

    # 1. nu: stand van de dip, klasse en leeftijd van het dal, de kans daarbij en het pad als het dal blijft standhouden
    depth, age = -dd_low[last], last - low[last]
    klasse = klasse_of(depth)
    state = "geen" if klasse is None else "dip" if dd[last] <= -DIP else "herstel"
    kans, pad = None, []
    if klasse is None:
        lk = "geen_dip"
    elif age == 0:
        lk = "loopt"
    else:
        cell = tab[(klasse, max(a for a in AGES if a <= age))]
        ok = cell["n"] >= MIN_N
        kans = {"age": cell["age"], "p": cell["p"] if ok else None, "n": cell["n"], "p60": cell["p60"] if cell["n60"] >= MIN_N else None}
        lk = level_of(kans["p"])
    if klasse is not None:
        for a in AGES:                                   # wat als het dal blijft standhouden?
            c = tab[(klasse, a)]
            if a > age and c["n"] >= MIN_N:
                pad.append({"age": a, "p": c["p"], "n": c["n"], "date": werkdag(dates[low[last]], a).isoformat(), "k": level_of(c["p"])})
    naam, text = LEVELS[lk]
    nxt = next((x for x in pad if x["k"] == "bereikt"), None) if lk in ("loopt", "geen", "maak") else None
    level = {"k": lk, "label": naam, "text": text, "kans": kans,
             "wanneer": {"date": nxt["date"], "age": nxt["age"], "p": nxt["p"]} if nxt else None}

    # 2. termijnstructuur: stand nu, de laatste omkering en de trefkans sinds de eerste meting
    nd = sum(1 for v in term if v is not None)
    t0 = next((i for i in range(n) if term[i] is not None), n)
    inv_days = [i for i in range(n) if ratio[i] is not None and ratio[i] > 1.0]
    inv = None
    if inv_days:
        k = len(inv_days) - 1
        while k > 0 and inv_days[k] - inv_days[k - 1] <= INV_WIN:        # de laatste omkering (dagen dicht bij elkaar)
            k -= 1
        inv = {"on": inv_days[-1] == last, "from": iso(inv_days[k]), "to": iso(inv_days[-1]), "days": len(inv_days) - k,
               "max": T.r(max(ratio[i] for i in inv_days[k:]), 2)}
    j = last
    while term[last] and j > 0 and term[j - 1]:
        j -= 1
    tstat = None
    if nd >= MIN_HIST:
        # de dag dat de termijnstructuur weer normaal werd, één keer per dal (in een dip, niet op de dag van een nieuw dal)
        seen, ev = set(), []
        for i in range(max(t0, 252) + 1, n):
            if term[i] and not term[i - 1] and dd_low[i] <= -DIP and i > low[i] and low[i] not in seen:
                seen.add(low[i])
                ev.append(i)
        base = {}                                        # even oud dal, zelfde periode, zonder naar het signaal te kijken
        for a in sorted({min(i - low[i], AGES[-1]) for i in ev}):
            ds = [d for d in dalen(low, dd_low, a, max(t0, 252))]
            base[a] = (pct([R["was"][d] for d in ds])[0], pct([R["h60"][d] for d in ds])[0])
        p, k_ = pct([R["was"][i] for i in ev])
        p60, k60 = pct([R["h60"][i] for i in ev])
        b = [base[min(i - low[i], AGES[-1])][0] for i in ev if R["was"][i] is not None]
        b60 = [base[min(i - low[i], AGES[-1])][1] for i in ev if R["h60"][i] is not None]
        b, b60 = [v for v in b if v is not None], [v for v in b60 if v is not None]
        tstat = {"from": iso(t0), "n": k_, "goed": sum(1 for i in ev if R["was"][i]), "p": p,
                 "base": T.r(sum(b) / len(b)) if b else None, "n60": k60, "p60": p60,
                 "base60": T.r(sum(b60) / len(b60)) if b60 else None,
                 "mis": sorted({dates[i].year for i in ev if R["was"][i] is False})}
    elif rows.get("v3m"):
        errors.append(f"{SRC['v3m'][1]}: te korte historie ({nd} dagen): geen trefkans voor de termijnstructuur")
    vix, v3m = R["vix"], R["v3m"]
    term_now = {"vix": T.r(vix[last], 2), "v3m": T.r(v3m[last], 2), "ratio": T.r(ratio[last], 3),
                "state": None if ratio[last] is None else "omgekeerd" if ratio[last] > 1.0 else "normaal",
                "on": term[last], "since": iso(j) if term[last] else None,
                "dal_erna": bool(term[last] and low[last] >= j),     # na het signaal kwam nog een lager slot
                "inv": inv, "stat": tstat}

    # 3. correcties >= 10% (sinds de termijnstructuur gemeten wordt): top, dal, de valse bodems onderweg (dalen die 10
    #    handelsdagen standhielden en toch braken) en wanneer de termijnstructuur weer normaal werd t.o.v. het dal
    dips = []
    for pk, tr, h10, end in T.correcties(ndx):
        if pk < max(t0, 252):
            continue
        stop = end if end is not None else last
        f = next((i for i in range(pk + 1, stop + 1) if term[i] and not term[i - 1] and i > low[i]), None)
        vals, seen = 0, set()
        for i in range(pk + 1, tr):
            if i - low[i] == 10 and low[i] not in seen and low[i] > pk:
                seen.add(low[i])
                vals += 1
        e = {"peak": iso(pk), "trough": iso(tr), "depth": T.r((ndx[tr] / ndx[pk] - 1) * 100), "days": tr - pk,
             "end": iso(end) if end is not None else None, "top": dd[pk] > -DIP, "vals": vals,
             "sig": iso(f) if f is not None else None}
        if f is not None:
            lo = min(ndx[pk:f + 1])
            e.update({"na_dal": f - tr,                  # handelsdagen na het uiteindelijke dal (negatief = ervoor)
                      "boven_dal": T.r((ndx[f] / lo - 1) * 100),
                      "lager": T.r(min(0.0, (ndx[tr] / lo - 1) * 100))})   # zoveel zakte het daarna nog onder het dal van toen
        dips.append(e)

    # 4. hoe lang duurden correcties van top tot dal (handelsdagen), sinds 1985
    alle = [(tr - pk, (ndx[tr] / ndx[pk] - 1) * 100) for pk, tr, _, end in T.correcties(ndx) if end is not None]
    typ = []
    for key, _, lo_, hi_ in KLASSEN[1:]:
        ds = [d for d, dp in alle if lo_ <= -dp < hi_]
        typ.append({"k": key, "n": len(ds), "med": median(ds), "q1": T.r(quart(ds, 0.25), 0), "q3": T.r(quart(ds, 0.75), 0)})

    sp = max(0, n - 90)
    return {
        "version": VERSION,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": iso(last),
        "sources": {k: f"{rows[k][0][0].year}–nu" for k in ("ndx", "vix", "v3m") if rows.get(k)},
        "source_errors": errors,
        "horizon": {"dip": DIP, "h": H, "min_n": MIN_N, "p_maak": P_MAAK, "p_ok": P_OK, "from": iso(252)},
        "dip": {"state": state, "klasse": klasse, "status": T.status_of(dd[last]), "dd": T.r(dd[last]),
                "low_dd": T.r(dd_low[last]), "top": iso(top[last]), "low": iso(low[last]),
                "days_top": last - top[last], "days_low": age, "top_low": low[last] - top[last],
                "from_low": T.r((ndx[last] / ndx[low[last]] - 1) * 100), "close": T.r(ndx[last], 2)},
        "level": level, "pad": pad,
        "tabel": [{"k": k, "label": lab, "rows": [tab[(k, a)] for a in AGES]} for k, lab, _, _ in KLASSEN],
        "term": term_now,
        "dips": dips[-12:], "typ": typ,
        "spark": {"start": iso(sp), "ndx": [round(c) for c in ndx[sp:]],
                  "spread": [T.r(v3m[i] - vix[i], 2) if vix[i] and v3m[i] else None for i in range(sp, n)],
                  "low": low[last] - sp if low[last] >= sp else None},
    }


def run(force=False):
    prev = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:  # noqa: BLE001
        pass
    prev = prev if isinstance(prev, dict) else {}
    age = T.age_min(prev.get("updated"))
    if age is not None and age < FRESH_MIN and prev.get("version") == VERSION and not force:
        print(f"bodem.json is {age:.0f} min oud: niets te doen")
        return

    t0 = time.monotonic()
    rows = {"ndx": T.yahoo_daily("^NDX")}                   # zonder Nasdaq-100 geen bodemsignaal
    if len(rows["ndx"]) < MIN_HIST + 260:                   # afgekapt antwoord: oude bodem.json laten staan
        raise RuntimeError(f"Nasdaq-100: te korte historie ({len(rows['ndx'])} dagen)")
    print(f"Nasdaq-100: {len(rows['ndx'])} dagen, {rows['ndx'][0][0]} t/m {rows['ndx'][-1][0]}")
    errors = []
    for key in ("vix", "v3m"):
        sym, name = SRC[key]
        if time.monotonic() - t0 > BUDGET_S:
            errors.append(f"{name}: overgeslagen (tijdslimiet)")
            continue
        try:
            rows[key] = T.yahoo_daily(sym, date(1990, 1, 1))
            print(f"  {name}: ok ({len(rows[key])})")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:160]}")
            print(f"  {name}: bron mislukt ({e})")

    out = build(rows, errors)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, OUT)
    L, d = out["level"], out["dip"]
    print(f"bodem.json: {d['state']} · {L['k']} · kans {T.nl((L['kans'] or {}).get('p'), 0)}% · "
          f"Nasdaq-100 {T.nl(d['dd'])}% t.o.v. jaartop, dal {T.nl(d['low_dd'])}% op {d['low']} ({d['days_low']} d) · "
          f"termijnstructuur {out['term']['state']}")
    for e in out["source_errors"]:
        print(f"  let op: {e}")


def main():
    try:
        run("--force" in sys.argv)
    except Exception as e:  # noqa: BLE001
        print(f"Bodemsignaal niet bijgewerkt: {e}")
        traceback.print_exc(limit=3)
        sys.exit(0 if os.path.exists(OUT) else 1)   # oude bodem.json blijft staan: de lus loopt door


if __name__ == "__main__":
    main()
