#!/usr/bin/env python3
"""
Techradar — schrijft tech.json: vroege waarschuwing voor een terugval of correctie in Amerikaanse tech en chips.

Waarom: de portefeuille zit vol Amerikaanse tech- en chipaandelen (en QDVE). Een correctie van de Nasdaq-100 raakt
die hard, ook als de brede markt (crash.py) rustig blijft. Bronnen: Nasdaq-100 (^NDX, vanaf 1985), chips (^SOX, 1994),
angstindex Nasdaq (^VXN, 2001) en de S&P 500 (^GSPC) voor de vraag of de rally smal is.
Alles wordt elke keer opnieuw nagerekend op de volledige historie: per signaal en per niveau hoe vaak de Nasdaq-100
daarna binnen 40 handelsdagen (±2 maanden) nog 10% of meer daalde, vergeleken met normaal. Per dag én per episode
(episodes liggen minstens 40 handelsdagen uit elkaar, zodat één daling niet dubbel telt), en apart voor dagen waarop de
Nasdaq-100 nog binnen 5% van zijn top stond: alleen dat toetst of de radar vóóraf waarschuwt (midden in een daling zijn
grote bewegingen sowieso waarschijnlijker). Een signaal op dag X gebruikt alleen koersen t/m dag X (geen blik vooruit).
Valt een bron weg, is ze verouderd of is haar historie te kort, dan telt dat signaal niet mee en staat dat in
source_errors.

Optioneel: staat het geheim NTFY_TOPIC in de omgeving, dan volgt een pushbericht via ntfy.sh zodra het niveau 'verhoogd'
of 'hoog' bereikt en dat niveau in deze waarschuwing nog niet gemeld is: een nieuwe waarschuwing hooguit eens per 24 uur,
een stijging naar 'hoog' meteen, en een mislukte melding probeert de volgende run opnieuw. De allereerste run meldt
niets (legt alleen het startpunt vast). Alleen marktinformatie, nooit iets over de portefeuille.

Alleen standaardbibliotheek. Draait in de lus van de Patroonradar; hooguit eens per FRESH_MIN minuten.
"""
import json
import os
import re
import sys
import time
import traceback
from collections import deque
from datetime import date, datetime, timezone
from statistics import median_low
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tech.json")
FRESH_MIN = 55               # niet vaker opnieuw rekenen dan eens per zoveel minuten
VERSION = 1                  # nieuwe versie van dit script = meteen opnieuw rekenen
FETCH_FROM = date(1985, 1, 1)
BUDGET_S = 180               # na zoveel seconden ophalen de overige bronnen overslaan (de lus geeft 5 minuten)
H10, H15 = 40, 60            # gebeurtenis: >= 10% daling binnen 40 handelsdagen (±2 mnd), >= 15% binnen 60 (±3 mnd)
EP_GAP = H10                 # episode: signaaldagen met een gat van minder dan 40 handelsdagen horen bij elkaar, zodat
                             # de vooruitvensters van twee episodes nooit overlappen
WARN_GAP = 10                # correcties: een waarschuwing die korter dan 10 handelsdagen wegvalt, loopt door
LEAD = 30                    # correcties: waarschuwing tot 30 handelsdagen vóór de top telt als 'vooraf'
CORR = 10.0                  # correctie: >= 10% onder de top (slotkoersen)
CORR_FROM = date(2001, 1, 1)
TOP_DD = 0.05                # 'bij de top': Nasdaq-100 binnen 5% van zijn 52-weekse top (stand 'normaal')
MIN_EP = 10                  # minder vergelijkbare episodes: geen percentage noemen
MIN_HIST = 2500              # signaal met minder dagen historie (±10 jaar) telt niet mee: afgekapte of kapotte bron
STALE_D = 6                  # slot van een andere index ouder dan zoveel kalenderdagen telt niet
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SRC = {"ndx": ("^NDX", "Nasdaq-100 (^NDX)"), "sox": ("^SOX", "Chipindex (^SOX)"),
       "vxn": ("^VXN", "Angstindex Nasdaq (^VXN)"), "spx": ("^GSPC", "S&P 500 (^GSPC)")}

# Drempels: vaste getallen, bewust rond en niet op de uitkomst geoptimaliseerd.
EXT_HOT = 0.15     # overstrekt: > 15% boven 200d. Naar schatting het bovenste 15-20% van de dagen sinds 1985 (1995-2000,
                   # 2003, 2009, 2020, 2023); het werkelijke aandeel staat elke run in tech.json (signals[].share)
EXT_WAS = 0.10     # trendbreuk: in de laatste 60 dagen nog > 10% boven 200d geweest
ROC_RUN = 0.15     # momentum: sterke rit = > 15% in 60 handelsdagen (ergens in de laatste 21 dagen)
SOX_DROP = 0.08    # chips: >= 8% onder hun 20-daagse top ...
NDX_NEAR = 0.04    # ... terwijl de Nasdaq-100 nog binnen 4% van zijn 52-weekse top staat
RATIO_GAP = 0.05   # of verhouding SOX/NDX > 5% onder haar 50-daags gemiddelde
VXN_JUMP = 0.25    # angst: VXN > 25% hoger dan 5 handelsdagen geleden
VXN_RISE = 0.20    # of VXN > 20% boven zijn 50-daags gemiddelde ...
VXN_CALM = 30.0    # ... terwijl hij nog onder 30 staat (onrust begint vanuit rust)
REL_GAP = 0.08     # smalle rally: Nasdaq-100 > 8 procentpunt beter dan S&P 500 over 63 handelsdagen
OPT_ON, OPT_OFF = 1.15, 1.05  # optioneel signaal telt mee zodra het tot dan toe >= 1,15x de normale kans gaf, en pas
                              # weer niet onder 1,05x (geen heen-en-weer); de keuze wordt eens per maand herzien ...
OPT_MIN, OPT_EP = 250, 15     # ... en pas na 250 afgeronde signaaldagen uit 15 episodes (alleen verleden: geen blik vooruit)

SIGNALS = [  # sleutel, naam, uitleg, bron, optioneel
    ("overstrekt", "Overstrekt", "Nasdaq-100 staat meer dan 15% boven zijn 200-daags gemiddelde: veel winst in korte tijd, weinig buffer.", "ndx", False),
    ("trendbreuk", "Trendbreuk", "Nasdaq-100 sluit onder zijn 50-daags gemiddelde, terwijl hij in de laatste 60 dagen nog meer dan 10% boven zijn 200-daags stond.", "ndx", False),
    ("momentum_omslag", "Momentum slaat om", "Na een sterke rit (meer dan 15% in 60 handelsdagen) staat de Nasdaq-100 lager dan 20 handelsdagen geleden.", "ndx", False),
    ("chips_zwakker", "Chips lopen voorop omlaag", "Chipindex (SOX) 8% of meer onder zijn 20-daagse top terwijl de Nasdaq-100 nog binnen 4% van zijn jaartop staat, of chips 5% zwakker dan de Nasdaq-100 t.o.v. hun 50-daags gemiddelde.", "sox", False),
    ("angst_stijgt", "Angst neemt toe", "Angstindex van de Nasdaq (VXN) meer dan 25% hoger dan 5 dagen geleden, of 20% boven zijn 50-daags gemiddelde terwijl hij nog onder 30 staat.", "vxn", False),
    ("smalle_rally", "Smalle rally", "Nasdaq-100 deed het in 3 maanden 8 procentpunt of meer beter dan de S&P 500: veel geld in weinig namen. Telt alleen mee in de som als het historisch extra kans gaf.", "spx", True),
]
OPT = {s[0]: s[4] for s in SIGNALS}
# Niveau op basis van het aantal signalen tegelijk (één signaal alleen is ruis): sleutel, naam, minimum, regel, tekst
LEVELS = [
    ("rustig", "Rustig", 0, "0–1 signalen", "Geen of één waarschuwingssignaal."),
    ("opletten", "Opletten", 2, "2 signalen", "Twee waarschuwingssignalen tegelijk: opletten, nog geen actie nodig."),
    ("verhoogd", "Verhoogd", 3, "3 signalen", "Drie waarschuwingssignalen tegelijk: verhoogde kans op een terugval in tech en chips."),
    ("hoog", "Hoog", 4, "4+ signalen", "Vier of meer waarschuwingssignalen tegelijk: de hoogste waarschuwingsstand."),
]
LV_WARN = 2                  # index van 'verhoogd': vanaf hier telt een correctie als 'vooraf gewaarschuwd' en volgt een pushbericht
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")


# ── Data ─────────────────────────────────────────────────────────────────
def get(url, tries=2, timeout=25):
    err = None
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            err = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url[:80]}: {err}")


def yahoo_daily(sym, start=FETCH_FROM):
    """[(date, close)] oplopend, één slot per dag."""
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    last = None
    for host in ("query1", "query2"):
        try:
            j = json.loads(get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(sym)}"
                               f"?period1={p1}&period2={p2}&interval=1d&includePrePost=false"))
            res = j["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            out = {datetime.fromtimestamp(t, tz=timezone.utc).date(): float(c)
                   for t, c in zip(res.get("timestamp") or [], closes) if c and c > 0}
            if out:
                return sorted(out.items())
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{sym}: {last}")


def align(dates, rows):
    """Slot van een andere index op de handelsdagen van de Nasdaq-100: laatste slot t/m die dag (None als te oud)."""
    out, j = [], -1
    for d in dates:
        while j + 1 < len(rows) and rows[j + 1][0] <= d:
            j += 1
        out.append(rows[j][1] if j >= 0 and (d - rows[j][0]).days <= STALE_D else None)
    return out


# ── Rekenhulpen (dag i gebruikt alleen dag 0..i) ─────────────────────────
def sma(xs, n):
    """Voortschrijdend gemiddelde over n dagen t/m dag i. Hooguit een vijfde van het venster mag ontbreken (gat in een
    bron): dan het gemiddelde van wat er is. Anders None."""
    out, s, k, need = [None] * len(xs), 0.0, 0, n - n // 5
    for i, x in enumerate(xs):
        if x is not None:
            s, k = s + x, k + 1
        if i >= n and xs[i - n] is not None:
            s, k = s - xs[i - n], k - 1
        if i >= n - 1 and k >= need:
            out[i] = s / k
    return out


def roll_max(xs, n):
    """Hoogste waarde over de laatste n dagen t/m dag i; hooguit een vijfde van die dagen mag ontbreken, anders None."""
    out, q, k, need = [None] * len(xs), deque(), 0, n - n // 5
    for i, x in enumerate(xs):
        if x is not None:
            k += 1
            while q and xs[q[-1]] <= x:
                q.pop()
            q.append(i)
        if i >= n and xs[i - n] is not None:
            k -= 1
        while q and q[0] <= i - n:
            q.popleft()
        if i >= n - 1 and k >= need:
            out[i] = xs[q[0]]
    return out


def chg(xs, i, k):
    return xs[i] / xs[i - k] - 1 if i >= k and xs[i] is not None and xs[i - k] else None


def vooruit(xs, h):
    """Diepste slot (% t.o.v. dag i) in de h handelsdagen ná dag i; None als die periode nog niet om is. Alleen de uitkomst."""
    n = len(xs)
    return [(min(xs[i + 1:i + h + 1]) / xs[i] - 1) * 100 if i + h < n else None for i in range(n)]


def episodes(idx):
    """Eerste dag van elke episode: signaaldagen met een gat van minder dan EP_GAP handelsdagen horen bij elkaar."""
    starts, last = [], None
    for i in idx:
        if last is None or i - last >= EP_GAP:
            starts.append(i)
        last = i
    return starts


def r(x, d=1):
    return None if x is None or x != x else round(x, d)


def nl(x, d=1):
    return "—" if x is None else f"{x:.{d}f}".replace(".", ",")


def lift(a, b):
    return round(a / b, 2) if a is not None and b else None


# ── Signalen ─────────────────────────────────────────────────────────────
def bereken(ndx, sox, vxn, spx):
    """Per handelsdag: {sleutel: [True/False/None]} (None = geen data) en de tussenreeksen."""
    n = len(ndx)
    s50, s200, hi52 = sma(ndx, 50), sma(ndx, 200), roll_max(ndx, 252)
    ext = [ndx[i] / s200[i] - 1 if s200[i] else None for i in range(n)]
    dd52 = [ndx[i] / hi52[i] - 1 if hi52[i] else None for i in range(n)]
    ext60 = roll_max(ext, 60)                                  # hoogste stand boven 200d in de laatste 60 dagen
    run60 = roll_max([chg(ndx, i, 60) for i in range(n)], 21)  # sterkste 60-daagse rit in de laatste 21 dagen
    sx_hi20 = roll_max(sox, 20)
    ratio = [sox[i] / ndx[i] if sox[i] is not None else None for i in range(n)]
    r50, v50 = sma(ratio, 50), sma(vxn, 50)
    sig = {s[0]: [None] * n for s in SIGNALS}
    for i in range(n):
        c = ndx[i]
        if ext[i] is not None:
            sig["overstrekt"][i] = ext[i] > EXT_HOT
        if s50[i] is not None and ext60[i] is not None:
            sig["trendbreuk"][i] = c < s50[i] and ext60[i] > EXT_WAS
        r20 = chg(ndx, i, 20)
        if r20 is not None and run60[i] is not None:
            sig["momentum_omslag"][i] = r20 < 0 and run60[i] > ROC_RUN
        if sox[i] is not None and sx_hi20[i] and r50[i] and dd52[i] is not None:
            sig["chips_zwakker"][i] = ((sox[i] / sx_hi20[i] - 1 <= -SOX_DROP and dd52[i] >= -NDX_NEAR)
                                       or ratio[i] < r50[i] * (1 - RATIO_GAP))
        v5 = chg(vxn, i, 5)
        if v5 is not None and v50[i]:
            sig["angst_stijgt"][i] = v5 > VXN_JUMP or (vxn[i] > v50[i] * (1 + VXN_RISE) and vxn[i] < VXN_CALM)
        a, b = chg(ndx, i, 63), chg(spx, i, 63)
        if a is not None and b is not None:
            sig["smalle_rally"][i] = a - b > REL_GAP
    return sig, {"s50": s50, "s200": s200, "ext": ext, "dd52": dd52, "sx_hi20": sx_hi20, "ratio": ratio, "r50": r50}


def mix_mask(col, f40, dates):
    """Telt een optioneel signaal op dag i mee? Alleen als het tot dan toe extra kans gaf, gemeten over signaaldagen
    waarvan de uitkomst op dag i al bekend was (dag j met j + H10 <= i), uit genoeg losse episodes. Aan bij >= OPT_ON,
    uit pas onder OPT_OFF, en alleen op de eerste handelsdag van een maand herzien. Zo kijkt ook deze keuze nooit vooruit
    en springt ze niet heen en weer."""
    out, a_all, a_ev, a_on, a_hit, a_ep, last_on, aan = [False] * len(col), 0, 0, 0, 0, 0, None, False
    for i in range(len(col)):
        j = i - H10
        if j >= 0 and col[j] is not None and f40[j] is not None:
            ev = f40[j] <= -10
            a_all, a_ev = a_all + 1, a_ev + ev
            if col[j]:
                a_on, a_hit = a_on + 1, a_hit + ev
                a_ep += last_on is None or j - last_on >= EP_GAP
                last_on = j
        if i == 0 or dates[i].month != dates[i - 1].month:
            lf = (a_hit / a_on) / (a_ev / a_all) if a_on and a_ev else 0.0
            aan = a_on >= OPT_MIN and a_ep >= OPT_EP and (lf >= OPT_ON or (aan and lf >= OPT_OFF))
        out[i] = aan
    return out


def level_of(c):
    return max(i for i, lv in enumerate(LEVELS) if c >= lv[2])


def reeks(rows):
    """Alle dagreeksen op de kalender van de Nasdaq-100: signalen, aantal tegelijk en niveau."""
    dates = [d for d, _ in rows["ndx"]]
    ndx = [c for _, c in rows["ndx"]]
    al = {k: align(dates, rows.get(k) or []) for k in ("sox", "vxn", "spx")}
    sig, x = bereken(ndx, al["sox"], al["vxn"], al["spx"])
    n = len(ndx)
    f40, f60 = vooruit(ndx, H10), vooruit(ndx, H15)
    nd = {k: sum(1 for v in sig[k] if v is not None) for k in sig}
    kort = [k for k in sig if 0 < nd[k] < MIN_HIST]                      # afgekapte bron: telt niet mee
    oud = [k for k in sig if nd[k] >= MIN_HIST and sig[k][-1] is None]   # geen actuele waarde: telt niet mee
    use = [k for k in sig if nd[k] >= MIN_HIST and sig[k][-1] is not None]
    core = [k for k in use if not OPT[k]]
    mask = {k: mix_mask(sig[k], f40, dates) for k in use if OPT[k]}
    c0 = max((next(i for i, v in enumerate(sig[k]) if v is not None) for k in core), default=n)
    cnt, lv = [None] * n, [None] * n
    for i in range(c0, n):
        if any(sig[k][i] is None for k in core):      # gat in een kernbron: deze dag telt niet mee (ook niet als 0)
            continue
        c = sum(1 for k in core if sig[k][i]) + sum(1 for k, m in mask.items() if m[i] and sig[k][i])
        cnt[i], lv[i] = c, level_of(c)
    return {"dates": dates, "ndx": ndx, "al": al, "sig": sig, "x": x, "f40": f40, "f60": f60, "use": use,
            "kort": kort, "oud": oud, "core": core, "mask": mask, "c0": c0, "cnt": cnt, "lv": lv}


# ── Backtest ─────────────────────────────────────────────────────────────
def kans(idx, f, thr):
    xs = [f[i] for i in idx if f[i] is not None]
    return r(100 * sum(1 for v in xs if v <= -thr) / len(xs)) if xs else None


def deel(on, defined, f40):
    """Trefkans per dag en per episode voor een deel van de dagen; n en n_ep tellen alleen dagen met bekende uitkomst."""
    on = [i for i in on if f40[i] is not None]
    eps = episodes(on)
    hit, hit_ep, base = kans(on, f40, 10), kans(eps, f40, 10), kans(defined, f40, 10)
    return {"n": len(on), "n_ep": len(eps), "hit": hit, "hit_ep": hit_ep, "base": base,
            "lift": lift(hit, base), "lift_ep": lift(hit_ep, base)}


def stats(on, defined, f40, f60, dd):
    """Trefkans (dagen en eerste dag per episode), normale kans en lift voor een reeks signaaldagen; plus dezelfde
    vergelijking alleen op dagen bij de top ('top': binnen 5% van de 52-weekse top) en in een daling ('dal')."""
    eps = episodes(on)
    hit, hit_ep, base = kans(on, f40, 10), kans(eps, f40, 10), kans(defined, f40, 10)
    hit15, base15 = kans(on, f60, 15), kans(defined, f60, 15)
    out = {"n_days": len(on), "n_ep": len(eps), "n_known": sum(1 for i in on if f40[i] is not None),
           "n_ep_known": sum(1 for i in eps if f40[i] is not None),
           "hit": hit, "hit_ep": hit_ep, "base": base, "lift": lift(hit, base), "lift_ep": lift(hit_ep, base),
           "hit15": hit15, "base15": base15, "lift15": lift(hit15, base15),
           "mdd40": r(median_low([f40[i] for i in on if f40[i] is not None] or [None]))}
    for key, top in (("top", True), ("dal", False)):
        part = [i for i in defined if dd[i] is not None and (dd[i] > -TOP_DD) == top]
        keep = set(part)
        out[key] = deel([i for i in on if i in keep], part, f40)
    return out


def correcties(xs):
    """(top, bodem, dag van -10%, einde) als indexen: elke daling van >= CORR% vanaf een top (slotkoersen). Een
    correctie eindigt bij herstel tot de top of een opleving van CORR% vanaf de bodem; daarna telt een nieuwe top.
    Zo tellen ook de golven binnen een lange berenmarkt (2000-2002, 2022) apart mee."""
    out, pk, tr, hit, inc = [], 0, 0, 0, False
    for i in range(1, len(xs)):
        if not inc:
            if xs[i] > xs[pk]:
                pk = i
            elif xs[i] <= xs[pk] * (1 - CORR / 100):
                inc, tr, hit = True, i, i
        else:
            if xs[i] < xs[tr]:
                tr = i
            if xs[i] >= xs[pk] or xs[i] >= xs[tr] * (1 + CORR / 100):
                out.append((pk, tr, hit, i))
                inc, pk = False, i
    if inc:
        out.append((pk, tr, hit, None))
    return out


def status_of(dd):
    x = -(dd or 0)
    return "normaal" if x < 5 else "terugval" if x < 10 else "correctie" if x < 20 else "berenmarkt"


def tail(rows):
    """Kerncijfers op de laatste dag van een eigen reeks (slot, 52-weekse top, gemiddelden)."""
    cs = [c for _, c in rows]
    c, hi = cs[-1], max(cs[-252:])
    s50 = sum(cs[-50:]) / 50 if len(cs) >= 50 else None
    s200 = sum(cs[-200:]) / 200 if len(cs) >= 200 else None
    return {"date": rows[-1][0].isoformat(), "close": r(c, 2), "hi52": r(hi, 2), "dd52": r((c / hi - 1) * 100),
            "sma50": r(s50, 2), "sma200": r(s200, 2), "ext200": r((c / s200 - 1) * 100) if s200 else None}


# ── Uitvoer ──────────────────────────────────────────────────────────────
def build(rows, errors):
    R = reeks(rows)
    dates, ndx, sig, f40, f60, cnt, lv, c0 = (R[k] for k in ("dates", "ndx", "sig", "f40", "f60", "cnt", "lv", "c0"))
    dd = R["x"]["dd52"]
    n, last = len(ndx), len(ndx) - 1
    iso = lambda i: dates[i].isoformat()  # noqa: E731
    errors = list(errors)
    for k, name, _, src, _ in SIGNALS:                    # signalen die niet meetellen: altijd zichtbaar melden
        if k in R["kort"]:
            errors.append(f"{SRC[src][1]}: te korte historie ({sum(1 for v in sig[k] if v is not None)} dagen); "
                          f"'{name}' telt niet mee")
        elif k in R["oud"]:
            errors.append(f"{SRC[src][1]}: geen actuele waarde (laatste koers {rows[src][-1][0].isoformat()}); "
                          f"'{name}' telt vandaag niet mee")

    # 1. per signaal (eigen historie, normaal = alle dagen waarop het signaal bestond)
    signals = []
    for k, name, why, src, opt in SIGNALS:
        col, e = sig[k], {"k": k, "name": name, "uitleg": why, "opt": opt}
        defined = [i for i in range(n) if col[i] is not None]
        if not defined or k in R["kort"]:                 # bron ontbreekt of is afgekapt: signaal overgeslagen
            signals.append({**e, "on": None, "na": True, "in_mix": False})
            continue
        on, j = [i for i in defined if col[i]], last
        while col[last] and j > 0 and col[j - 1]:         # begin van de huidige reeks
            j -= 1
        signals.append({**e, "on": col[last], "in_mix": k in R["use"] and (not opt or R["mask"][k][last]),
                        **({"oud": True} if k in R["oud"] else {}),
                        "since": iso(j) if col[last] else None, "last": iso(on[-1]) if on else None,
                        "from": iso(defined[0]), "share": r(100 * len(on) / len(defined)),
                        **stats(on, defined, f40, f60, dd)})

    # 2. per niveau (vanaf de dag dat alle kernsignalen bestaan; zelfde samenstelling als vandaag; dagen met een gat
    #    in een kernbron tellen niet mee)
    comp = [i for i in range(c0, n) if lv[i] is not None]
    levels = []
    for li, (k, label, _, rule, _) in enumerate(LEVELS):
        st = stats([i for i in comp if lv[i] == li], comp, f40, f60, dd)
        levels.append({"k": k, "label": label, "rule": rule, "share": r(100 * st["n_days"] / len(comp)) if comp else None,
                       "hit10": st["hit"], "hit10_ep": st["hit_ep"], "hit15": st["hit15"],
                       **{x: st[x] for x in ("n_days", "n_ep", "n_ep_known", "lift", "lift_ep", "lift15", "mdd40",
                                             "top", "dal")}})
    base = {"hit10": kans(comp, f40, 10), "hit15": kans(comp, f60, 15),
            "hit10_top": kans([i for i in comp if dd[i] is not None and dd[i] > -TOP_DD], f40, 10),
            "from": iso(comp[0]) if comp else None, "n": len(comp)}

    # 3. correcties >= 10% sinds 2001: gaf de radar vooraf een nieuwe waarschuwing ('verhoogd' of hoger)? Het venster
    #    begint 30 handelsdagen vóór de top, maar nooit vóór het einde van de vorige correctie, en een waarschuwing die
    #    in de vorige correctie begon telt niet: zo telt elke waarschuwing hooguit voor één correctie.
    ws, w0, wl = [None] * n, None, None                   # per dag: begin van de lopende waarschuwing
    for i in range(n):
        if lv[i] is not None and lv[i] >= LV_WARN:
            if wl is None or i - wl >= WARN_GAP:
                w0 = i
            ws[i], wl = w0, i
    corr, prev_end = [], None
    for pk, tr, h, end in correcties(ndx):
        pe, prev_end = prev_end, end
        if dates[pk] < CORR_FROM:
            continue
        lo = max(pk - LEAD, 0 if pe is None else pe + 1)
        win = [j for j in range(lo, h + 1) if lv[j] is not None]
        warn = next((j for j in win if ws[j] is not None and (pe is None or ws[j] > pe)), None)
        pre = [cnt[j] for j in range(lo, pk + 1) if cnt[j] is not None]
        corr.append({"peak": iso(pk), "trough": iso(tr), "depth": r((ndx[tr] / ndx[pk] - 1) * 100), "days": tr - pk,
                     "hit10": iso(h), "end": iso(end) if end is not None else None, "nvt": not win,
                     "top": dd[pk] is not None and dd[pk] > -TOP_DD,   # begon bij de jaartop (anders: golf in een daling)
                     "warn": iso(warn) if warn is not None else None,
                     "lead": pk - warn if warn is not None else None,       # > 0: handelsdagen vóór de top
                     "al": warn is not None and ws[warn] < lo,             # stond al langer dan 30 d vóór de top aan
                     "voor10": h - warn if warn is not None else None,      # handelsdagen vóór de -10%-dag
                     "max_pre": max(pre) if pre else None})
    known = [c for c in corr if not c["nvt"]]
    tops = [c for c in known if c["top"]]
    corr_sum = {"n": len(tops), "pre": sum(1 for c in tops if c["lead"] is not None and c["lead"] >= 0),
                "voor10": sum(1 for c in tops if c["voor10"] is not None and c["voor10"] > 0),
                "n_golf": len(known) - len(tops)}

    # 4. nu
    nd = tail(rows["ndx"])
    nd.update({"roc20": r(100 * chg(ndx, last, 20)) if n > 20 else None,
               "roc60": r(100 * chg(ndx, last, 60)) if n > 60 else None})
    spx = R["al"]["spx"]
    a, b = chg(ndx, last, 63), chg(spx, last, 63)
    nd["rel63"] = r(100 * (a - b)) if a is not None and b is not None else None
    sox, vxn, x = None, None, R["x"]
    if rows.get("sox"):
        sox = tail(rows["sox"])
        sox.update({"status": status_of(sox["dd52"]), "oud": R["al"]["sox"][last] is None,
                    "dd20": r((R["al"]["sox"][last] / x["sx_hi20"][last] - 1) * 100)
                    if x["sx_hi20"][last] and R["al"]["sox"][last] else None,
                    "rel50": r((x["ratio"][last] / x["r50"][last] - 1) * 100)
                    if x["r50"][last] and x["ratio"][last] else None})
    if rows.get("vxn"):
        vs = [c for _, c in rows["vxn"]]
        vxn = {"date": rows["vxn"][-1][0].isoformat(), "level": r(vs[-1], 2), "oud": R["al"]["vxn"][last] is None,
               "chg5": r((vs[-1] / vs[-6] - 1) * 100) if len(vs) > 5 else None,
               "sma50": r(sum(vs[-50:]) / 50, 2) if len(vs) >= 50 else None}
    st = status_of(nd["dd52"])
    word = {"normaal": "normale schommeling", "terugval": "een terugval, nog geen correctie",
            "correctie": "een correctie (10-20%)", "berenmarkt": "een berenmarkt (20% of meer)"}[st]
    text = f"Nasdaq-100 staat {nl(-(nd['dd52'] or 0))}% onder zijn 52-weekse top: {word}."
    if sox:
        text += f" Chips (SOX): {nl(-(sox['dd52'] or 0))}% onder de top ({sox['status']})."
    state = {"status": st, "dd": nd["dd52"], "text": text, "sox_dd": sox["dd52"] if sox else None,
             "sox_status": sox["status"] if sox else None}

    k_now = cnt[last] if cnt[last] is not None else 0
    li = level_of(k_now)
    L, Ls = LEVELS[li], levels[li]
    cond = "top" if dd[last] is not None and dd[last] > -TOP_DD else "dal"
    m = {"cond": cond, **Ls[cond]}                        # vergelijkbare stand: zelfde niveau, ook bij de top/in een daling
    mix = [s for s in signals if s.get("in_mix")]
    ltext = L[4]
    if li > 0 and m["n_ep"] < MIN_EP:
        ltext += f" Let op: te weinig vergelijkbare keren in de historie ({m['n_ep']}) om de kans te schatten."
    elif li > 0 and (m["lift"] is None or m["lift_ep"] is None or min(m["lift"], m["lift_ep"]) < 1.2):
        ltext += (f" Let op: historisch voegde dit niveau bij een vergelijkbare stand weinig toe "
                  f"(×{nl(m['lift_ep'], 2)} per keer, ×{nl(m['lift'], 2)} per dag de normale kans).")
    level = {"k": L[0], "label": L[1], "n_on": k_now, "n_max": len(mix), "on": [s["k"] for s in mix if s["on"]],
             "text": ltext, "hit": Ls["hit10"], "hit_ep": Ls["hit10_ep"], "base": base["hit10"], "lift": Ls["lift"],
             "lift_ep": Ls["lift_ep"], "hit15": Ls["hit15"], "base15": base["hit15"], "n_days": Ls["n_days"],
             "n_ep": Ls["n_ep"], "m": m}

    sp = max(0, n - 260)
    s200 = R["x"]["s200"]
    return {
        "version": VERSION,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": dates[last].isoformat(),
        "sources": {k: f"{rows[k][0][0].year}–nu" for k in ("ndx", "sox", "vxn", "spx") if rows.get(k)},
        "source_errors": errors,
        "horizon": {"h10": H10, "h15": H15, "ep_gap": EP_GAP, "lead": LEAD, "top": round(TOP_DD * 100),
                    "min_ep": MIN_EP},
        "ndx": nd, "sox": sox, "vxn": vxn, "state": state,
        "signals": signals, "level": level, "levels": levels, "base": base,
        "corrections": corr[-15:], "corr_sum": corr_sum,
        "spark": {"start": iso(sp), "c": [round(c) for c in ndx[sp:]],
                  "s200": [round(v) if v else None for v in s200[sp:]]},
    }


def melden(prev, out):
    """Pushbericht via ntfy.sh zodra het niveau 'verhoogd' of hoger is en dat niveau in deze waarschuwing nog niet gemeld
    is. Maatstaf is de laatst gelukte melding (notified), niet de vorige tech.json: een stijging naar 'hoog' komt dus
    ook binnen 24 uur door, een bron die één run wegvalt geeft geen dubbele melding, en een mislukte melding probeert de
    volgende run opnieuw. Een nieuwe waarschuwing na een rustige periode hooguit eens per 24 uur. Alleen
    marktinformatie. Geeft de nieuwe waarde voor 'notified' terug."""
    note = prev.get("notified") if isinstance(prev.get("notified"), dict) else None
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or not TOPIC_RE.fullmatch(topic):
        if topic:
            print("  NTFY_TOPIC ongeldig: geen pushbericht")
        return note
    rank = {lv[0]: i for i, lv in enumerate(LEVELS)}
    new = out["level"]["k"]
    if not isinstance(prev.get("level"), dict):          # eerste run: geen melding, wel het startpunt vastleggen
        return {"level": new, "at": None} if rank[new] >= LV_WARN else note
    done = rank.get((note or {}).get("level"), -1)       # hoogste niveau dat in deze waarschuwing al gemeld is
    age = age_min((note or {}).get("at"))
    recent = age is not None and age < 24 * 60
    if rank[new] < LV_WARN:
        # waarschuwing voorbij: pas na 24 uur en bij een run zonder bronfouten weer klaar voor een nieuwe melding
        if done >= LV_WARN and not recent and not out.get("source_errors"):
            return {**note, "level": new}
        return note
    if rank[new] <= done:
        return note
    if done < LV_WARN and recent:
        print("  nieuwe waarschuwing binnen 24 uur na de vorige melding: nog geen pushbericht")
        return note
    L, nd, names, m = out["level"], out["ndx"], {s["k"]: s["name"].lower() for s in out["signals"]}, out["level"].get("m") or {}
    hist = ""
    if m.get("hit_ep") is not None and (m.get("n_ep") or 0) >= MIN_EP:
        hist = (f"Historisch volgde bij dit niveau ({'dicht bij de top' if m.get('cond') == 'top' else 'al onder de top'}) "
                f"na {nl(m['hit_ep'], 0)}% van de keren binnen 2 maanden een daling van 10%+ (normaal {nl(m['base'], 0)}%). ")
    msg = (f"Techradar {L['label'].lower()}: {L['n_on']} signalen tegelijk ({', '.join(names[k] for k in L['on'])}). "
           f"Nasdaq-100 {nl(-(nd['dd52'] or 0))}% onder de jaartop"
           + (f", chips {nl(-(out['sox']['dd52'] or 0))}%" if out.get("sox") else "") + ". " + hist + "Geen advies.")
    try:
        req = Request(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), method="POST",
                      headers={"Title": f"Techradar: {L['label']}", "Tags": "warning",
                               "Priority": "high" if new == "hoog" else "default", "User-Agent": UA})
        with urlopen(req, timeout=15) as resp:
            resp.read()
        print(f"  pushbericht verstuurd ({new})")
        return {"level": new, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        print(f"  pushbericht mislukt (volgende run opnieuw): {e}")
        return note


def age_min(iso):
    """Minuten sinds een ISO-tijdstempel; None als die ontbreekt of onleesbaar is."""
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return None


def run(force=False):
    prev = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:  # noqa: BLE001
        pass
    prev = prev if isinstance(prev, dict) else {}
    age = age_min(prev.get("updated"))
    if age is not None and age < FRESH_MIN and prev.get("version") == VERSION and not force:
        print(f"tech.json is {age:.0f} min oud: niets te doen")
        return

    t0 = time.monotonic()
    rows = {"ndx": yahoo_daily("^NDX")}                     # zonder Nasdaq-100 geen radar
    if len(rows["ndx"]) < MIN_HIST + 260:                   # afgekapt antwoord: oude tech.json laten staan
        raise RuntimeError(f"Nasdaq-100: te korte historie ({len(rows['ndx'])} dagen)")
    print(f"Nasdaq-100: {len(rows['ndx'])} dagen, {rows['ndx'][0][0]} t/m {rows['ndx'][-1][0]}")
    errors = []
    for key in ("sox", "vxn", "spx"):
        sym, name = SRC[key]
        if time.monotonic() - t0 > BUDGET_S:
            errors.append(f"{name}: overgeslagen (tijdslimiet)")
            continue
        try:
            rows[key] = yahoo_daily(sym)
            print(f"  {name}: ok ({len(rows[key])})")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:160]}")
            print(f"  {name}: bron mislukt ({e})")

    out = build(rows, errors)
    try:
        out["notified"] = melden(prev, out)
    except Exception as e:  # noqa: BLE001  (een pushbericht mag de radar nooit tegenhouden)
        print(f"  pushbericht overgeslagen: {e}")
        out["notified"] = prev.get("notified")
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, OUT)
    L = out["level"]
    print(f"tech.json: {L['k']} · {L['n_on']} signalen ({', '.join(L['on']) or 'geen'}) · "
          f"Nasdaq-100 {nl(out['ndx']['dd52'])}% t.o.v. jaartop · {len(out['corrections'])} correcties getoond")
    for e in out["source_errors"]:
        print(f"  let op: {e}")


def main():
    try:
        run("--force" in sys.argv)
    except Exception as e:  # noqa: BLE001
        print(f"Techradar niet bijgewerkt: {e}")
        traceback.print_exc(limit=3)
        sys.exit(0 if os.path.exists(OUT) else 1)   # oude tech.json blijft staan: de lus loopt door


if __name__ == "__main__":
    main()
