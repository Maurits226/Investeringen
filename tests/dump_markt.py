"""TIJDELIJK (alleen op de werkbranch): rekent het bodemsignaal door op de echte historie en zet een kort verslag in het
logboek. Wordt verwijderd voordat dit naar main gaat."""
import os
import sys
from datetime import date

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bodem as B  # noqa: E402
import tech as T  # noqa: E402

if os.environ.get("SYNTH"):
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_bodem import synth  # noqa: E402
    rows = synth()
else:
    rows = {key: T.yahoo_daily(sym, date(1985, 1, 1)) for key, (sym, _) in B.SRC.items()}
R = B.reeks(rows)
dates, ndx, x, sig = R["dates"], R["ndx"], R["x"], R["sig"]
n, c0 = len(ndx), R["c0"]
dd, dd_low, low, top, ratio = x["dd"], x["dd_low"], x["low"], x["top"], x["ratio"]
vix, v250 = R["al"]["vix"], x["v250"]


def fmin(i, h):
    return min(ndx[i + 1:i + h + 1]) if i + h < n else None


def final(i):
    """Was dit het dal van de dip? True = de koers kwam terug op de top zonder lager slot, False = lager slot, None = loopt nog."""
    lo, hi = ndx[low[i]], ndx[top[i]]
    for j in range(i + 1, n):
        if ndx[j] < lo:
            return False
        if ndx[j] >= hi:
            return True
    return None


def troughs(start, age):
    """Alle dalen (5%+ onder de top) die 'age' handelsdagen standhielden: de dag waarop ze die leeftijd bereikten."""
    seen, out = set(), []
    for i in range(max(start, 252), n):
        if dd_low[i] <= -B.DIP and i - low[i] == age and low[i] not in seen:
            seen.add(low[i])
            out.append(i)
    return out


def panic(i, w=5):
    """Paniekdal: rond het dal (5 handelsdagen ervoor t/m de dag zelf... en erna tot dag i) stond de VIX boven de VIX3M."""
    L = low[i]
    return any(ratio[j] is not None and ratio[j] > 1.0 for j in range(max(0, L - w), i + 1))


def spike(i, w=5):
    L = low[i]
    return any(vix[j] is not None and v250[j] and vix[j] >= 1.5 * v250[j] for j in range(max(0, L - w), i + 1))


def rate(days, f):
    xs = [f(i) for i in days]
    xs = [v for v in xs if v is not None]
    return (f"{100 * sum(xs) / len(xs):>3.0f}% (n={len(xs)})" if xs else "  —")


h60 = lambda i: None if fmin(i, 60) is None else fmin(i, 60) >= ndx[low[i]]  # noqa: E731
h20 = lambda i: None if fmin(i, 20) is None else fmin(i, 20) >= ndx[low[i]]  # noqa: E731
h120 = lambda i: None if fmin(i, 120) is None else fmin(i, 120) >= ndx[low[i]]  # noqa: E731
print("== Kans dat het dal standhoudt, naar leeftijd van het dal (alle dalen 5%+ onder de top) ==")
for label, start in (("sinds 2006", c0), ("sinds 1986", 252)):
    print(f"-- {label}")
    for a in (1, 2, 3, 5, 7, 10, 15, 20, 30, 40):
        ds = troughs(start, a)
        deep = [i for i in ds if dd_low[i] <= -20]
        mid = [i for i in ds if -20 < dd_low[i] <= -10]
        shal = [i for i in ds if dd_low[i] > -10]
        print(f"{a:>3} d: 20d {rate(ds, h20)} · 60d {rate(ds, h60)} · 120d {rate(ds, h120)} · was het dal {rate(ds, final)}"
              f" | 60d naar diepte: 5-10% {rate(shal, h60)} · 10-20% {rate(mid, h60)} · 20%+ {rate(deep, h60)}"
              f" | was het dal: 5-10% {rate(shal, final)} · 10-20% {rate(mid, final)} · 20%+ {rate(deep, final)}")
print("\n== Paniekdal (VIX boven VIX3M rond het dal) tegenover dal zonder paniek, sinds 2006 ==")
for a in (2, 3, 5, 10, 15, 20):
    ds = troughs(c0, a)
    p, q = [i for i in ds if panic(i)], [i for i in ds if not panic(i)]
    s, t = [i for i in ds if spike(i)], [i for i in ds if not spike(i)]
    print(f"{a:>3} d: omkering ja 60d {rate(p, h60)} dal {rate(p, final)} · nee 60d {rate(q, h60)} dal {rate(q, final)}"
          f" || VIX-piek ja 60d {rate(s, h60)} dal {rate(s, final)} · nee 60d {rate(t, h60)} dal {rate(t, final)}")
print("\n== Paniekdal en diepte 10%+ ==")
for a in (3, 5, 10, 15, 20):
    ds = [i for i in troughs(c0, a) if dd_low[i] <= -10]
    p, q = [i for i in ds if panic(i)], [i for i in ds if not panic(i)]
    print(f"{a:>3} d: omkering ja 60d {rate(p, h60)} dal {rate(p, final)} · nee 60d {rate(q, h60)} dal {rate(q, final)}")

print("\n== Termijnstructuur weer normaal: de dag zelf (eerste keer per dal), sinds 2006 ==")
seen, ev = set(), []
for i in range(max(c0, 252), n):
    if sig["termijn"][i] and not sig["termijn"][i - 1] and dd_low[i] <= -B.DIP and i > low[i] and low[i] not in seen:
        seen.add(low[i])
        ev.append(i)
BA = {}
for a in range(0, 131):
    ds = troughs(c0, a)
    BA[a] = ([h60(i) for i in ds if h60(i) is not None], [final(i) for i in ds if final(i) is not None])
m60 = [sum(BA[min(130, i - low[i])][0]) / max(1, len(BA[min(130, i - low[i])][0])) for i in ev if h60(i) is not None]
mfin = [sum(BA[min(130, i - low[i])][1]) / max(1, len(BA[min(130, i - low[i])][1])) for i in ev if final(i) is not None]
print(f"n={len(ev)} · 60d {rate(ev, h60)} (zelfde leeftijd dal: {100 * sum(m60) / max(1, len(m60)):.0f}%) · was het dal {rate(ev, final)} (zelfde leeftijd: {100 * sum(mfin) / max(1, len(mfin)):.0f}%)"
      f" · diepte 10%+: 60d {rate([i for i in ev if dd_low[i] <= -10], h60)} dal {rate([i for i in ev if dd_low[i] <= -10], final)}"
      f" · 20%+: {rate([i for i in ev if dd_low[i] <= -20], h60)}")
print("missers (60 d): " + " · ".join(f"{dates[i]} (dal {dd_low[i]:.0f}%, {((fmin(i, 60) / ndx[low[i]] - 1) * 100):.1f}% lager)" for i in ev if h60(i) is False))
print("niet het dal: " + " · ".join(f"{dates[i]}" for i in ev if final(i) is False))
print("\n== Nu ==")
i = n - 1
print(f"{dates[i]}: dal {dates[low[i]]} ({dd_low[i]:.1f}%), {i - low[i]} d oud, nu {dd[i]:.1f}% · omkering rond het dal: {panic(i)} · VIX-piek: {spike(i)} · termijn aan: {sig['termijn'][i]}")
