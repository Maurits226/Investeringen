"""Test van het bewijs per pijler en de gewichten van de marktrang (scripts/bewijs.py en evidence() in screener.py).
Rekenregels op kleine, uitgeschreven voorbeelden; terugrekenen op een nagebouwde markt waarin we zelf bepalen welke
pijler werkt; de live-maandstanden; en de koppeling in de Top 250-run op de opgeslagen cache. Geen netwerk.
Draait in de GitHub-check en lokaal: python3 tests/test_bewijs.py"""
import json
import math
import os
import random
import sys
from datetime import date

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bewijs as B  # noqa: E402
import screener as S  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FOUT", msg)


def near(a, b, eps=1e-9):
    return a is not None and b is not None and abs(a - b) <= eps


# 1. Kleine rekenregels
check(B.mno("2026-01-31") == 2026 * 12 and B.ym(B.mno("2025-12-31")) == "2025-12", "maandnummer en terug")
check(B.reeks({"m": 10, "c": [1.0, None, 3.0]}) == {10: 1.0, 12: 3.0} and B.reeks(None) == {}, "maandreeks: ontbrekende maand overslaan")
check(B.rangen([10, 30, 20, 30]) == [1, 3.5, 2, 3.5], f"rangen met gelijke waarden: {B.rangen([10, 30, 20, 30])}")
check(near(B.correlatie([1, 2, 3, 4], [2, 4, 6, 8]), 1.0) and near(B.correlatie([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)
      and B.correlatie([1, 1, 1], [1, 2, 3]) is None, "correlatie")
ic, spread = B.meet(list(range(10)), [x * 2.0 for x in range(10)])
check(near(ic, 1.0) and near(spread, 17.0 - 1.0), f"meet: rangcorrelatie 1 en beste vijfde min slechtste vijfde ({ic}, {spread})")
check(B.neutraal({0: 10.0, 1: 20.0, 2: 5.0}, lambda i: "a" if i < 2 else "b") == {0: -5.0, 1: 5.0, 2: 0.0}, "rendement t.o.v. de sector")
rows = [{"x": v, "_sec": "tech"} for v in range(20)] + [{"x": 100, "_sec": "klein"}, {"_sec": "tech"}]
pc = B.percentielen(rows, "x", True)
check(near(pc[0], 2.5) and near(pc[19], 97.5) and 21 not in pc, f"percentiel binnen de sector ({pc[0]}, {pc[19]})")
check(pc[20] > 95, "te kleine sector: vergelijken met de hele markt")
check(near(B.percentielen(rows, "x", False)[0], 97.5), "lager = beter draait het percentiel om")
mu, t, n = B.gemiddelde([0.1, 0.1, 0.1, 0.1])
check(near(mu, 0.1) and t is None and n == 4, "gemiddelde zonder spreiding: geen t-waarde")
mu, t0, _ = B.gemiddelde([0.2, -0.1, 0.3, 0.0, 0.25, 0.05] * 4)
_, t5, _ = B.gemiddelde(sorted([0.2, -0.1, 0.3, 0.0, 0.25, 0.05] * 4), 5)
check(t0 > 0 and t5 < B.gemiddelde(sorted([0.2, -0.1, 0.3, 0.0, 0.25, 0.05] * 4))[1], "overlappende vensters (samenhang in de reeks) maken de t-waarde kleiner")


# 2. Kenmerken van één bedrijf: alleen wat toen bekend was
def bedrijf(sym="AAA", region="VS", sector="Technology", fcur=None, c=None, **over):
    m0 = B.mno("2022-01-31")
    ts = {"annualTotalRevenue": [["2022-12-31", 1000.0], ["2023-12-31", 1200.0]], "annualGrossProfit": [["2022-12-31", 500.0], ["2023-12-31", 660.0]],
          "annualTotalAssets": [["2022-12-31", 2000.0], ["2023-12-31", 2200.0]], "annualOperatingIncome": [["2022-12-31", 200.0], ["2023-12-31", 300.0]],
          "annualCurrentLiabilities": [["2022-12-31", 400.0], ["2023-12-31", 400.0]], "annualFreeCashFlow": [["2022-12-31", 150.0], ["2023-12-31", 240.0]],
          "annualOperatingCashFlow": [["2022-12-31", 220.0], ["2023-12-31", 330.0]], "annualNetIncome": [["2022-12-31", 100.0], ["2023-12-31", 150.0]],
          "annualDilutedAverageShares": [["2022-12-31", 110.0], ["2023-12-31", 100.0]], "annualEBITDA": [["2022-12-31", 250.0], ["2023-12-31", 350.0]],
          "annualTotalDebt": [["2022-12-31", 300.0], ["2023-12-31", 300.0]], "annualCashAndCashEquivalents": [["2022-12-31", 100.0], ["2023-12-31", 100.0]]}
    cache = {"region": region, "sector": sector, "fcur": fcur, "price": 50.0, "mcap": 5000.0, "ts": ts,
             "pm": {"m": m0, "c": c or [20.0 + i for i in range(40)]}}
    cache.update(over)
    return B.Aandeel(sym, cache), m0


s, m0 = bedrijf()
dec22, dec23 = B.mno("2022-12-31"), B.mno("2023-12-31")
check("gp_assets" not in B.kenmerken(s, dec22 + B.LAG - 1, {}), "jaarcijfers mogen pas 4 maanden na het boekjaar meetellen")
k = B.kenmerken(s, dec22 + B.LAG, {})
check(near(k["gp_assets"], 25.0) and near(k["fcf_margin"], 15.0) and near(k["cash_conv"], 2.2) and "rev_yoy" not in k and "dilution" not in k,
      f"eerste boekjaar bekend: niveaus wel, groei nog niet ({k})")
check(near(k["roic"], 200 * 0.79 / 1600 * 100), "rendement op kapitaal = bedrijfsresultaat na belasting / (bezit - kortlopende schuld)")
k2 = B.kenmerken(s, dec23 + B.LAG, {})
check(near(k2["rev_yoy"], 20.0) and near(k2["ni_g"], 50.0) and near(k2["dilution"], (100 / 110 - 1) * 100), f"tweede boekjaar bekend: groei en verwatering ({k2})")
px = s.px(dec23 + B.LAG)
check(near(k2["_mc"], px * 100.0) and near(k2["fcf_yield"], 240 / (px * 100) * 100) and near(k2["pe"], px * 100 / 150)
      and near(k2["ev_ebitda"], (px * 100 + 200) / 350), f"beurswaarde van toen = koers van toen x aantal aandelen ({k2['_mc']})")
check(near(k["_mc"], s.px(dec22 + B.LAG) * 110.0), "aantal aandelen van toen: geschaald met de verwatering sindsdien")
m = dec23 + B.LAG
check(near(k2["chg12"], (s.px(m) / s.px(m - 12) - 1) * 100) and near(k2["near_high"], 100.0) and k2["above"] == 100.0, "momentum uit de maandkoersen")
kort, _ = bedrijf(c=[20.0 + i for i in range(40)][:m - m0 + 1])
check(B.kenmerken(kort, m, {}) == k2, "geen blik vooruit: latere koersen veranderen de kenmerken van toen niet")
check(B.kenmerken(s, m0 - 1, {}) is None, "zonder koers geen kenmerken")
check(near(B.kenmerken(s, m0 + 2, {})["_mc"], s.px(m0 + 2) * 100.0), "beurswaarde van toen is er ook vóór de eerste jaarcijfers (voor de grens van het universum)")
eur, _ = bedrijf(fcur="EUR")                           # dollarnotering, cijfers in euro: omrekenen met de koers van toen
ke = B.kenmerken(eur, m, {m: 1.25})
check(near(ke["fcf_yield"], 240 * 1.25 / (px * 100) * 100) and near(ke["pe"], px * 100 / (150 * 1.25)), "cijfers in euro bij een dollarkoers omrekenen")
check("fcf_yield" not in B.kenmerken(eur, m, {}) and "gp_assets" in B.kenmerken(eur, m, {}), "zonder wisselkoers geen waardering, wel de verhoudingen")
verlies, _ = bedrijf(ts=dict(s_ts := {"annualTotalRevenue": [["2022-12-31", 1000.0]], "annualNetIncome": [["2022-12-31", -50.0]]}))
check(B.kenmerken(verlies, dec22 + B.LAG, {})["pe"] == 9999.0, "verlies = slechtste waardering")


# 3. Terugrekenen op een nagebouwde markt: kwaliteit helpt altijd, groei werkt altijd tegen, de rest is toeval
def markt(seed=7, n=420, months=59):
    rnd = random.Random(seed)
    m0 = B.mno("2021-12-31")
    mkt = [0.0] + [(0.03 if (i // 3) % 2 == 0 else -0.025) + rnd.gauss(0, 0.01) for i in range(1, months)]    # op en neer
    sp, lvl = [], 4000.0
    for r in mkt:
        lvl *= 1 + r
        sp.append(round(lvl, 2))
    cache = {}
    for i in range(n):
        q, g = rnd.gauss(0, 1), rnd.gauss(0, 1)        # kwaliteit en groei van dit bedrijf
        region, sector = ("VS" if i % 4 else "EU"), ("Technology", "Healthcare", "Industrials")[i % 3]
        px, c = 40.0 + rnd.random() * 60, []
        for t in range(months):
            px *= 1 + mkt[t] + 0.012 * q - 0.012 * g + rnd.gauss(0, 0.05)
            c.append(round(max(px, 0.5), 4))
        ts = {k: [] for k in ("annualTotalRevenue", "annualGrossProfit", "annualTotalAssets", "annualOperatingIncome", "annualCurrentLiabilities",
                              "annualFreeCashFlow", "annualOperatingCashFlow", "annualNetIncome", "annualDilutedAverageShares", "annualEBITDA",
                              "annualTotalDebt", "annualCashAndCashEquivalents")}
        rev, ni = 1000.0, 100.0
        for y in (2022, 2023, 2024, 2025):
            d = f"{y}-12-31"
            rev *= 1 + 0.08 + 0.06 * g
            ni *= 1 + 0.08 + 0.10 * g
            ta = 2000.0
            ts["annualTotalRevenue"].append([d, rev]); ts["annualGrossProfit"].append([d, ta * (0.30 + 0.08 * q)])
            ts["annualTotalAssets"].append([d, ta]); ts["annualOperatingIncome"].append([d, ta * (0.12 + 0.04 * q)])
            ts["annualCurrentLiabilities"].append([d, 400.0]); ts["annualFreeCashFlow"].append([d, rev * (0.12 + 0.04 * q)])
            ts["annualOperatingCashFlow"].append([d, max(ni, 1.0) * (1.2 + 0.2 * q)]); ts["annualNetIncome"].append([d, max(ni, 1.0)])
            ts["annualDilutedAverageShares"].append([d, 100.0]); ts["annualEBITDA"].append([d, rev * (0.2 + rnd.random() * 0.1)])
            ts["annualTotalDebt"].append([d, 300.0]); ts["annualCashAndCashEquivalents"].append([d, 100.0])
        cache[f"S{i:03d}"] = {"region": region, "sector": sector, "price": c[-1], "mcap": c[-1] * 1e8, "ts": ts, "pm": {"m": m0, "c": c}}
    cache["KLEIN"] = dict(cache["S000"], mcap=cache["S000"]["price"] * 1e5)          # te klein voor het universum
    cache["OVERGESLAGEN"] = dict(cache["S001"], skip="valuta GBp")
    ref = {"sp": {"m": m0, "c": sp}, "fx": {"m": m0, "c": [1.10 + 0.01 * math.sin(t / 5) for t in range(months)]}}
    return cache, ref


cache, ref = markt()
R = B.terugrekenen(cache, ref)
check(R["n"] == 421 and R["last"] == B.mno("2021-12-31") + 58, f"bedrijven en laatste maand ({R['n']}, {R['last']})")
check(all(v[2] <= 420 for d in R["res"].values() for v in d.values()), "een bedrijf dat toen te klein was mag niet meetellen")
sam = B.samenvatting(R)
h6 = {p: sam[p].get(6) or {} for p in "QGVM"}
check(h6["Q"]["t"] >= 4 and h6["Q"]["spread"] > 2 and h6["Q"]["pos"] >= 80, f"kwaliteit helpt in de nagebouwde markt en moet zo gemeten worden ({h6['Q']})")
check(h6["G"]["t"] <= -4 and h6["G"]["spread"] < -2, f"groei werkt tegen en moet zo gemeten worden ({h6['G']})")
check(sam["Q"]["op"]["n"] >= B.MIN_SIDE and sam["Q"]["neer"]["n"] >= B.MIN_SIDE and sam["Q"]["op"]["ic"] > 0 and sam["Q"]["neer"]["ic"] > 0,
      f"kwaliteit helpt in stijgende en in dalende maanden ({sam['Q']['op']}, {sam['Q']['neer']})")
check(h6["Q"]["van"] == "2023-04" and sam["G"][6]["van"] == "2024-04", f"jaarcijfers tellen pas na 4 maanden; groei pas met twee boekjaren ({h6['Q']['van']}, {sam['G'][6]['van']})")
base = dict(S.PILLAR_W)
W, st = B.gewichten(base, sam)
check(st["Q"] == 1 and st["G"] == -1 and st["F"] == 0, f"steun: kwaliteit +1, groei -1, scorecard 0 ({st})")
check(near(sum(W.values()), 1.0, 2e-4) and W["Q"] > base["Q"] * 1.1 and W["G"] < base["G"] * 0.85, f"gewichten volgen de steun en blijven samen 100% ({W})")
tot = sum(base[p] * (1.25 if st[p] > 0 else 0.75 if st[p] < 0 else 1) for p in base)
check(all(near(W[p], base[p] * (1.25 if st[p] > 0 else 0.75 if st[p] < 0 else 1) / tot, 1e-4) for p in base), "gewicht = vast gewicht x 1,25 of 0,75, daarna samen 100%")
check(B.gewichten(base, {}) == ({p: round(v, 4) for p, v in base.items()}, {p: 0 for p in base}), "zonder bewijs: de vaste gewichten")
check("zwaarder" in B.oordeel("Q", sam["Q"], 1) and "lichter" in B.oordeel("G", sam["G"], -1) and "Niet terug te rekenen" in B.oordeel("F", {}, 0), "oordeel in woorden")

# 4. Steun: duidelijk bewijs, in beide richtingen van de beurs, en niet heen en weer
E = lambda t, op, neer, n=40, tg=None, k=10: {6: {"n": n, "t": t, "t_groot": tg}, "op": {"n": k, "ic": op}, "neer": {"n": k, "ic": neer}}  # noqa: E731
check(B.steun(E(2.5, 0.03, 0.02)) == 1 and B.steun(E(-2.5, -0.03, -0.02)) == -1, "duidelijk bewijs in beide richtingen")
check(B.steun(E(2.5, 0.03, -0.02)) == 0 and B.steun(E(2.5, -0.03, 0.02)) == 0, "werkt alleen in stijgende of alleen in dalende maanden: geen steun")
check(B.steun(E(1.9, 0.03, 0.02)) == 0 and B.steun(E(1.9, 0.03, 0.02), was=1) == 1 and B.steun(E(1.4, 0.03, 0.02), was=1) == 0, "eenmaal toegekend vervalt steun pas onder t = 1,5")
check(B.steun(E(2.5, 0.005, 0.02)) == 0 and B.steun(E(2.5, 0.005, 0.02), was=1) == 1 and B.steun(E(2.5, -0.02, 0.02), was=1) == 0, "marge op de richting: geen heen-en-weer rond nul")
check(B.steun(E(3.0, 0.03, 0.02, n=B.MIN_MONTHS - 1)) == 0 and B.steun(E(3.0, 0.03, 0.02, k=B.MIN_SIDE - 1)) == 0, "te weinig maanden: geen steun")
check(B.steun(E(3.0, 0.03, 0.02, tg=0.5)) == 0 and B.steun(E(3.0, 0.03, 0.02, tg=2.2)) == 1, "het bewijs moet ook gelden bij de bedrijven die toen ruim boven de grens zaten")

# 5. Live: maandstand vastleggen, volgen en vastzetten
items = [{"sym": f"T{i:03d}", "region": "VS" if i % 2 else "EU", "sector": "Technology" if i % 3 else "Healthcare", "native": 100.0,
          "pillars": {"F": 50 + i % 40, "Q": i % 100, "G": (i * 7) % 100, "V": (i * 13) % 100, "M": 99 - i % 100},
          "card": {"s": 60 + i % 60}, "val": {"lvl": ["gunstig", "normaal", "duur", "zeer duur"][i % 4]},
          "koop": {"status": "zone"} if i % 10 == 0 else None, "gap": {"ok": i % 7 == 0} if i % 5 == 0 else None,
          "s12": {"survivor": i % 9 == 0}} for i in range(300)] + [{"sym": "ZONDER", "region": "VS", "sector": "Technology"}]
coh = B.stand(items, "2026-11", "2026-11-01", 1.10)
check(len(coh["rows"]) == 300 and coh["cols"] == B.COLS and coh["rows"][0][:4] == ["T000", 1, 0, 100.0], f"maandstand: één rij per bedrijf met een koers ({coh['rows'][0]})")
check(coh["rows"][0][B.COLS.index("koop")] == 0 and coh["rows"][3][B.COLS.index("val")] == 3 and coh["rows"][0][B.COLS.index("surv")] == 1, "maandstand: koopmoment, waardering en survivor")
json.dumps(coh, allow_nan=False)
now = lambda sym: 100.0 * (1 + (int(sym[1:]) % 100) / 500.0)        # noqa: E731  hoe hoger de kwaliteit, hoe meer gestegen
v = B.volg(coh, now, 1.10)
check(v["n"] == 300 and v["pijlers"]["Q"][0] > 0.9 and v["pijlers"]["M"][0] < -0.9 and v["pijlers"]["Q"][1] > 0, f"volgen: kwaliteit liep voor, momentum achter ({v['pijlers']})")
check(v["groepen"]["card100"][0] == sum(1 for it in items[:300] if it["card"]["s"] >= 100) and "koop" in v["groepen"], "volgen: groepen geteld")
dollar = B.volg(coh, lambda sym: 100.0, 1.21)                       # koers gelijk, dollar 10% minder waard: dollaraandelen -9% in euro
check(near(dollar["gem"], (150 * (1.10 / 1.21 - 1) * 100) / 300, 0.01), f"dollarrendement naar euro ({dollar['gem']})")
check(B.volg(coh, lambda sym: None, 1.1) is None, "zonder koersen geen uitslag")
L1 = B.live_bijwerken([coh], None, now, 1.10, date(2026, 11, 5))
check(L1["2026-11"]["days"] == 4 and "nu" not in L1["2026-11"], "de eerste week nog geen uitslag")
L2 = B.live_bijwerken([coh], L1, now, 1.10, date(2026, 12, 2))
check("nu" in L2["2026-11"] and "1" in L2["2026-11"] and "6" not in L2["2026-11"], "na 30 dagen: uitslag van 1 maand vastgezet")
L3 = B.live_bijwerken([coh], L2, lambda sym: 100.0 - int(sym[1:]) % 50, 1.10, date(2027, 5, 10))
check(L3["2026-11"]["1"] == L2["2026-11"]["1"] and "6" in L3["2026-11"] and L3["2026-11"]["6"] != L3["2026-11"]["1"], "vastgezette uitslag blijft staan; 6 maanden komt erbij")
ser = B.live_reeks(L3)
check(set(ser) >= {("Q", 1), ("Q", 6)} and list(ser[("Q", 1)]) == [B.mno("2026-10-31")], f"de stand van begin november hoort bij maandeinde oktober ({list(ser[('Q', 1)])})")
sam2 = B.samenvatting(R, {("Q", 1): {R["last"] - 1: (-0.5, -10.0, 300)}})
check(sam2["Q"][1]["live"] == 1 and sam2["Q"][1]["n"] == sam["Q"][1]["n"], "een live gemeten maand vervangt de teruggerekende maand")

# 6. In de Top 250-run: gewichten voor deze run, hergebruik zolang er niets verandert, één maandstand per maand
raw = json.load(open(os.path.join(ROOT, "screener_cache.json"), encoding="utf-8"))
W0, bw0, log0 = S.evidence({}, None, {}, 1.1, date(2026, 11, 14))
check(W0 == {p: round(v, 4) for p, v in S.PILLAR_W.items()} and bw0["pijlers"][0]["steun"] == 0, "lege cache: de vaste gewichten, geen fout")
W1, bw1, log1 = S.evidence(raw["stocks"], raw.get("ref_pm"), {}, 1.1, date(2026, 11, 14))
check(near(sum(W1.values()), 1.0, 2e-4) and all(0.55 * S.PILLAR_W[p] <= W1[p] <= 1.6 * S.PILLAR_W[p] for p in W1), f"gewichten binnen de grenzen en samen 100% ({W1})")
check([p["k"] for p in bw1["pijlers"]] == ["F", "Q", "G", "V", "M"] and all(p["oordeel"] for p in bw1["pijlers"]), "bewijs.json: vijf pijlers met een oordeel")
try:
    json.dumps(bw1, allow_nan=False)
    json.dumps(log1, allow_nan=False)
except ValueError as e:
    check(False, f"bewijs is geen geldige JSON: {e}")
_tr = B.terugrekenen
B.terugrekenen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("opnieuw teruggerekend terwijl er niets veranderd is"))
try:
    W2, bw2, log2 = S.evidence(raw["stocks"], raw.get("ref_pm"), {}, 1.1, date(2026, 11, 14), log1, bw1)
    check(W2 == W1 and log2["terug"] == log1["terug"], "zelfde cache: uitkomst hergebruiken en dezelfde gewichten")
except AssertionError as e:
    check(False, str(e))
finally:
    B.terugrekenen = _tr
lg, nieuw = S.evidence_stand({}, items[:300], date(2026, 11, 14), 1.1)
lg2, nieuw2 = S.evidence_stand(lg, items[:300], date(2026, 11, 20), 1.1)
lg3, nieuw3 = S.evidence_stand(lg2, items[:300], date(2026, 12, 1), 1.1)
check(nieuw and not nieuw2 and nieuw3 and [c["month"] for c in lg3["standen"]] == ["2026-11", "2026-12"], "één maandstand per maand")
many = {"standen": [{"month": f"20{20 + i // 12}-{i % 12 + 1:02d}", "date": "2020-01-01", "rows": []} for i in range(B.KEEP)]}
check(len(S.evidence_stand(many, items[:300], date(2026, 12, 1), 1.1)[0]["standen"]) == B.KEEP, "hooguit zoveel maandstanden bewaren")
# de bestanden in de repository: geldig en in de vorm die de pagina's lezen
for name in ("bewijs.json", "bewijs_log.json"):
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        d = json.load(open(path, encoding="utf-8"))
        check(isinstance(d, dict) and d.get("v") == B.VERSION, f"{name}: onbekende vorm")
        for c in d.get("standen") or []:
            check(c["cols"] == B.COLS and all(len(r) == len(B.COLS) for r in c["rows"]), f"{name}: maandstand {c.get('month')} heeft niet de afgesproken kolommen")

print(f"BEWIJS: {len(fails)} fout(en)" if fails else
      f"BEWIJS: alles in orde · nagebouwde markt: kwaliteit t {h6['Q']['t']}, groei t {h6['G']['t']} · echte cache: "
      + " ".join(f"{p} {W1[p] * 100:.0f}%" for p in W1))
sys.exit(1 if fails else 0)
