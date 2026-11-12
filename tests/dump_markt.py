"""TIJDELIJK (alleen op de werkbranch): rekent het bodemsignaal door op de echte historie en zet een verslag in het
logboek, zodat de uitkomst nagekeken kan worden voordat het live gaat. Wordt verwijderd voordat dit naar main gaat."""
import json
import os
import sys
from datetime import date

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bodem as B  # noqa: E402
import tech as T  # noqa: E402


def load():
    if os.environ.get("SYNTH"):
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from test_bodem import synth  # noqa: E402
        return synth()
    rows = {}
    for key, (sym, name) in B.SRC.items():
        rows[key] = T.yahoo_daily(sym, date(1985, 1, 1))
        print(f"{name}: {len(rows[key])} dagen, {rows[key][0][0]} t/m {rows[key][-1][0]}, laatste {rows[key][-1][1]:.2f}")
    return rows


rows = load()
out = B.build(rows, [])
print("JSON:" + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
R = B.reeks(rows)
dates, ndx, x, sig, cnt, lv = R["dates"], R["ndx"], R["x"], R["sig"], R["cnt"], R["lv"]
n = len(ndx)
dd, dd_low, low, top = x["dd"], x["dd_low"], x["low"], x["top"]
print(f"\nc0 (alle signalen bestaan) = {dates[R['c0']] if R['c0'] < n else None} · in gebruik: {R['use']} · kort: {R['kort']} · oud: {R['oud']}")
in_dip = [i for i in range(252, n) if dd[i] <= -B.DIP]
print(f"dagen in een dip: {len(in_dip)} van {n - 252} ({100 * len(in_dip) / (n - 252):.0f}%)")

print("\n== Per signaal, dagen in een dip (hele eigen historie) ==")
for s in out["signals"]:
    print({k: s.get(k) for k in ("k", "on", "since", "from", "n", "n_ep", "hold", "hold_ep", "base", "lift", "lift_ep", "fwd", "pos", "lager")})
print("\n== Per signaal, zelfde periode (vanaf c0) ==")
comp = [i for i in in_dip if i >= R["c0"] and cnt[i] is not None]
for k in R["use"]:
    st = B.stats([i for i in comp if sig[k][i]], comp, R)
    print(k, st)
print("\n== Per niveau ==")
for l in out["levels"]:
    print(l["k"], "dip", l["dip"])
    print("   herstel", l["herstel"])
print("\n== Per aantal signalen (in een dip) ==")
for c in out["per_cnt"]:
    print(c)

# Andere horizonnen (alleen ter controle: verandert het beeld?)
print("\n== Robuustheid: andere horizon voor 'dal hield stand' ==")
for h in (20, 40, 60, 120, 250):
    B.H = h
    hold, lager, fwd = B.uitkomst(ndx, low)
    R2 = dict(R, hold=hold, lager=lager, fwd=fwd)
    row = []
    for li in range(len(B.LEVELS)):
        st = B.stats([i for i in comp if lv[i] == li], comp, R2)
        row.append(f"{B.LEVELS[li][0]}: {st['hold']}% (ep {st['hold_ep']}%, n_ep {st['n_ep']}, fwd {st['fwd']}%)")
    print(f"H={h}: basis {st['base']}% · " + " · ".join(row))
B.H = 60

print("\n== Omkeringen van de termijnstructuur (VIX boven VIX3M) ==")
ratio = x["ratio"]
inv = [i for i in range(n) if ratio[i] is not None and ratio[i] > 1.0]
eps, cur = [], []
for i in inv:
    if cur and i - cur[-1] > B.INV_WIN:
        eps.append(cur)
        cur = []
    cur.append(i)
if cur:
    eps.append(cur)
hold, lager, fwd = B.uitkomst(ndx, low)
print(f"{len(eps)} omkeringen sinds {dates[next(i for i in range(n) if ratio[i] is not None)]}")
good = tot = 0
for e in eps:
    a, b = e[0], e[-1]
    u = next((j for j in range(b + 1, min(n, b + 30)) if sig["termijn"][j]), None)   # eerste dag 'weer normaal'
    if u is None:
        print(f"{dates[a]} t/m {dates[b]} ({len(e)} d, max {max(ratio[i] for i in e):.2f}) · geen 'weer normaal' gevonden")
        continue
    lo_i = low[u]
    after = min(ndx[u + 1:u + 61]) if u + 60 < n else None
    held = None if after is None else after >= ndx[lo_i]
    if held is not None and dd_low[u] <= -B.DIP:
        tot += 1
        good += held
    print(f"{dates[a]} t/m {dates[b]} ({len(e):>3} d, max {max(ratio[i] for i in e):.2f}) · normaal {dates[u]} · NDX {dd[u]:+.1f}% t.o.v. top, dal {dd_low[u]:+.1f}% op {dates[lo_i]} "
          f"({u - lo_i} d eerder) · 60 d later {'—' if fwd[u] is None else f'{fwd[u]:+.1f}%'} · dal hield: {held}"
          f"{'' if held or held is None else f' (nog {lager[u]:.1f}% lager)'}")
print(f"In een dip (5%+): dal hield in {good} van {tot} omkeringen")

print("\n== Alle correcties >= 10% vanaf c0 ==")
for pk, tr, h10, end in T.correcties(ndx):
    if pk < R["c0"]:
        continue
    stop = end if end is not None else n - 1
    ok = [j for j in range(h10, stop + 1) if lv[j] is not None and lv[j] >= B.LV_OK]
    mk = [j for j in range(h10, stop + 1) if lv[j] is not None and lv[j] >= 1]
    f = ok[0] if ok else None
    line = f"{dates[pk]} → {dates[tr]} {((ndx[tr] / ndx[pk] - 1) * 100):+.1f}% in {tr - pk} d (top52w: {dd[pk] > -B.DIP})"
    if f is None:
        line += " · geen 'bereikt'" + (f" · 'in de maak' {dates[mk[0]]} ({mk[0] - tr:+d} d t.o.v. dal)" if mk else "")
    else:
        lo = min(ndx[pk:f + 1])
        na = min(ndx[f + 1:f + 61]) if f + 60 < n else None
        line += (f" · bereikt {dates[f]} ({f - tr:+d} d t.o.v. dal, {((ndx[f] / lo - 1) * 100):+.1f}% boven dal, signalen "
                 f"{[k for k in R['use'] if sig[k][f]]}) · hield: {None if na is None else na >= lo}"
                 f"{'' if na is None or na >= lo else f' ({((na / lo - 1) * 100):.1f}% lager)'}"
                 f" · 60 d later {'—' if f + 60 >= n else f'{((ndx[f + 60] / ndx[f] - 1) * 100):+.1f}%'}")
    print(line)

print("\n== Laatste 30 handelsdagen ==")
for i in range(n - 30, n):
    v, m = R["al"]["vix"][i], R["al"]["v3m"][i]
    print(f"{dates[i]} NDX {ndx[i]:>9.1f} dd {dd[i]:+6.1f}% dal {dd_low[i]:+6.1f}% · VIX {v} VIX3M {m} ratio {ratio[i] if ratio[i] is None else round(ratio[i], 3)} · RSI {x['rsi'][i]:.0f} · "
          f"signalen {[k for k in sig if sig[k][i]]} · niveau {lv[i]}")
