"""Test van het bodemsignaal (scripts/bodem.py) op synthetische koersen: geen netwerk.
Draait in de GitHub-check en lokaal: python3 tests/test_bodem.py"""
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


if __name__ == "__main__":
    out = B.build(synth(), [])
    print("BODEM (voorlopige test):", out["dip"]["state"], out["level"]["k"], out["level"]["n_on"], "van", out["level"]["n_max"])
