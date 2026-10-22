"""Synthetische dagkoersen voor ^NDX, ^SOX, ^VXN en ^GSPC (random walk met volatiliteitsregimes en ingespoten correcties)."""
import math
import random
from datetime import date, timedelta


def bdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make(seed=1, end=date(2026, 10, 20), tail_pullback=True):
    rnd = random.Random(seed)
    days = bdays(date(1985, 10, 1), end)
    n = len(days)
    # plan: correcties (start, lengte, diepte) met een rally ervoor
    plan = {}
    i = 300
    while i < n - 120:
        i += rnd.randint(150, 600)
        if i >= n - 120:
            break
        rally = rnd.randint(40, 90)
        depth = rnd.uniform(0.10, 0.32)
        length = rnd.randint(15, 70)
        for k in range(rally):
            plan[i - rally + k] = ("rally", 0.25 / rally)
        for k in range(length):
            plan[i + k] = ("drop", math.log(1 - depth) / length)
        i += length
    if tail_pullback:  # laatste stuk: sterke rit en dan een begin van een terugval met oplopende angst
        for k in range(80):
            plan[n - 100 + k] = ("rally", 0.22 / 80)
        for k in range(20):
            plan[n - 20 + k] = ("drop", math.log(0.93) / 20)
    ndx, sox, spx, vxn = [], [], [], []
    p, s, g, sig = 250.0, 100.0, 180.0, 0.012
    for t in range(n):
        mode = plan.get(t)
        target = 0.011
        mu = 0.00045
        if mode and mode[0] == "drop":
            target, mu = 0.024, mode[1]
        elif mode and mode[0] == "rally":
            target, mu = 0.010, mode[1]
        sig = 0.94 * sig + 0.06 * target + 0.002 * abs(rnd.gauss(0, 1)) * (1 if mode and mode[0] == "drop" else 0.3)
        e = rnd.gauss(0, 1)
        rn = mu + sig * e
        p *= math.exp(rn)
        # chips: hogere beta, en in de laatste 10 dagen van een rally al zwakker (lopen voorop)
        lead = -0.006 if (mode and mode[0] == "rally" and plan.get(t + 8, ("",))[0] == "drop") else 0.0
        s *= math.exp(1.5 * rn + lead + rnd.gauss(0, 0.009))
        g *= math.exp(0.7 * rn + rnd.gauss(0, 0.004) + 0.0001)
        ndx.append((days[t], round(p, 2)))
        sox.append((days[t], round(s, 2)))
        spx.append((days[t], round(g, 2)))
        vxn.append((days[t], round(max(9.0, sig * math.sqrt(252) * 100 * 1.1 + rnd.gauss(0, 1.2)), 2)))
    return {
        "^NDX": ndx,
        "^SOX": [x for x in sox if x[0] >= date(1994, 5, 4)],
        "^VXN": [x for x in vxn if x[0] >= date(2001, 1, 23)],
        "^GSPC": spx,
    }
