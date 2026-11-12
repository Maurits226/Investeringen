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
dates, ndx, x, sig, cnt = R["dates"], R["ndx"], R["x"], R["sig"], R["cnt"]
n, c0 = len(ndx), R["c0"]
dd, dd_low, low, top = x["dd"], x["dd_low"], x["low"], x["top"]
H = 60


def fmin(i, h):
    return min(ndx[i + 1:i + h + 1]) if i + h < n else None


def first_reach(cond, start=c0, need_dip=True):
    """Per dal (index van het laagste slot sinds de top) de eerste dag waarop cond(i) waar is."""
    seen, out = set(), []
    for i in range(max(start, 252), n):
        if dd_low[i] > -B.DIP or (need_dip and dd[i] > -B.DIP):
            continue
        if low[i] in seen or i == low[i]:               # op de dag van een nieuw dal kan het dal niet 'bereikt' zijn
            continue
        if cond(i):
            seen.add(low[i])
            out.append(i)
    return out


def base_age(a, h=H, start=c0):
    """Kans dat een dal dat al 'a' handelsdagen niet gebroken is, ook de h dagen daarna standhoudt (alle dalen in een dip)."""
    seen, ok, tot = set(), 0, 0
    for i in range(max(start, 252), n):
        if dd_low[i] > -B.DIP or i - low[i] != a or low[i] in seen:
            continue
        seen.add(low[i])
        m = fmin(i, h)
        if m is None:
            continue
        tot += 1
        ok += m >= ndx[low[i]]
    return (100 * ok / tot if tot else None), tot


BA = {a: base_age(a) for a in range(0, 41)}
print("Basis: kans dat het dal 60 d standhoudt naar leeftijd van het dal (handelsdagen): "
      + " · ".join(f"{a}d {BA[a][0]:.0f}% (n={BA[a][1]})" for a in (0, 1, 2, 3, 4, 5, 7, 10, 15, 20, 30) if BA[a][0] is not None))


def report(name, cond, **kw):
    ev = first_reach(cond, **kw)
    known = [i for i in ev if fmin(i, H) is not None]
    if not known:
        print(f"{name:<34} geen gevallen")
        return
    hold = [fmin(i, H) >= ndx[low[i]] for i in known]
    k120 = [i for i in known if fmin(i, 120) is not None]
    h120 = [fmin(i, 120) >= ndx[low[i]] for i in k120]
    age = sorted(i - low[i] for i in known)
    above = sorted((ndx[i] / ndx[low[i]] - 1) * 100 for i in known)
    fwd = sorted((ndx[i + H] / ndx[i] - 1) * 100 for i in known)
    matched = [BA[min(40, i - low[i])][0] for i in known if BA[min(40, i - low[i])][0] is not None]
    deep = [h for i, h in zip(known, hold) if dd_low[i] <= -20]
    shal = [h for i, h in zip(known, hold) if dd_low[i] > -20]
    miss = sorted((fmin(i, H) / ndx[low[i]] - 1) * 100 for i, h in zip(known, hold) if not h)
    print(f"{name:<34} n={len(known):>3} · hield {100 * sum(hold) / len(hold):>3.0f}% (zelfde leeftijd dal: {sum(matched) / len(matched):.0f}%) · 120d {100 * sum(h120) / max(1, len(h120)):.0f}%"
          f" · dal {age[len(age) // 2]} d oud, +{above[len(above) // 2]:.1f}% boven dal · 60d later mediaan {fwd[len(fwd) // 2]:+.1f}%"
          f" · dal <20%: {sum(shal)}/{len(shal)} · dal 20%+: {sum(deep)}/{len(deep)}"
          f" · mis: mediaan {miss[len(miss) // 2] if miss else 0:.1f}% lager")


fast = lambda i: sum(1 for k in ("termijn", "angstpiek", "rsi") if sig[k][i])  # noqa: E731
print("\n== Eerste keer per dal dat de stand bereikt werd (in een dip, vanaf 2006) ==")
for k in R["use"]:
    report(k, lambda i, k=k: bool(sig[k][i]))
report("termijn, dal 10%+", lambda i: bool(sig["termijn"][i]) and dd_low[i] <= -10)
for c in (1, 2, 3, 4):
    report(f"{c}+ signalen", lambda i, c=c: cnt[i] is not None and cnt[i] >= c)
report("snel: 1+ van termijn/angst/rsi", lambda i: fast(i) >= 1)
report("snel: 2+ van termijn/angst/rsi", lambda i: fast(i) >= 2)
report("snel: alle 3", lambda i: fast(i) >= 3)
report("dal_houdt & boven_20d", lambda i: bool(sig["dal_houdt"][i] and sig["boven_20d"][i]))
report("dal_houdt & boven_20d & snel 1+", lambda i: bool(sig["dal_houdt"][i] and sig["boven_20d"][i]) and fast(i) >= 1)
report("boven_20d & snel 1+", lambda i: bool(sig["boven_20d"][i]) and fast(i) >= 1)
report("boven_20d & termijn", lambda i: bool(sig["boven_20d"][i] and sig["termijn"][i]))
for a in (3, 5, 10, 15, 20):
    report(f"dal {a} d oud (zonder signaal)", lambda i, a=a: i - low[i] >= a)
for p in (3, 5, 8):
    report(f"{p}% boven het dal", lambda i, p=p: ndx[i] >= ndx[low[i]] * (1 + p / 100))
print("\n== Zelfde, hele historie van de koerssignalen (vanaf 1986) ==")
for k in ("rsi", "dal_houdt", "boven_20d"):
    report(k, lambda i, k=k: bool(sig[k][i]), start=252)
report("dal_houdt & boven_20d", lambda i: bool(sig["dal_houdt"][i] and sig["boven_20d"][i]), start=252)
print("\n== Ook buiten een dip gerekend vanaf het herstel (dal 5%+, nu binnen 5% van de top telt mee) ==")
report("dal_houdt & boven_20d", lambda i: bool(sig["dal_houdt"][i] and sig["boven_20d"][i]), need_dip=False)
report("termijn", lambda i: bool(sig["termijn"][i]), need_dip=False)
