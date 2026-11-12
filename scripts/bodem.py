#!/usr/bin/env python3
"""
Bodemsignaal — schrijft bodem.json: is het dal van een dip in de Nasdaq-100 waarschijnlijk bereikt?

Waarom: de techradar (tech.py) waarschuwt vóór een terugval. Dit is de andere kant: staat de Nasdaq-100 al 5% of meer
onder zijn 52-weekse top, dan is de vraag of het dal er al is. Vijf bodemsignalen, elk met vaste, ronde drempels die
vooraf gekozen zijn (niet op de uitkomst geoptimaliseerd):
  1. termijnstructuur weer normaal: de angstindex voor 1 maand (VIX) stond boven die voor 3 maanden (VIX3M) = paniek,
     en staat er nu weer onder (de omkering is voorbij)
  2. angstpiek voorbij: de VIX piekte ruim boven zijn jaargemiddelde en is sindsdien een vijfde of meer gezakt
  3. uitverkocht en herstelt: de RSI van de Nasdaq-100 kwam onder 30 en staat weer boven 40
  4. dal houdt stand: al twee weken geen lager slot dan het dal van deze dip
  5. weer boven het 20-daags gemiddelde
Alles wordt elke keer nagerekend op de volledige historie: per signaal en per niveau (aantal signalen tegelijk) hoe vaak
het dal van dat moment daarna standhield, d.w.z. de Nasdaq-100 sloot in de 60 handelsdagen (±3 maanden) erna niet lager
dan het dal tot dan toe. Vergeleken met alle dagen in een dip, per dag én per episode (episodes liggen minstens 60
handelsdagen uit elkaar, zodat één dip niet dubbel telt). Een signaal op dag X gebruikt alleen koersen t/m dag X.
Valt een bron weg of is haar historie te kort, dan telt dat signaal niet mee en staat dat in source_errors.

Een bodemsignaal is geen garantie: in een lange berenmarkt (2000-2002, 2008, 2022) volgde na een bevestigd dal vaak
toch een lager dal. Daarom staat bij elke stand hoe vaak het in het verleden goed ging.

Alleen standaardbibliotheek; rekenhulpen en ophalen komen uit tech.py. Draait in de lus van de Patroonradar, hooguit
eens per FRESH_MIN minuten.
"""
import json
import os
import sys
import time
import traceback
from collections import deque
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tech as T  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "bodem.json")
FRESH_MIN = 55               # niet vaker opnieuw rekenen dan eens per zoveel minuten
VERSION = 1                  # nieuwe versie van dit script = meteen opnieuw rekenen
BUDGET_S = 150               # na zoveel seconden ophalen de overige bronnen overslaan
MIN_HIST = 2500              # signaal met minder dagen historie (±10 jaar) telt niet mee
MIN_EP = 10                  # minder vergelijkbare episodes: geen percentage noemen
SRC = {"ndx": ("^NDX", "Nasdaq-100 (^NDX)"), "vix": ("^VIX", "Angstindex 1 maand (^VIX)"),
       "v3m": ("^VIX3M", "Angstindex 3 maanden (^VIX3M)")}

# Drempels: vaste, ronde getallen, vooraf gekozen
DIP = 5.0          # dip: Nasdaq-100 5% of meer onder zijn 52-weekse top (zelfde grens als 'terugval' in de techradar)
H = 60             # 'het dal hield stand' = geen lager slot in de 60 handelsdagen (±3 maanden) erna
EP_GAP = H         # episodes minstens 60 handelsdagen uit elkaar: hun vooruitvensters overlappen nooit
INV_WIN, INV_MIN, NORM_DAYS = 15, 2, 2   # termijnstructuur: >= 2 dagen omgekeerd in 15 handelsdagen, nu 2 dagen normaal
ANGST_WIN = 15     # angstpiek: hoogste VIX-slot van de laatste 15 handelsdagen ...
ANGST_SPIKE = 1.5  # ... minstens anderhalf keer het jaargemiddelde (250 dagen) van dat moment ...
ANGST_DROP = 0.20  # ... en nu een vijfde of meer daaronder
RSI_N, RSI_LOW, RSI_BACK, RSI_WIN = 14, 30.0, 40.0, 10   # RSI onder 30 in de laatste 10 handelsdagen, nu 40 of hoger
HOLD_DAYS = 10     # dal houdt stand: 10 handelsdagen (twee weken) geen lager slot
MA_N, MA_DAYS = 20, 2    # twee slotkoersen op rij boven het 20-daags gemiddelde

SIGNALS = [  # sleutel, naam, uitleg, bron
    ("termijn", "Termijnstructuur weer normaal", "De angstindex voor 1 maand (VIX) stond boven die voor 3 maanden (VIX3M): beleggers betaalden meer voor bescherming op korte dan op lange termijn, een teken van paniek. Nu staat hij er weer onder.", "v3m"),
    ("angstpiek", "Angstpiek voorbij", "De angstindex (VIX) piekte op minstens anderhalf keer zijn jaargemiddelde en is sindsdien een vijfde of meer gezakt.", "vix"),
    ("rsi", "Uitverkocht en herstelt", "De RSI van de Nasdaq-100 kwam onder 30 (uitverkocht) en staat weer op 40 of hoger.", "ndx"),
    ("dal_houdt", "Dal houdt stand", "Al 10 handelsdagen (twee weken) geen lager slot dan het dal van deze dip.", "ndx"),
    ("boven_20d", "Weer boven 20-daags gemiddelde", "De Nasdaq-100 sloot twee dagen op rij boven zijn 20-daags gemiddelde.", "ndx"),
]
# Niveau op basis van het aantal bodemsignalen tegelijk: sleutel, naam, minimum, regel, tekst
LEVELS = [
    ("geen", "Nog geen bodem", 0, "0–1 signalen", "Nog geen bevestiging dat het dal is bereikt."),
    ("maak", "Bodem in de maak", 2, "2 signalen", "Twee bodemsignalen tegelijk: de daling lijkt uit te raken, maar het dal is nog niet bevestigd."),
    ("bereikt", "Dal waarschijnlijk bereikt", 3, "3+ signalen", "Drie of meer bodemsignalen tegelijk: het dal is waarschijnlijk bereikt."),
]
LV_OK = 2                    # index van 'bereikt'


def level_of(c):
    return max(i for i, lv in enumerate(LEVELS) if c >= lv[2])


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


def rsi(xs, n=RSI_N):
    """RSI volgens Wilder over n dagen; None zolang er te weinig dagen zijn."""
    out, up, dn = [None] * len(xs), 0.0, 0.0
    for i in range(1, len(xs)):
        ch = xs[i] - xs[i - 1]
        u, d = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            up, dn = up + u / n, dn + d / n
            if i < n:
                continue
        else:
            up, dn = (up * (n - 1) + u) / n, (dn * (n - 1) + d) / n
        out[i] = 100.0 if not dn else 100 - 100 / (1 + up / dn)
    return out


def episodes(idx, gap=EP_GAP):
    """Eerste dag van elke episode: signaaldagen met een gat van minder dan 'gap' handelsdagen horen bij elkaar."""
    starts, last = [], None
    for i in idx:
        if last is None or i - last >= gap:
            starts.append(i)
        last = i
    return starts


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


def window(xs, i, k):
    """De laatste k waarden t/m dag i zonder gaten; None als er meer dan een vijfde ontbreekt."""
    part = [v for v in xs[max(0, i - k + 1):i + 1] if v is not None]
    return part if i >= k - 1 and len(part) >= k - k // 5 else None


# ── Signalen ─────────────────────────────────────────────────────────────
def bereken(ndx, vix, v3m):
    """Per handelsdag: {sleutel: [True/False/None]} (None = geen data) en de tussenreeksen."""
    n = len(ndx)
    top, low = dal_reeks(ndx)
    s20, rs = T.sma(ndx, MA_N), rsi(ndx)
    v250 = T.sma(vix, 250)
    ratio = [vix[i] / v3m[i] if vix[i] and v3m[i] else None for i in range(n)]
    dd = [(ndx[i] / ndx[top[i]] - 1) * 100 for i in range(n)]             # nu t.o.v. de 52-weekse top
    dd_low = [(ndx[low[i]] / ndx[top[i]] - 1) * 100 for i in range(n)]    # het dal t.o.v. die top
    sig = {s[0]: [None] * n for s in SIGNALS}
    for i in range(n):
        # 1. termijnstructuur: de laatste NORM_DAYS dagen normaal, in de INV_WIN dagen daarvoor >= INV_MIN keer omgekeerd
        nu, eerder = ratio[max(0, i - NORM_DAYS + 1):i + 1], window(ratio, i - NORM_DAYS, INV_WIN)
        if i >= INV_WIN + NORM_DAYS and eerder is not None and all(v is not None for v in nu):
            sig["termijn"][i] = all(v < 1.0 for v in nu) and sum(1 for v in eerder if v > 1.0) >= INV_MIN
        # 2. angstpiek: hoogste slot van de laatste ANGST_WIN dagen was een piek, en we staan er ruim onder
        w = window(vix, i, ANGST_WIN)
        if w is not None and vix[i] is not None:
            j = max((k for k in range(i - ANGST_WIN + 1, i + 1) if vix[k] is not None), key=lambda k: (vix[k], k))
            if v250[j]:
                sig["angstpiek"][i] = j < i and vix[j] >= ANGST_SPIKE * v250[j] and vix[i] <= (1 - ANGST_DROP) * vix[j]
        # 3. RSI onder 30 geweest en terug op 40+
        if i >= RSI_WIN and rs[i] is not None and all(v is not None for v in rs[i - RSI_WIN + 1:i + 1]):
            sig["rsi"][i] = min(rs[i - RSI_WIN + 1:i + 1]) < RSI_LOW and rs[i] >= RSI_BACK
        # 4. het dal van deze dip (5%+ onder de top) is al HOLD_DAYS handelsdagen niet gebroken
        if i >= 252:
            sig["dal_houdt"][i] = dd_low[i] <= -DIP and i - low[i] >= HOLD_DAYS
        # 5. MA_DAYS slotkoersen op rij boven het 20-daags gemiddelde
        if i >= MA_N + MA_DAYS and all(s20[k] is not None for k in range(i - MA_DAYS + 1, i + 1)):
            sig["boven_20d"][i] = all(ndx[k] > s20[k] for k in range(i - MA_DAYS + 1, i + 1))
    return sig, {"top": top, "low": low, "dd": dd, "dd_low": dd_low, "ratio": ratio, "rsi": rs, "s20": s20, "v250": v250}


def uitkomst(ndx, low):
    """Per dag de uitkomst over de H handelsdagen erna (None als die nog niet om zijn): hield het dal tot dan toe stand
    (geen lager slot), hoeveel procent lager het diepste slot kwam dan dat dal (0 als het standhield), en het rendement."""
    n, hold, lager, fwd = len(ndx), [None] * len(ndx), [None] * len(ndx), [None] * len(ndx)
    q = deque()                                      # voortschrijdend minimum over i+1..i+H
    for i in range(n - 1, -1, -1):
        while q and q[0] > i + H:
            q.popleft()
        if i + H < n:
            m = ndx[q[0]]
            hold[i] = m >= ndx[low[i]]
            lager[i] = min(0.0, (m / ndx[low[i]] - 1) * 100)
            fwd[i] = (ndx[i + H] / ndx[i] - 1) * 100
        while q and ndx[q[-1]] >= ndx[i]:
            q.pop()
        q.append(i)
    return hold, lager, fwd


def reeks(rows):
    """Alle dagreeksen op de kalender van de Nasdaq-100: signalen, aantal tegelijk en niveau."""
    dates = [d for d, _ in rows["ndx"]]
    ndx = [c for _, c in rows["ndx"]]
    al = {k: T.align(dates, rows.get(k) or []) for k in ("vix", "v3m")}
    sig, x = bereken(ndx, al["vix"], al["v3m"])
    n = len(ndx)
    hold, lager, fwd = uitkomst(ndx, x["low"])
    nd = {k: sum(1 for v in sig[k] if v is not None) for k in sig}
    kort = [k for k in sig if 0 < nd[k] < MIN_HIST]                      # afgekapte of jonge bron: telt niet mee
    oud = [k for k in sig if nd[k] >= MIN_HIST and sig[k][-1] is None]   # geen actuele waarde: telt niet mee
    use = [k for k in sig if nd[k] >= MIN_HIST and sig[k][-1] is not None]
    c0 = max((next(i for i, v in enumerate(sig[k]) if v is not None) for k in use), default=n)
    cnt, lv = [None] * n, [None] * n
    for i in range(c0, n):
        if any(sig[k][i] is None for k in use):       # gat in een bron: deze dag telt niet mee (ook niet als 0)
            continue
        cnt[i] = sum(1 for k in use if sig[k][i])
        lv[i] = level_of(cnt[i])
    return {"dates": dates, "ndx": ndx, "al": al, "sig": sig, "x": x, "hold": hold, "lager": lager, "fwd": fwd,
            "use": use, "kort": kort, "oud": oud, "c0": c0, "cnt": cnt, "lv": lv}


# ── Backtest ─────────────────────────────────────────────────────────────
def pct(xs):
    return T.r(100 * sum(1 for v in xs if v) / len(xs)) if xs else None


def stats(on, base_days, R):
    """Hoe vaak hield het dal stand na deze signaaldagen (per dag en per episode), vergeleken met alle dagen in
    'base_days' (zelfde soort dagen zonder het signaal mee te wegen)? n telt alleen dagen met bekende uitkomst."""
    hold, lager, fwd = R["hold"], R["lager"], R["fwd"]
    on = [i for i in on if hold[i] is not None]
    eps = episodes(on)
    base = pct([hold[i] for i in base_days if hold[i] is not None])
    h, h_ep = pct([hold[i] for i in on]), pct([hold[i] for i in eps])
    mis = [lager[i] for i in on if not hold[i]]
    return {"n": len(on), "n_ep": len(eps), "hold": h, "hold_ep": h_ep, "base": base,
            "lift": T.lift(h, base), "lift_ep": T.lift(h_ep, base),
            "fwd": T.r(median([fwd[i] for i in on])), "pos": pct([fwd[i] > 0 for i in on]),
            "lager": T.r(median(mis))}                # ging het mis, dan kwam het dal mediaan zoveel procent lager


def build(rows, errors):
    R = reeks(rows)
    dates, ndx, sig, x, cnt, lv, c0 = (R[k] for k in ("dates", "ndx", "sig", "x", "cnt", "lv", "c0"))
    n, last = len(ndx), len(ndx) - 1
    iso = lambda i: dates[i].isoformat()  # noqa: E731
    dd, dd_low, top, low = x["dd"], x["dd_low"], x["top"], x["low"]
    errors = list(errors)
    for k, name, _, src in SIGNALS:                       # signalen die niet meetellen: altijd zichtbaar melden
        if k in R["kort"]:
            errors.append(f"{SRC[src][1]}: te korte historie ({sum(1 for v in sig[k] if v is not None)} dagen); "
                          f"'{name}' telt niet mee")
        elif k in R["oud"]:
            errors.append(f"{SRC[src][1]}: geen actuele waarde; '{name}' telt vandaag niet mee")
        elif not any(v is not None for v in sig[k]):
            errors.append(f"{SRC[src][1]}: geen data; '{name}' telt niet mee")

    # Dagen in een dip (nu 5%+ onder de top) en dagen in herstel (dal 5%+ onder de top, nu weer binnen 5%)
    in_dip = [i for i in range(252, n) if dd[i] <= -DIP]
    in_her = [i for i in range(252, n) if dd_low[i] <= -DIP < dd[i]]
    cond_days = {"dip": in_dip, "herstel": in_her}

    # 1. per signaal: alle dagen in een dip waarop het signaal bestond
    signals = []
    for k, name, why, _ in SIGNALS:
        col, e = sig[k], {"k": k, "name": name, "uitleg": why}
        defined = [i for i in in_dip if col[i] is not None]
        if not defined or k in R["kort"]:
            signals.append({**e, "on": None, "na": True, "in_mix": False})
            continue
        j = last
        while col[last] and j > 0 and col[j - 1]:         # begin van de huidige reeks
            j -= 1
        on_all = [i for i in range(n) if col[i]]
        signals.append({**e, "on": col[last], "in_mix": k in R["use"], **({"oud": True} if k in R["oud"] else {}),
                        "since": iso(j) if col[last] else None, "last": iso(on_all[-1]) if on_all else None,
                        "from": iso(next(i for i in range(n) if col[i] is not None)),
                        **stats([i for i in defined if col[i]], defined, R)})

    # 2. per niveau, apart voor 'in een dip' en 'in herstel' (vanaf de dag dat alle signalen bestaan)
    levels = []
    for li, (k, label, _, rule, _) in enumerate(LEVELS):
        e = {"k": k, "label": label, "rule": rule}
        for cond, days in cond_days.items():
            comp = [i for i in days if i >= c0 and lv[i] is not None]
            e[cond] = stats([i for i in comp if lv[i] == li], comp, R)
            e[cond]["share"] = T.r(100 * sum(1 for i in comp if lv[i] == li) / len(comp)) if comp else None
        levels.append(e)
    per_cnt = []                                          # per aantal signalen (0..5), in een dip
    comp = [i for i in in_dip if i >= c0 and cnt[i] is not None]
    for c in range(len(R["use"]) + 1):
        st = stats([i for i in comp if cnt[i] == c], comp, R)
        per_cnt.append({"c": c, **{k: st[k] for k in ("n", "n_ep", "hold", "hold_ep", "fwd")}})

    # 3. correcties >= 10% sinds alle signalen bestaan: wanneer kwam 'dal waarschijnlijk bereikt' voor het eerst na de
    #    dag van -10%, hoe lang na het dal, hoe ver boven het dal, en hield het dal van dat moment daarna stand?
    dips = []
    for pk, tr, h10, end in T.correcties(ndx):
        if pk < c0:
            continue
        stop = end if end is not None else last
        ok = [j for j in range(h10, stop + 1) if lv[j] is not None and lv[j] >= LV_OK]
        f = ok[0] if ok else None
        e = {"peak": iso(pk), "trough": iso(tr), "depth": T.r((ndx[tr] / ndx[pk] - 1) * 100), "days": tr - pk,
             "end": iso(end) if end is not None else None,
             "top": dd[pk] > -DIP,                    # begon bij de 52-weekse top (anders: golf in een langere daling)
             "sig": iso(f) if f is not None else None}
        if f is not None:
            lo = min(ndx[pk:f + 1])                   # het dal van deze correctie tot en met de signaaldag
            na = min(ndx[f + 1:f + H + 1]) if f + H < n else None
            e.update({"na_dal": f - tr,               # handelsdagen na het uiteindelijke dal (negatief = ervoor)
                      "boven_dal": T.r((ndx[f] / lo - 1) * 100), "onder_top": T.r((ndx[f] / ndx[pk] - 1) * 100),
                      "hield": None if na is None else na >= lo,            # None = de 60 dagen zijn nog niet om
                      "lager": None if na is None else T.r(min(0.0, (na / lo - 1) * 100)),
                      "na60": T.r((ndx[f + H] / ndx[f] - 1) * 100) if f + H < n else None})
        dips.append(e)
    met = [d for d in dips if d.get("hield") is not None]
    goed = [d for d in met if d["hield"]]
    dip_sum = {"n": len(dips), "met": len(met), "goed": len(goed),
               "na_dal": median([d["na_dal"] for d in goed]), "boven_dal": T.r(median([d["boven_dal"] for d in goed])),
               "lager": T.r(median([d["lager"] for d in met if not d["hield"]])),
               "geen": sum(1 for d in dips if not d["sig"] and d["end"])}

    # 4. hoe lang duurden correcties van top tot dal (handelsdagen), sinds 1985
    alle = [(tr - pk, (ndx[tr] / ndx[pk] - 1) * 100) for pk, tr, _, end in T.correcties(ndx) if end is not None]
    typ = []
    for key, lo_, hi_ in (("correctie", 10, 20), ("berenmarkt", 20, 101)):
        ds = [d for d, depth in alle if lo_ <= -depth < hi_]
        typ.append({"k": key, "n": len(ds), "med": median(ds), "q1": T.r(quart(ds, 0.25), 0), "q3": T.r(quart(ds, 0.75), 0)})

    # 5. nu
    state = "dip" if dd[last] <= -DIP else "herstel" if dd_low[last] <= -DIP else "geen"
    k_now = cnt[last] if cnt[last] is not None else 0
    li = level_of(k_now)
    L, Ls = LEVELS[li], levels[li]
    m = dict(Ls[state], cond=state) if state in cond_days else None
    mix = [s for s in signals if s.get("in_mix")]
    text = L[4]
    if state == "geen":
        text = "Geen dip: de Nasdaq-100 staat binnen 5% van zijn 52-weekse top."
    elif m and m["n_ep"] < MIN_EP:
        text += f" Let op: te weinig vergelijkbare keren in de historie ({m['n_ep']}) om de kans te schatten."
    inv_days = [i for i in range(n) if x["ratio"][i] is not None and x["ratio"][i] > 1.0]
    inv = None
    if inv_days:
        j = inv_days[-1]
        k = len(inv_days) - 1
        while k > 0 and inv_days[k] - inv_days[k - 1] <= INV_WIN:        # de laatste omkering (dagen dicht bij elkaar)
            k -= 1
        inv = {"on": j == last, "from": iso(inv_days[k]), "to": iso(j), "days": len(inv_days) - k,
               "max": T.r(max(x["ratio"][i] for i in inv_days[k:]), 2)}
    vix, v3m = R["al"]["vix"], R["al"]["v3m"]
    sp = max(0, n - 90)
    return {
        "version": VERSION,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": iso(last),
        "sources": {k: f"{rows[k][0][0].year}–nu" for k in ("ndx", "vix", "v3m") if rows.get(k)},
        "source_errors": errors,
        "horizon": {"h": H, "dip": DIP, "ep_gap": EP_GAP, "min_ep": MIN_EP},
        "dip": {"state": state, "status": T.status_of(dd[last]), "dd": T.r(dd[last]), "low_dd": T.r(dd_low[last]),
                "top": iso(top[last]), "low": iso(low[last]), "days_top": last - top[last], "days_low": last - low[last],
                "from_low": T.r((ndx[last] / ndx[low[last]] - 1) * 100), "close": T.r(ndx[last], 2)},
        "term": {"vix": T.r(vix[last], 2), "v3m": T.r(v3m[last], 2), "ratio": T.r(x["ratio"][last], 3), "inv": inv},
        "rsi": T.r(x["rsi"][last]),
        "signals": signals,
        "level": {"k": L[0], "label": L[1], "n_on": k_now, "n_max": len(mix), "on": [s["k"] for s in mix if s["on"]],
                  "text": text, "m": m},
        "levels": levels, "per_cnt": per_cnt, "from": iso(c0) if c0 < n else None,
        "dips": dips[-15:], "dip_sum": dip_sum, "typ": typ,
        "spark": {"start": iso(sp), "ndx": [round(c) for c in ndx[sp:]],
                  "spread": [T.r(v3m[i] - vix[i], 2) if vix[i] and v3m[i] else None for i in range(sp, n)]},
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
    print(f"bodem.json: {d['state']} · {L['k']} · {L['n_on']} van {L['n_max']} signalen ({', '.join(L['on']) or 'geen'}) · "
          f"Nasdaq-100 {T.nl(d['dd'])}% t.o.v. jaartop, dal {T.nl(d['low_dd'])}% op {d['low']}")
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
