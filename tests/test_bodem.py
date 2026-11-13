"""Test van het bodemsignaal (scripts/bodem.py): rekenregels op kleine, uitgeschreven reeksen en de hele uitvoer op
synthetische koersen. Geen netwerk. Draait in de GitHub-check en lokaal: python3 tests/test_bodem.py"""
import json
import math
import os
import random
import sys
from datetime import date

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import bodem as B  # noqa: E402
import synth_tech  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FOUT", msg)


def synth(seed=3, end=date(2026, 11, 12)):
    """Nasdaq-100 uit synth_tech, met een angstindex voor 1 maand (springt mee met de beweeglijkheid) en voor 3 maanden
    (trager): in een scherpe daling komt de korte boven de lange te staan."""
    rnd = random.Random(seed + 100)
    ndx = synth_tech.make(seed=seed, end=end)["^NDX"]
    vx, sig = [], 0.012
    for i, (d, c) in enumerate(ndx):
        r = math.log(c / ndx[i - 1][1]) if i else 0.0
        sig = 0.9 * sig + 0.1 * abs(r) * 1.25
        vx.append((d, round(max(9.0, sig * math.sqrt(252) * 100 * 1.15 + rnd.gauss(0, 0.8)), 2)))
    v3, ema = [], vx[0][1]
    for d, v in vx:
        ema = 0.97 * ema + 0.03 * v
        v3.append((d, round(0.45 * v + 0.55 * ema + 1.2 + rnd.gauss(0, 0.3), 2)))
    return {"ndx": ndx, "vix": [p for p in vx if p[0] >= date(1990, 1, 2)], "v3m": [p for p in v3 if p[0] >= date(2007, 12, 4)]}


def reeks_van(closes, start=date(2020, 1, 1)):
    days = synth_tech.bdays(start, date(2100, 1, 1))[:len(closes)] if len(closes) > 4000 else None
    days = days or synth_tech.bdays(start, date(start.year + 30, 1, 1))[:len(closes)]
    return list(zip(days, closes))


# 1. Top en dal: stijgen naar 100, zakken naar 85, herstel naar 95, nieuw dal 80, dan een nieuwe top
xs = [90, 95, 100, 92, 85, 90, 95, 88, 80, 90, 101, 99]
top, low = B.dal_reeks(xs)
check(top == [0, 1, 2, 2, 2, 2, 2, 2, 2, 2, 10, 10], f"top klopt niet: {top}")
check(low == [0, 1, 2, 3, 4, 4, 4, 4, 8, 8, 10, 11], f"dal klopt niet: {low}")
# de top valt uit het venster: dan telt de hoogste van de laatste n dagen en het dal sinds die dag
top3, low3 = B.dal_reeks([100, 90, 95, 80, 85, 70], n=3)
check(top3 == [0, 0, 0, 2, 2, 4] and low3 == [0, 1, 1, 3, 3, 5], f"voortschrijdende top klopt niet: {top3} {low3}")

# 2. Afloop: het dal van 85 (dag 4) brak op dag 8 (80); het dal van 80 kwam terug op de top zonder lager slot
was, h60 = B.afloop(xs, top, low)
check(was[4] is False and was[7] is False, f"een dal dat later brak moet 'niet het dal' zijn: {was}")
check(was[8] is True and was[9] is True, f"het dal waarna de top terugkwam moet 'het dal' zijn: {was}")
check(was[11] is None, "een dal waarvan de afloop nog open is, is None")
check(all(v is None for v in h60), "zonder 60 dagen vooruit is de 60-dagenuitkomst None")
B.H, _h = 3, B.H
try:
    _, h3 = B.afloop(xs, top, low)
    check(h3[4] is True and h3[5] is False and h3[8] is True and h3[9] is None, f"geen lager slot in de 3 dagen erna: {h3}")
finally:
    B.H = _h

# 3. Dalen per leeftijd: één keer per dal, alleen bij 5% of meer onder de top
dd_low = [(xs[low[i]] / xs[top[i]] - 1) * 100 for i in range(len(xs))]
check(B.dalen(low, dd_low, 1, start=0) == [5, 9], f"dalen van 1 dag oud: {B.dalen(low, dd_low, 1, start=0)}")
check(B.dalen(low, dd_low, 3, start=0) == [7], f"dalen van 3 dagen oud: {B.dalen(low, dd_low, 3, start=0)}")

# 4. Kans loopt op met de leeftijd: buren samennemen waar een latere groep lager uitvalt
check(B.oplopend([1, 3, 2, 4], [1, 1, 1, 1]) == [1, 2.5, 2.5, 4], "oplopend: buren gemiddeld")
check(B.oplopend([50, 40, 30], [10, 10, 20]) == [37.5, 37.5, 37.5], "oplopend: gewogen naar het aantal dalen")
check(B.oplopend([10, 20, 30], [5, 5, 5]) == [10, 20, 30], "oplopend: een reeks die al oploopt blijft staan")

# 5. Klasse, niveau en datum
check([B.klasse_of(d) for d in (4.9, 5, 9.99, 10, 19.9, 20, 55)] == [None, "terugval", "terugval", "correctie", "correctie", "berenmarkt", "berenmarkt"], "klasse naar diepte")
check([B.level_of(p) for p in (None, 49.9, 50, 69.9, 70, 95)] == ["onbekend", "geen", "maak", "maak", "bereikt", "bereikt"], "niveau naar kans")
check(B.werkdag(date(2026, 11, 5), 15) == date(2026, 11, 26) and B.werkdag(date(2026, 11, 6), 1) == date(2026, 11, 9), "werkdagen tellen (weekend overslaan)")

# 6. Termijnstructuur: twee dagen omgekeerd, daarna twee dagen normaal = weer normaal; één dag omgekeerd telt niet
rat = [0.9] * 20 + [1.05, 1.1] + [0.95] * 20
t = B.termijn(rat)
check(t[21] is False and t[22] is False and t[23] is True, f"weer normaal pas na twee normale dagen: {t[20:25]}")
check(t[21 + B.INV_WIN] is True and t[22 + B.INV_WIN] is False, "het signaal dooft uit zodra de omkering uit het venster van 15 dagen valt")
check(not any(B.termijn([0.9] * 20 + [1.05] + [0.95] * 20)[17:]), "één dag omgekeerd is geen omkering")
check(all(v is None for v in B.termijn([None] * 40)), "zonder data geen signaal (None)")

# 7. Hele uitvoer op synthetische koersen
rows = synth()
out = B.build(rows, [])
try:
    json.dumps(out, allow_nan=False)
except ValueError as e:
    check(False, f"uitvoer is geen geldige JSON: {e}")
check(out["level"]["k"] in B.LEVELS and out["dip"]["state"] in ("geen", "dip", "herstel"), f"onbekend niveau of stand: {out['level']['k']} {out['dip']['state']}")
check(len(out["spark"]["ndx"]) == len(out["spark"]["spread"]) == 90, "grafiek: reeksen niet even lang")
for kl in out["tabel"]:
    ps = [r["p"] for r in kl["rows"] if r["n"]]
    check(all(0 <= p <= 100 for p in ps) and ps == sorted(ps), f"{kl['k']}: kans loopt niet op met de leeftijd: {ps}")
    check([r["age"] for r in kl["rows"]] == B.AGES, f"{kl['k']}: leeftijden kloppen niet")
check([x["date"] for x in out["pad"]] == sorted(x["date"] for x in out["pad"]) and all(x["date"] > out["dip"]["low"] for x in out["pad"]),
      "tijdpad: datums niet oplopend of niet na het dal")
st = out["term"]["stat"]
check(st and st["n"] >= st["goed"] >= 0 and 0 <= st["p"] <= 100, f"trefkans termijnstructuur klopt niet: {st}")

# 8. Geen blik vooruit: wat op een dag bekend is, verandert niet als er later koersen bijkomen
R = B.reeks(rows)
cut = len(rows["ndx"]) - 400
part = {k: [p for p in v if p[0] <= rows["ndx"][cut - 1][0]] for k, v in rows.items()}
Rp = B.reeks(part)
for key in ("top", "low", "dd", "dd_low", "term"):
    check(R[key][:cut] == Rp[key], f"'{key}' kijkt vooruit: verandert als er latere koersen bijkomen")

# 9. Een uitgeschreven dip: lang stijgen, 15% zakken, dan 6 dagen iets boven het dal
up = [1000 * 1.0008 ** i for i in range(3200)]
down = [up[-1] * (1 - 0.15 * (i + 1) / 20) for i in range(20)]
flat = [down[-1] * 1.02] * 6
o = B.build({"ndx": reeks_van(up + down + flat, date(2013, 1, 1))}, [])
check(o["dip"]["state"] == "dip" and o["dip"]["klasse"] == "correctie" and o["dip"]["days_low"] == 6 and o["dip"]["top_low"] == 20,
      f"uitgeschreven dip niet herkend: {o['dip']}")
check(o["level"]["kans"]["age"] == 5 and o["level"]["k"] == "onbekend" and o["level"]["kans"]["p"] is None,
      f"te weinig vergelijkbare dalen: geen percentage ({o['level']})")
check(o["term"]["state"] is None and o["term"]["stat"] is None, "zonder angstindex: geen stand en geen trefkans")
o2 = B.build({"ndx": reeks_van(up + down, date(2013, 1, 1))}, [])
check(o2["level"]["k"] == "loopt" and o2["dip"]["days_low"] == 0, f"nieuw dal vandaag = daling loopt nog: {o2['level']}")
o3 = B.build({"ndx": reeks_van(up, date(2013, 1, 1))}, [])
check(o3["level"]["k"] == "geen_dip" and o3["dip"]["state"] == "geen" and o3["pad"] == [], f"op de top is er geen dip: {o3['level']}")

# 10. De run zelf (met nagebootste bronnen): bestand schrijven, niet vaker dan eens per uur, en bij een storing blijft
#     het oude bestand staan; valt alleen de angstindex weg, dan is er wel een kans maar geen termijnstructuur
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402

SYM = {"^NDX": "ndx", "^VIX": "vix", "^VIX3M": "v3m"}
storing = set()


def fake_yahoo(sym, start=None):
    if sym in storing:
        raise RuntimeError(f"{sym}: gesimuleerde storing")
    return rows[SYM[sym]]


_yd, _out = B.T.yahoo_daily, B.OUT
B.T.yahoo_daily = fake_yahoo
with tempfile.TemporaryDirectory() as tmp:
    B.OUT = os.path.join(tmp, "bodem.json")
    try:
        with contextlib.redirect_stdout(io.StringIO()) as log:
            B.run()
            first = json.load(open(B.OUT, encoding="utf-8"))
            B.run()                                          # net geschreven: niets te doen
            check("niets te doen" in log.getvalue(), "een verse bodem.json moet niet opnieuw berekend worden")
            storing.add("^VIX3M")
            B.run(force=True)
            zonder = json.load(open(B.OUT, encoding="utf-8"))
            storing.add("^NDX")
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    B.main()                                 # leest sys.argv: geen --force, dus eerst het bestand oud maken
                except SystemExit as e:
                    check(e.code in (0, None), f"met een oud bestand moet een storing de lus niet breken (code {e.code})")
                zonder["updated"] = "2020-01-01T00:00:00+00:00"
                json.dump(zonder, open(B.OUT, "w", encoding="utf-8"))
                try:
                    B.main()
                    check(False, "storing van de Nasdaq-100 moet de run afbreken")
                except SystemExit as e:
                    check(e.code == 0, f"storing met een bestaand bestand: code 0 verwacht, kreeg {e.code}")
            na = json.load(open(B.OUT, encoding="utf-8"))
        check(first["version"] == B.VERSION and first["level"]["k"] == out["level"]["k"], "de run schrijft niet dezelfde uitkomst als build")
        check(zonder["term"]["state"] is None and zonder["level"]["k"] == first["level"]["k"] and any("VIX3M" in e for e in zonder["source_errors"]),
              f"zonder VIX3M: wel een kans, geen termijnstructuur, en de storing gemeld ({zonder['source_errors']})")
        check(na["updated"] == "2020-01-01T00:00:00+00:00", "bij een storing van de Nasdaq-100 moet het oude bestand blijven staan")
    finally:
        B.T.yahoo_daily, B.OUT = _yd, _out

print(f"BODEM: {len(fails)} fout(en)" if fails else
      f"BODEM: alles in orde · synthetisch: {out['dip']['state']}, {out['level']['k']}, "
      f"{sum(r['n'] for kl in out['tabel'] for r in kl['rows'] if r['age'] == 1)} dalen")
sys.exit(1 if fails else 0)
