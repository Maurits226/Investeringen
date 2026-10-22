"""Test van de Top 250-pijplijn (scripts/screener.py) op de opgeslagen cijfers in screener_cache.json: geen netwerk.
Rekent voor een vaste steekproef van bedrijven (plus je eigen posities) de scorecard, de 12 stappen en de koopmomenten
uit, zoals de echte run, en controleert de uitkomst: geldige waarden, 12 stappen per bedrijf, schrijfbare JSON.
Draait in de GitHub-check en lokaal: python3 tests/test_screener.py
"""
import json
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import screener as S  # noqa: E402

SAMPLE = 400                       # zoveel bedrijven uit de cache (vast patroon, dus elke keer dezelfde)
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FOUT", msg)


cache = json.load(open(os.path.join(ROOT, "screener_cache.json"), encoding="utf-8"))["stocks"]
quality = S.load_quality()
own = S.owned_positions()
ready = sorted(s for s, c in cache.items() if c.get("ts") and not c.get("skip"))
step = max(1, len(ready) // SAMPLE)
pick = set(ready[::step]) | {s for s in ready if s in own["own"] or ((cache[s].get("eu") or {}).get("sym") or "") in own["own"]}

items = []
for sym in sorted(pick):
    c = cache[sym]
    try:
        it = S.build_item(sym, c, {}, 1.16, quality.get(sym) or quality.get((c.get("eu") or {}).get("sym") or ""), own)
    except Exception as e:  # noqa: BLE001
        check(False, f"{sym}: build_item faalt: {e!r}")
        continue
    if it:
        items.append(it)
items = S.dedupe_listings(items)
check(len(items) >= min(100, len(pick) // 2), f"te weinig bedrijven gescoord: {len(items)} van {len(pick)}")

# zelfde stappen als main(): percentielen, pijlers + scorecard, 12 stappen, koopmoment
grp = lambda it: it.get("sector") or "?"  # noqa: E731
for key, (_, higher) in S.METRICS.items():
    S.pct_ranks(items, key, higher, grp)
S.pct_ranks(items, "rpe", True, grp)
sp5 = {"VS": 0.70, "EU": 0.72, "lvl": 6900.0}
mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
for it in items:
    mp = it["mp"]
    by = {pl: [mp[k] for k, (p2, _) in S.METRICS.items() if p2 == pl and k in mp] for pl in ("Q", "G", "V", "M")}
    S.scorecard(it, sp5)
    cs = it["card"].get("s")
    fin_q, qp = mean(by["Q"]), it["kwal"] / 30 * 100
    it["pillars"] = {"F": cs / 1.25 if cs is not None else it["formule"], "Q": 0.5 * fin_q + 0.5 * qp if fin_q is not None else qp,
                     "G": mean(by["G"]), "V": mean(by["V"]), "M": mean(by["M"])}
    it["val"] = S.valuation_check(it)
med = {}
for it in items:
    S.twelve_steps(it, med)
    it.pop("_card", None)
    it["koop"] = S.koopmoment(it)

STATUS = {"pass", "fail", "check", "manual"}
for it in items:
    card, s12 = it.get("card") or {}, it.get("s12")
    check(card.get("s") is None or 0 <= card["s"] <= 125, f"{it['sym']}: scorecard buiten 0-125: {card.get('s')}")
    check(card.get("lvl") in (None, "top", "watch", "low"), f"{it['sym']}: onbekend niveau {card.get('lvl')}")
    check(s12 is not None and [x["n"] for x in s12["list"]] == list(range(1, 13)), f"{it['sym']}: niet precies 12 stappen")
    if s12:
        check(all(x["s"] in STATUS for x in s12["list"]), f"{it['sym']}: onbekende stapstatus")
        fails_ = [x["n"] for x in s12["list"] if x["s"] == "fail"]
        check(s12["survivor"] == (not fails_) and s12["nfail"] == len(fails_) and s12["fail"] == (fails_[0] if fails_ else None),
              f"{it['sym']}: survivor/fail klopt niet met de stappen")
        hard = any(x["s"] == "fail" and x["n"] in S.HARD_STEPS for x in s12["list"])
        check(not (hard and it["koop"]), f"{it['sym']}: koopmoment terwijl een harde stap faalt")
try:
    json.dumps(items, allow_nan=False)
    json.dumps(S.koop_rows(items), allow_nan=False)
except ValueError as e:
    check(False, f"uitvoer bevat NaN of oneindig: {e}")

# Lichte lijst + details (screener.json fmt 2): samengevoegd moet precies het volledige item opleveren
if hasattr(S, "light_item") and hasattr(S, "detail_of"):
    for it in items:
        full = json.loads(json.dumps(it))
        merged = dict(S.light_item(full), **S.detail_of(full))
        check(merged == full, f"{it['sym']}: lichte lijst + details is niet gelijk aan het volledige item")

# Detailbestanden: elk bedrijf in de juiste shard (zelfde formule als shared.js), samen precies alle bedrijven
if hasattr(S, "write_details") and hasattr(S, "shard_of"):
    import tempfile
    from datetime import date, timedelta
    with tempfile.TemporaryDirectory() as tmp:
        full = json.loads(json.dumps(items))
        S.write_details(full, "2026-01-01T00:00:00+00:00", folder=tmp)
        names = sorted(os.listdir(tmp))
        check(names == [f"{n:02d}.json" for n in range(S.DET_N)], f"detailbestanden: verwacht {S.DET_N}, gevonden {names}")
        seen = set()
        for n in range(S.DET_N):
            d = json.load(open(os.path.join(tmp, f"{n:02d}.json"), encoding="utf-8"), parse_constant=lambda c: check(False, c))
            for sym in d.get("items", {}):
                check(sum(ord(ch) for ch in sym) % S.DET_N == n == S.shard_of(sym), f"{sym}: in shard {n}, hoort elders")
                seen.add(sym)
        check(seen == {it["sym"] for it in full}, "detailbestanden bevatten niet precies alle bedrijven")
    # These-bewaking: één foto per week, cijferdatum schuift door naar 'laatst gepubliceerd'
    refs = [it for it in items if it.get("card", {}).get("s") is not None][:5]
    if refs and hasattr(S, "update_these"):
        d0 = date(2026, 10, 19)                                    # maandag
        r0 = json.loads(json.dumps(refs))
        r0[0]["earnings"] = (d0 + timedelta(days=3)).isoformat()
        t1 = S.update_these({}, r0, d0)
        t2 = S.update_these(t1, r0, d0 + timedelta(days=2))         # zelfde week: overschrijven
        t3 = S.update_these(t2, r0, d0 + timedelta(days=7))         # volgende week: erbij, cijfers gepubliceerd
        e = t3["stocks"][r0[0]["sym"]]
        check(len(t2["stocks"][r0[0]["sym"]]["hist"]) == 1, "these: zelfde week moet de foto overschrijven")
        check(len(e["hist"]) == 2, "these: nieuwe week moet een foto toevoegen")
        check(e.get("last_earn") == r0[0]["earnings"], "these: gepasseerde cijferdatum moet naar last_earn")
        json.dumps(t3, allow_nan=False)

# Zoekindex (zoek.json) voor 'positie toevoegen': elke regel [naam, ticker, euro-notering, beurs, sector, marktwaarde]
if hasattr(S, "write_search_index"):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        raw = json.load(open(os.path.join(ROOT, "screener_cache.json"), encoding="utf-8"))
        n = S.write_search_index(cache, raw.get("dyn"), path=os.path.join(tmp, "zoek.json"))
        z = json.load(open(os.path.join(tmp, "zoek.json"), encoding="utf-8"), parse_constant=lambda c: check(False, c))
        rows = z.get("rows") or []
        check(n == len(rows) and n >= 500, f"zoekindex: te weinig namen ({n})")
        check(all(isinstance(r, list) and len(r) == 6 and r[0] and r[1] for r in rows), "zoekindex: regel zonder naam of ticker")
        check(len({r[1] for r in rows}) == len(rows), "zoekindex: dubbele ticker")
        check(any(r[1] == "NVDA" and r[2] for r in rows), "zoekindex: NVIDIA met euro-notering ontbreekt")

surv = sum(1 for it in items if it["s12"] and it["s12"]["survivor"])
print(f"TOP 250: {len(items)} bedrijven gescoord, {surv} survivors, {sum(1 for it in items if it['koop'])} koopmomenten · "
      + (f"{len(fails)} fout(en)" if fails else "alles in orde"))
sys.exit(1 if fails else 0)
