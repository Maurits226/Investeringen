#!/usr/bin/env python3
"""
Crashradar — schrijft crash.json voor de derde beursklimaat-tegel in signals.html.

Doel: zien wanneer het verstandig is cash klaar te hebben om een dip te kopen.
Alles wordt elke keer opnieuw nagerekend op de S&P 500 sinds 1950:
  * per waarschuwingssignaal: hoe vaak volgde binnen 12 maanden een daling van 10% / 20%
  * per aantal signalen tegelijk: dezelfde kansen (dat is de meter in de tegel)
  * alle berenmarkten (>= 20% onder de top) en welke signalen er vooraf brandden
  * dip kopen: rendement 12 en 24 maanden na een stand van X% onder de top

Bronnen: Yahoo Finance (S&P 500, 10-jaars- en 3-maandsrente, angstindex) en FRED (inflatie,
werkloosheid). Valt een bron weg, dan telt dat signaal niet mee; de rest blijft werken.
Maandsignalen gebruiken alleen wat op dat moment bekend was (FRED-cijfers één maand vertraagd).

Alleen standaardbibliotheek. Draait in de lus van de Patroonradar; hooguit eens per FRESH_MIN minuten.
"""
import csv
import io
import json
import math
import os
import sys
import time
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "crash.json")
FRESH_MIN = 55               # niet vaker opnieuw rekenen dan eens per zoveel minuten
START = date(1950, 1, 1)     # vanaf hier tellen maanden en crashes mee
FETCH_FROM = date(1939, 1, 1)  # eerder ophalen voor het 10-jaarsgemiddelde
HORIZON_D = 365              # "binnen 12 maanden"
BEAR = 20.0                  # berenmarkt: >= 20% onder de top (slotkoersen)
LADDER = [10, 20, 30]        # gespreid kopen bij zoveel % onder de top
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ── Data ─────────────────────────────────────────────────────────────────
def get(url, tries=3, timeout=40):
    err = None
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url[:80]}: {err}")


def yahoo_daily(sym, start=FETCH_FROM):
    """[(date, close)] oplopend."""
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    last = None
    for host in ("query1", "query2"):
        try:
            j = json.loads(get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(sym)}"
                               f"?period1={p1}&period2={p2}&interval=1d&includePrePost=false"))
            res = j["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            out = [(datetime.fromtimestamp(t, tz=timezone.utc).date(), c)
                   for t, c in zip(res.get("timestamp") or [], closes) if c]
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{sym}: {last}")


def fred_monthly(series):
    """{'JJJJ-MM': waarde} uit FRED (geen sleutel nodig)."""
    txt = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}").decode()
    out = {}
    for row in csv.reader(io.StringIO(txt)):
        if len(row) >= 2 and row[0][:4].isdigit():
            try:
                out[row[0][:7]] = float(row[1])
            except ValueError:
                pass
    return out


def ym(d):
    return d.strftime("%Y-%m")


def month_add(m, k):
    y, mo = int(m[:4]), int(m[5:]) + k
    y += (mo - 1) // 12
    return f"{y:04d}-{(mo - 1) % 12 + 1:02d}"


def month_ends(daily):
    """{'JJJJ-MM': (datum, slot)} = laatste handelsdag van elke maand (lopende maand: laatste koers)."""
    out = {}
    for d, c in daily:
        out[ym(d)] = (d, c)
    return out


# ── Signalen per maand ───────────────────────────────────────────────────
SIGNALS = [  # sleutel, naam, uitleg
    ("trend", "Onder 10-maands gemiddelde", "S&P 500 sluit de maand onder het gemiddelde van de laatste 10 maanden: de trend is gebroken."),
    ("mom", "12 maanden negatief", "S&P 500 staat lager dan een jaar geleden."),
    ("val", "Ver boven langetermijntrend", "S&P 500 staat 1,7 keer of meer boven zijn 10-jaarsgemiddelde: duur, weinig buffer."),
    ("vol", "Hoge beweeglijkheid", "De afgelopen maand bewoog de S&P 500 met meer dan 25% op jaarbasis: onrust."),
    ("curve", "Rentecurve omgekeerd", "In de afgelopen 18 maanden stond de 3-maandsrente boven de 10-jaarsrente: klassieke recessievoorspeller."),
    ("infl", "Inflatie boven 5%", "Amerikaanse inflatie (CPI) hoger dan 5% op jaarbasis: rente omhoog, druk op winsten."),
    ("sahm", "Werkloosheid loopt op", "Werkloosheid (3-mnd gem.) 0,5 procentpunt boven het laagste punt van het jaar ervoor (Sahm-regel): recessie."),
]


def build_signals(months, me, daily, tnx, irx, cpi, unr):
    """Per maand een dict sleutel -> True/False/None (None = geen data)."""
    closes = [me[m][1] for m in months]
    d_dates = [d for d, _ in daily]
    d_close = [c for _, c in daily]
    spread = {}
    for m in months:
        if m in tnx and m in irx:
            spread[m] = tnx[m][1] - irx[m][1]
    def known(series, m):
        """Laatst gepubliceerde maand op het einde van maand m: m-1 (of eerder als die er nog niet is)."""
        for k in (1, 2, 3):
            if month_add(m, -k) in series:
                return month_add(m, -k)
        return None
    out = {}
    for i, m in enumerate(months):
        s = {}
        c = closes[i]
        s["trend"] = c < sum(closes[i - 9:i + 1]) / 10 if i >= 9 else None
        s["mom"] = c < closes[i - 12] if i >= 12 else None
        s["val"] = c >= 1.7 * sum(closes[i - 119:i + 1]) / 120 if i >= 119 else None
        # gerealiseerde beweeglijkheid over de laatste 21 handelsdagen tot het einde van de maand
        j = bisect_right(d_dates, me[m][0]) - 1
        if j >= 22:
            rets = [math.log(d_close[k] / d_close[k - 1]) for k in range(j - 20, j + 1)]
            mu = sum(rets) / len(rets)
            sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
            s["vol"] = sd >= 0.25
        else:
            s["vol"] = None
        sp = [spread[month_add(m, -k)] for k in range(18) if month_add(m, -k) in spread]
        s["curve"] = (min(sp) < 0) if len(sp) >= 12 else None
        km = known(cpi, m)
        c0, c12 = (cpi.get(km), cpi.get(month_add(km, -12))) if km else (None, None)
        s["infl"] = (c0 / c12 - 1) * 100 > 5 if c0 and c12 else None
        ku = known(unr, m)
        u = [unr.get(month_add(ku, -k)) for k in range(0, 15)] if ku else [None]   # laatst bekende maand en 14 ervoor
        if all(x is not None for x in u):
            avg3 = lambda a: sum(a) / 3  # noqa: E731
            now3 = avg3(u[0:3])
            low = min(avg3(u[k:k + 3]) for k in range(1, 13))
            s["sahm"] = now3 - low >= 0.5
        else:
            s["sahm"] = None
        out[m] = s
    return out


# ── Statistiek ───────────────────────────────────────────────────────────
def fwd_drop(daily_dates, daily_close, d, c):
    """Grootste daling (%) vanaf slot c op datum d binnen HORIZON_D dagen; None als die periode nog niet om is."""
    end = d + timedelta(days=HORIZON_D)
    if end > daily_dates[-1]:
        return None
    a, b = bisect_right(daily_dates, d), bisect_right(daily_dates, end)
    return (min(daily_close[a:b]) / c - 1) * 100 if b > a else None


def pct(xs, thr):
    return round(100 * sum(1 for x in xs if x <= -thr) / len(xs), 1) if xs else None


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def bear_markets(daily):
    """Alle dalingen van >= BEAR% (slotkoersen) sinds START: top, bodem, herstel."""
    out, peak, pk_d, inb, trough, tr_d, hit10 = [], None, None, False, None, None, None
    for d, c in daily:
        if peak is None or (not inb and c > peak):
            peak, pk_d, hit10 = c, d, None
            continue
        dd = (c / peak - 1) * 100
        if hit10 is None and dd <= -10:
            hit10 = d
        if not inb and dd <= -BEAR:
            inb, trough, tr_d = True, c, d
        if inb:
            if c < trough:
                trough, tr_d = c, d
            if c >= peak:
                out.append({"peak": pk_d, "trough": tr_d, "depth": (trough / peak - 1) * 100, "rec": d, "hit10": hit10})
                inb, peak, pk_d, hit10 = False, c, d, None
    if inb:
        out.append({"peak": pk_d, "trough": tr_d, "depth": (trough / peak - 1) * 100, "rec": None, "hit10": hit10})
    return [b for b in out if b["peak"] >= START]


def main():
    force = "--force" in sys.argv
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(prev["updated"])).total_seconds() / 60
        if age < FRESH_MIN and not force:
            print(f"crash.json is {age:.0f} min oud: niets te doen")
            return
    except Exception:  # noqa: BLE001
        pass

    daily = yahoo_daily("^GSPC")
    print(f"S&P 500: {len(daily)} dagen, {daily[0][0]} t/m {daily[-1][0]}")
    sources = {"S&P 500": f"{daily[0][0].year}–nu"}

    def opt(name, fn):
        try:
            v = fn()
            print(f"  {name}: ok ({len(v)})")
            return v
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: niet beschikbaar ({e})")
            return {}

    def yahoo_me(sym):
        rows = yahoo_daily(sym)
        me = month_ends(rows)
        if me and max(c for _, c in me.values()) > 25:        # oude notering x10: terug naar procenten
            me = {k: (d, c / 10) for k, (d, c) in me.items()}
        return me

    tnx = opt("10-jaarsrente (^TNX)", lambda: yahoo_me("^TNX"))
    irx = opt("3-maandsrente (^IRX)", lambda: yahoo_me("^IRX"))
    cpi = opt("inflatie (FRED CPIAUCSL)", lambda: fred_monthly("CPIAUCSL"))
    unr = opt("werkloosheid (FRED UNRATE)", lambda: fred_monthly("UNRATE"))

    me = month_ends(daily)
    months = sorted(me)
    sig_all = build_signals(months, me, daily, tnx, irx, cpi, unr)
    d_dates = [d for d, _ in daily]
    d_close = [c for _, c in daily]

    # maanden vanaf 1950 met bekende afloop (12 maanden later)
    lab = [m for m in months if m >= ym(START)]
    drop = {m: fwd_drop(d_dates, d_close, *me[m]) for m in lab}
    known = [m for m in lab if drop[m] is not None]
    base = {"n": len(known), "p10": pct([drop[m] for m in known], 10), "p20": pct([drop[m] for m in known], 20)}

    # 1. per signaal
    sig_stats = []
    for key, name, why in SIGNALS:
        defined = [m for m in known if sig_all[m][key] is not None]
        if not defined:
            continue
        on = [m for m in defined if sig_all[m][key]]
        b10, b20 = pct([drop[m] for m in defined], 10), pct([drop[m] for m in defined], 20)
        p10, p20 = pct([drop[m] for m in on], 10), pct([drop[m] for m in on], 20)
        sig_stats.append({"key": key, "name": name, "why": why, "since": defined[0][:4], "n": len(on),
                          "p10": p10, "p20": p20, "b10": b10, "b20": b20,
                          "lift10": round(p10 / b10, 2) if p10 is not None and b10 else None,
                          "lift20": round(p20 / b20, 2) if p20 is not None and b20 else None})
    use = [s["key"] for s in sig_stats]

    def count(m):
        return sum(1 for k in use if sig_all[m].get(k))

    # 2. per aantal signalen tegelijk
    by_k = []
    for k in range(0, 5):
        xs = [m for m in known if (count(m) == k if k < 4 else count(m) >= 4)]
        if xs:
            by_k.append({"k": k, "plus": k == 4, "n": len(xs), "share": round(100 * len(xs) / len(known), 1),
                         "p10": pct([drop[m] for m in xs], 10), "p20": pct([drop[m] for m in xs], 20),
                         "med12": round(median([(me[month_add(m, 12)][1] / me[m][1] - 1) * 100
                                                for m in xs if month_add(m, 12) in me]) or 0, 1)})

    # 3. berenmarkten en wat er vooraf brandde
    bears = []
    for b in bear_markets(daily):
        pre = sorted({k for i in range(0, 13) for k in use
                      if sig_all.get(month_add(ym(b["peak"]), -i), {}).get(k)}, key=use.index)
        at10 = sorted({k for k in use if b["hit10"] and sig_all.get(ym(b["hit10"]), {}).get(k)}, key=use.index)
        mx = max((count(month_add(ym(b["peak"]), -i)) for i in range(0, 13) if month_add(ym(b["peak"]), -i) in sig_all), default=0)
        bears.append({"peak": b["peak"].isoformat(), "trough": b["trough"].isoformat(), "depth": round(b["depth"], 1),
                      "days": (b["trough"] - b["peak"]).days, "rec": b["rec"].isoformat() if b["rec"] else None,
                      "hit10": b["hit10"].isoformat() if b["hit10"] else None, "pre": pre, "at10": at10, "max_pre": mx})

    # 4. dip kopen: X% onder de top (slot einde maand) -> rendement 12/24 maanden later
    ath, run = {}, 0
    j = 0
    for m in months:
        while j < len(daily) and daily[j][0] <= me[m][0]:
            run = max(run, daily[j][1])
            j += 1
        ath[m] = run
    dips = []
    for lo, hi in [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 100)]:
        xs = [m for m in lab if lo <= (1 - me[m][1] / ath[m]) * 100 < hi]
        r12 = [(me[month_add(m, 12)][1] / me[m][1] - 1) * 100 for m in xs if month_add(m, 12) in me and month_add(m, 12) != months[-1]]
        r24 = [(me[month_add(m, 24)][1] / me[m][1] - 1) * 100 for m in xs if month_add(m, 24) in me and month_add(m, 24) != months[-1]]
        if r12:
            dips.append({"lo": lo, "hi": hi, "n": len(r12), "med12": round(median(r12), 1),
                         "pos12": round(100 * sum(1 for x in r12 if x > 0) / len(r12)), "worst12": round(min(r12), 1),
                         "med24": round(median(r24), 1) if r24 else None,
                         "pos24": round(100 * sum(1 for x in r24 if x > 0) / len(r24)) if r24 else None})

    # 5. nu
    last_d, last_c = daily[-1]
    top = max(daily, key=lambda x: x[1])
    dd = (1 - last_c / top[1]) * 100
    now_m = months[-1]
    now_sig = sig_all[now_m]
    k_now = count(now_m)
    row = next((r for r in by_k if r["k"] == min(k_now, 4)), None)
    sma200 = sum(d_close[-200:]) / 200 if len(d_close) >= 200 else None
    elevated = row and base["p10"] and row["p10"] >= 1.5 * base["p10"]
    if dd >= 20:
        state = "koop"
    elif dd >= 10:
        state = "correctie"
    elif elevated:
        state = "opletten"
    else:
        state = "rustig"
    bucket = next((x for x in dips if x["lo"] <= dd < x["hi"]), None)
    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {**sources, **{k: v for k, v in {"rente": "1962–nu" if tnx and irx else None,
                                                    "inflatie": "FRED" if cpi else None,
                                                    "werkloosheid": "FRED" if unr else None}.items() if v}},
        "now": {"date": last_d.isoformat(), "close": round(last_c, 2), "top": round(top[1], 2), "top_date": top[0].isoformat(),
                "dd": round(dd, 1), "above200": last_c > sma200 if sma200 else None, "sma200": round(sma200, 2) if sma200 else None,
                "month": now_m, "signals": {k: now_sig.get(k) for k in use}, "k": k_now, "row": row, "state": state,
                "ladder": [{"pct": p, "level": round(top[1] * (1 - p / 100), 2), "hit": dd >= p} for p in LADDER],
                "dip": bucket,
                "curve": round(tnx[now_m][1] - irx[now_m][1], 2) if now_m in tnx and now_m in irx else None},
        "base": base,
        "signals": sig_stats,
        "by_k": by_k,
        "bears": bears,
        "dips": dips,
        # maandreeks voor de grafiek: stand onder de top en het aantal signalen
        "series": [[m, round((1 - me[m][1] / ath[m]) * 100, 1), count(m)] for m in lab],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"crash.json: {state} · {k_now} signalen ({', '.join(k for k in use if now_sig.get(k)) or 'geen'}) · "
          f"{dd:.1f}% onder de top · {len(bears)} berenmarkten sinds {START.year}")


if __name__ == "__main__":
    main()
