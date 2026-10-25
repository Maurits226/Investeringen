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
    if hasattr(S, "find_gap"):
        it["gap"] = S.find_gap(it)
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
        fin = bool(getattr(S, "FIN_BALANCE", None) and S.FIN_BALANCE.search(it.get("industry") or ""))
        check(s12["survivor"] == (not fails_ and not fin) and s12["nfail"] == len(fails_) and s12["fail"] == (fails_[0] if fails_ else None),
              f"{it['sym']}: survivor/fail klopt niet met de stappen")
        if fin:   # bank of verzekeraar: cash vs. schuld en vrije kasstroom tellen niet, DCF n.v.t.
            s3 = next(x for x in s12["list"] if x["n"] == 3)
            check("meer schuld dan cash" not in (s3.get("note") or ""), f"{it['sym']}: bank valt af op schuld > cash")
            check(next(x for x in s12["list"] if x["n"] == 12)["s"] != "fail", f"{it['sym']}: DCF-afvaller bij een bank")
        hard = any(x["s"] == "fail" and x["n"] in S.HARD_STEPS for x in s12["list"])
        check(not (hard and it["koop"]), f"{it['sym']}: koopmoment terwijl een harde stap faalt")
# Find the gap: alleen als de cijfers omhoog gaan en het aandeel omlaag, en de gap groot genoeg is
for it in items:
    g = it.get("gap")
    if not g:
        continue
    check(g["rev"] >= S.GAP_REV_MIN and g["eps"] > 0, f"{it['sym']}: gap zonder groeiende omzet en winst")
    check(g["px"] < 0 or (g["dd"] is not None and g["dd"] <= -S.GAP_DD), f"{it['sym']}: gap terwijl het aandeel niet daalde")
    check(g["pct"] >= S.GAP_MIN and g["ok"] == (not g["warn"]), f"{it['sym']}: gap te klein of ok/warn klopt niet")
# en een vast voorbeeld: omzet +20%, winst +30%, koers -20% → 1 - 0,8/1,2 = 33% goedkoper (de kleinste van de twee)
if hasattr(S, "find_gap"):
    demo = {"price": 80.0, "hi52": 110.0, "s12": None, "_card": {"chg12": -20.0, "ts": {}, "c": {
        "rev_g": 0.20, "eps_g": 0.30, "eps": 2.0, "rev_up": 3, "rev_down": 1, "fwd_growth": 0.1}}}
    g = S.find_gap(demo)
    check(g is not None and abs(g["pct"] - 33.3) < 0.1 and g["ok"], f"gap-voorbeeld: verwacht 33,3% en ok, kreeg {g}")
    # dollarbedrijf met euronotering: koers -20% in euro, maar de euro steeg 10% → in dollars -12%, gap kleiner
    demo_fx = json.loads(json.dumps(demo))
    demo_fx["region"], demo_fx["_card"]["fcur0"] = "EU", "USD"
    g_fx = S.find_gap(demo_fx, 0.10)
    check(g_fx is not None and abs(g_fx["pct"] - (1 - 0.88 / 1.2) * 100) < 0.1, f"gap-voorbeeld valuta: kreeg {g_fx}")
    demo["_card"]["c"]["rev_down"] = 9
    check((S.find_gap(demo) or {}).get("ok") is False, "gap-voorbeeld: verlaagde verwachtingen moeten 'let op' geven")
    demo["_card"]["chg12"], demo["price"] = 5.0, 100.0
    check(S.find_gap(demo) is None, "gap-voorbeeld: aandeel steeg en staat dicht bij de top, dus geen gap")
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

# Find the gap volgen: een nieuwe kans (scorecard 90+ én een gap) komt in het logboek, een kans zonder gap of met 'let op' niet
if hasattr(S, "update_koop"):
    mk = lambda sym, card, gap: {"sym": sym, "name": sym, "region": "VS", "native": 100.0, "card": {"s": card, "lvl": "top"},  # noqa: E731
                                 "gap": gap, "own": False}
    pool = [mk("AAA", 105, {"pct": 30.0, "ok": True, "warn": []}), mk("BBB", 80, {"pct": 30.0, "ok": True, "warn": []}),
            mk("CCC", 110, {"pct": 30.0, "ok": False, "warn": ["x"]}), mk("DDD", 110, None)]
    k1 = S.update_koop(None, [], pool, {"AAA": {"price": 110.0}}, {}, {"lvl": 5000.0}, 1.1)
    check([e["sym"] for e in k1.get("gaplog", [])] == ["AAA"], f"gaplog: verwacht alleen AAA, kreeg {[e['sym'] for e in k1.get('gaplog', [])]}")
    k2 = S.update_koop(k1, [], pool, {"AAA": {"price": 110.0}}, {}, {"lvl": 5000.0}, 1.1)
    check(len(k2["gaplog"]) == 1, "gaplog: dezelfde kans mag niet dubbel worden vastgelegd")
    json.dumps(k2, allow_nan=False)

# Verversen: een overgeslagen bedrijf (rapporteert in yen, pond, ...) pas na een maand opnieuw ophalen, niet elke run
if hasattr(S, "SKIP_REFRESH_DAYS"):
    from datetime import datetime, timedelta, timezone
    ago = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat(timespec="seconds")  # noqa: E731
    check(not S.needs_refresh({"f": ago(2), "skip": "rapporteert in JPY"}, "VS"), "overgeslagen VS-aandeel wordt elke run opnieuw opgehaald")
    check(S.needs_refresh({"f": ago(S.SKIP_REFRESH_DAYS + 1), "skip": "rapporteert in JPY"}, "VS"), "overgeslagen aandeel wordt nooit opnieuw bekeken")
    check(S.needs_refresh({"f": ago(S.FUND_REFRESH_DAYS + 1), "ts": {}, "v": S.CACHE_V}, "EU"), "verouderde cijfers worden niet ververst")

# Opzoeken (tab Opzoeken): lijst lezen (geldig, niet verlopen, nieuwste eerst), de notering die de scan kent,
# het resultaat terugvinden onder een andere notering en de reden als het niet lukt
if hasattr(S, "load_lookups"):
    import tempfile
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    stamp = lambda d: (t0 - timedelta(days=d)).isoformat().replace("+00:00", "Z")  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        lp = os.path.join(tmp, "opzoeklijst.json")
        json.dump({"items": [
            {"sym": "nvda", "alt": ["NVD.DE", "nvd.de", "<x>"], "name": "NVIDIA", "at": stamp(2)},
            {"sym": "AMD.DE", "alt": ["AMD"], "at": stamp(0.1)},
            {"sym": "OUD", "at": stamp(S.LOOK_DAYS + 1)},                 # verlopen
            {"sym": "<script>", "at": stamp(1)},                          # geen ticker
            {"sym": "TOEKOMST", "at": (t0 + timedelta(days=3)).isoformat()},
            {"sym": "NVDA", "at": stamp(5)},                              # dubbel: de eerste telt
            {"sym": "GEENDATUM"},
        ]}, open(lp, "w"))
        look = S.load_lookups(lp, at=t0)
        check([e["sym"] for e in look] == ["AMD.DE", "NVDA"], f"opzoeklijst: verwacht AMD.DE, NVDA, kreeg {[e['sym'] for e in look]}")
        check(look and look[1]["alt"] == ["NVD.DE"], f"opzoeklijst: andere noteringen niet opgeschoond: {look[1]['alt'] if len(look) > 1 else None}")
        check(S.load_lookups(os.path.join(tmp, "bestaat-niet.json")) == [], "opzoeklijst: ontbrekend bestand moet een lege lijst geven")
        uni_t = {"AMD": {}, "NVDA": {}}
        check(S.lookup_pick(look[0], uni_t) == "AMD", "opzoeken: euro-notering moet de dollarnotering uit de scan kiezen")
        check(S.lookup_pick({"sym": "XYZ.AS", "alt": []}, uni_t) == "XYZ.AS", "opzoeken: onbekend aandeel = de gevraagde notering")
        # terugvinden onder een andere notering (euro-notering of weggelaten dubbele notering)
        pool = [it for it in items if it.get("eu") and it["eu"] != it["sym"]][:2]     # VS-bedrijf met een euro-notering
        check(bool(pool), "opzoeken: geen VS-bedrijf met euro-notering in de steekproef")
        if pool:
            idx = S.lookup_index(pool)
            r = S.lookup_result({"sym": pool[0]["eu"], "alt": [], "at": stamp(0)}, idx, {})
            check(r["status"] == "ok" and r["it"] is pool[0], f"opzoeken: {pool[0]['eu']} niet gevonden via de euro-notering")
        cache_t = {"SKP": {"f": stamp(1), "skip": "rapporteert in DKK"}, "ERR": {"err": "HTTP Error 404: Not Found", "err_at": stamp(0)},
                   "LEEG": {"f": stamp(1), "ts": {}}}
        st = {k: S.lookup_result({"sym": k, "alt": [], "at": stamp(0)}, {}, cache_t) for k in ("SKP", "ERR", "LEEG", "NIEUW")}
        check([st[k]["status"] for k in ("SKP", "ERR", "LEEG", "NIEUW")] == ["skip", "fout", "geen", "wacht"],
              f"opzoeken: statussen kloppen niet: {[(k, v['status']) for k, v in st.items()]}")
        check(st["ERR"]["why"] == "Yahoo kent deze ticker niet", f"opzoeken: onbekende ticker niet herkend: {st['ERR']['why']}")
        out = S.write_lookups(look, items, {}, len(items), path=os.path.join(tmp, "opzoeken.json"), outside={look[1]["sym"]})
        back = json.load(open(os.path.join(tmp, "opzoeken.json"), encoding="utf-8"), parse_constant=lambda c: check(False, c))
        check(set(back["items"]) == {"AMD.DE", "NVDA"} and back["days"] == S.LOOK_DAYS, "opzoeken.json: onverwachte inhoud")
        check(all(r["at"] for r in out["items"].values()), "opzoeken.json: resultaat zonder datum van het opzoeken")
    # een opgezocht aandeel buiten het universum wordt tegen de rest afgezet, maar verandert hun percentielen niet
    pool = [json.loads(json.dumps(it)) for it in items if it["m"].get("fcf_margin") is not None][:60]
    if len(pool) >= 20:
        for it in pool:
            it["mp"] = {}
        S.pct_ranks(pool, "fcf_margin", True, lambda it: "x")
        before = [it["mp"].get("fcf_margin") for it in pool]
        ext = json.loads(json.dumps(pool[0]))
        ext["mp"], ext["m"]["fcf_margin"] = {}, 1e6
        for it in pool:
            it["mp"] = {}
        S.pct_ranks(pool, "fcf_margin", True, lambda it: "x", [ext])
        check([it["mp"].get("fcf_margin") for it in pool] == before, "opzoeken: extra aandeel verandert de percentielen van de rest")
        check(ext["mp"].get("fcf_margin") == 100.0, f"opzoeken: extra aandeel met de hoogste marge moet percentiel 100 krijgen, kreeg {ext['mp']}")

surv = sum(1 for it in items if it["s12"] and it["s12"]["survivor"])
print(f"TOP 250: {len(items)} bedrijven gescoord, {surv} survivors, {sum(1 for it in items if it['koop'])} koopmomenten, "
      f"{sum(1 for it in items if (it.get('gap') or {}).get('ok'))} gaps · "
      + (f"{len(fails)} fout(en)" if fails else "alles in orde"))
sys.exit(1 if fails else 0)
