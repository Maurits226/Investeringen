"""Droogloop van de hele Top 250-run (main() in scripts/screener.py) op de opgeslagen cache: geen netwerk, alle uitvoer
naar een tijdelijke map. Vangt fouten in de volgorde en de koppeling van de stappen (verversen, koersen, bewijs en
gewichten, scoren, wegschrijven) die de losse tests niet zien. Yahoo is nagebootst: elk verzoek mislukt, de koersen
komen uit de cache. Draait in de GitHub-check en lokaal: python3 tests/test_screener_run.py"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import screener as S  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FOUT", msg)


class GeenNetwerk:
    """Elk verzoek mislukt: wat de run nodig heeft, komt uit de cache (zie hieronder)."""
    calls = 0

    def get(self, url, **_):
        GeenNetwerk.calls += 1
        raise RuntimeError("geen netwerk in de droogloop")

    post = get


def run(tmp):
    raw = json.load(open(os.path.join(tmp, "screener_cache.json"), encoding="utf-8"))
    cache0 = raw["stocks"]

    def universe(cache, y=None, dyn=None):
        return {s: {"region": c.get("region") or ("EU" if "." in s else "VS"), "src": c.get("src") or "test", "name": c.get("name")}
                for s, c in cache.items() if c.get("src") != "opzoeken"}, dyn

    def quotes(y, syms):
        return {s: {"price": c["price"], "cur": c.get("cur") or ("EUR" if "." in s else "USD"), "mcap": c.get("mcap"), "hi52": c.get("hi52"),
                    "lo52": c.get("lo52"), "sma200": c.get("sma200"), "day": 0.1, "chg52": c["chg52"] * 100 if c.get("chg52") is not None else None}
                for s in syms for c in [cache0.get(s) or {}] if c.get("price")}
    patch = {"Yahoo": GeenNetwerk, "load_universe": universe, "fx_eurusd": lambda y: 1.10, "needs_refresh": lambda c, region: False,
             "fx_rates": lambda y, curs: {c: {"TWD": 32.0, "JPY": 150.0, "GBP": 0.78}.get(c, 1.0) for c in curs}, "batch_quotes": quotes,
             "benchmarks": lambda y: ({k: dict(b, price=100.0, ret12=12.0, ret3m=3.0) for k, b in S.BRANCHES.items()}, 0.05),
             "sp500_5y": lambda y: {"VS": 0.7, "EU": 0.72, "lvl": 6900.0, "pm": raw.get("ref_pm")}, "etf_lookthrough": lambda y, prev: prev or {},
             "SLEEP": 0, "MAX_FAILS": 3,
             **{k: os.path.join(tmp, os.path.basename(getattr(S, k))) for k in ("OUT", "CACHE", "KOOP_OUT", "DET_DIR", "THESE_OUT", "ZOEK_OUT", "LOOK_OUT",
                                                                              "ETF_OUT", "BEWIJS_OUT", "BEWIJS_LOG")}}
    old, sleep = {k: getattr(S, k) for k in patch}, S.time.sleep
    for k, v in patch.items():
        setattr(S, k, v)
    S.time.sleep = lambda *_: None
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            S.main()
    finally:
        for k, v in old.items():
            setattr(S, k, v)
        S.time.sleep = sleep
    return log.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    for name in ("screener_cache.json", "koopmoment.json", "these.json", "etf_holdings.json", "bewijs.json", "bewijs_log.json"):
        if os.path.exists(os.path.join(ROOT, name)):
            shutil.copy(os.path.join(ROOT, name), os.path.join(tmp, name))
    try:
        out1 = run(tmp)
    except SystemExit as e:
        check(False, f"de run stopte met code {e.code}")
        out1 = ""
    except Exception as e:  # noqa: BLE001
        check(False, f"de run liep vast: {e!r}")
        out1 = ""
    load = lambda f: json.load(open(os.path.join(tmp, f), encoding="utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))  # noqa: E731
    if out1:
        for bad in ("niet bijgewerkt", "niet weggeschreven", "mislukt:"):
            check(bad not in out1, f"een onderdeel van de run gaf een fout ('{bad}'):\n" + "\n".join(x for x in out1.split("\n") if bad in x)[:600])
        scr, bw, lg, koop = load("screener.json"), load("bewijs.json"), load("bewijs_log.json"), load("koopmoment.json")
        W, W0 = scr["weights"], scr["weights_base"]
        check(W0 == S.PILLAR_W and abs(sum(W.values()) - 1) < 2e-4 and all(0.55 * W0[p] <= W[p] <= 1.6 * W0[p] for p in W0), f"gewichten van de marktrang kloppen niet: {W}")
        check({p["k"]: p["gewicht"] for p in bw["pijlers"]} == W, "bewijs.json en screener.json noemen niet dezelfde gewichten")
        check(scr["scored"] >= 1500 and len(scr["items"]) >= 500 and all(0 <= it["score"] <= 100 for it in scr["items"]), f"te weinig bedrijven gescoord ({scr.get('scored')})")
        check(lg.get("terug", {}).get("key") and len(lg.get("standen") or []) >= 1, "bewijs_log.json: geen teruggerekende maanden of geen maandstand")
        check(len(koop.get("candidates") or []) >= 0 and "log" in koop, "koopmoment.json niet geschreven")
        check(len(os.listdir(os.path.join(tmp, "screener_det"))) == S.DET_N, "detailbestanden niet geschreven")
        # tweede run: de teruggerekende maanden worden hergebruikt en er komt geen tweede maandstand bij
        stand1, key1 = [c["month"] for c in lg["standen"]], lg["terug"]["key"]
        tr = S.BW.terugrekenen
        S.BW.terugrekenen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("opnieuw teruggerekend"))
        try:
            out2 = run(tmp)
            lg2, scr2 = load("bewijs_log.json"), load("screener.json")
            check("bewijs niet bijgewerkt" not in out2, "tweede run: terugrekenen had hergebruikt moeten worden")
            check([c["month"] for c in lg2["standen"]] == stand1 and lg2["terug"]["key"] == key1 and scr2["weights"] == W, "tweede run: maandstand of gewichten veranderd")
        except Exception as e:  # noqa: BLE001
            check(False, f"tweede run liep vast: {e!r}")
        finally:
            S.BW.terugrekenen = tr

print(f"TOP 250-RUN: {len(fails)} fout(en)" if fails else
      "TOP 250-RUN: hele run zonder netwerk in orde · " + next((x for x in out1.split("\n") if x.startswith("Marktrang")), "")[:120])
sys.exit(1 if fails else 0)
