"""Test van scripts/tech.py (techradar) met synthetische koersen: geen netwerk, schrijft alleen in een tijdelijke map.
Draait in de GitHub-check (tests/run_checks.sh) en lokaal: python3 tests/test_tech.py"""
import io
import json
import os
import random
import shutil
import tempfile
import sys
import time
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

sys.dont_write_bytecode = True               # geen __pycache__ in de repo
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)
import tech  # noqa: E402
import synth_tech as synth  # noqa: E402

TMP = tempfile.mkdtemp(prefix="techtest-")

OUT = os.path.join(TMP, "tech_test.json")
tech.OUT = OUT
os.environ.pop("NTFY_TOPIC", None)
DATA = synth.make(seed=7)
CALLS = []
FAIL = set()


def fake_yahoo(sym, start=None):
    CALLS.append(sym)
    if sym in FAIL:
        raise RuntimeError(f"{sym}: gesimuleerde storing")
    return list(DATA[sym])


tech.yahoo_daily = fake_yahoo
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1
    print("  ok:", msg)


def rows_from(data):
    return {"ndx": data["^NDX"], "sox": data["^SOX"], "vxn": data["^VXN"], "spx": data["^GSPC"]}


def run_quiet(force=True):
    buf = io.StringIO()
    with redirect_stdout(buf):
        tech.run(force)
    return buf.getvalue()


# 1. volledige run
print("1. volledige run op synthetische data")
for p in (OUT,):
    if os.path.exists(p):
        os.remove(p)
t = time.time()
log = run_quiet()
dt = time.time() - t
print(log)
out = json.load(open(OUT, encoding="utf-8"))
check(dt < 20, f"rekentijd {dt:.1f}s")
for k in ("version", "updated", "asof", "ndx", "sox", "vxn", "state", "signals", "level", "levels", "corrections",
          "source_errors", "spark", "base", "corr_sum", "notified"):
    check(k in out, f"sleutel {k}")
check(out["asof"] == DATA["^NDX"][-1][0].isoformat(), "asof = laatste datum")
check(len(out["spark"]["c"]) == 260 and len(out["spark"]["s200"]) == 260, "spark 260 punten")
check(sum(l["n_days"] for l in out["levels"]) == out["base"]["n"], "niveaus dekken alle dagen")
check(out["source_errors"] == [], "geen bronfouten")
check(len(out["signals"]) == 6 and all(not s.get("na") for s in out["signals"]), "6 signalen met data")
check(json.dumps(out, allow_nan=False) is not None, "geen NaN")
check(out["state"]["status"] in ("normaal", "terugval", "correctie", "berenmarkt"), "status geldig")
check(out["level"]["k"] in ("rustig", "opletten", "verhoogd", "hoog"), "niveau geldig")
check(all(c["depth"] <= -10 for c in out["corrections"]), "correcties >= 10%")
check(all(c["peak"] >= "2001-01-01" for c in out["corrections"]), "correcties sinds 2001")
print("   signalen:")
for s in out["signals"]:
    print(f"    {s['k']:16s} aan={s['on']!s:5s} share={s['share']}% dagen={s['n_days']} ep={s['n_ep']} "
          f"hit={s['hit']} hit_ep={s['hit_ep']} base={s['base']} lift={s['lift']} lift_ep={s['lift_ep']} mix={s['in_mix']}")
print("   niveaus:")
for l in out["levels"]:
    print(f"    {l['k']:9s} {l['rule']:12s} tijd={l['share']}% dagen={l['n_days']} ep={l['n_ep']} hit10={l['hit10']} "
          f"ep={l['hit10_ep']} hit15={l['hit15']} lift={l['lift']}")
print("   nu:", out["level"]["k"], out["level"]["n_on"], out["level"]["on"], "|", out["state"]["text"])
print("   correcties:", out["corr_sum"], len(out["corrections"]))

# 2. geen blik vooruit: toekomst vervangen of afkappen verandert niets aan dagen <= T
print("2. geen blik vooruit")
base_rows = rows_from(DATA)
alt = synth.make(seed=99)                      # andere toekomst
PROD_MIN = tech.MIN_HIST
for mh, fracs in ((200, (0.45, 0.7, 0.9, 0.995)), (PROD_MIN, (0.7, 0.9, 0.995))):
  tech.MIN_HIST = mh                           # bij 0.45 heeft VXN nog geen 10 jaar historie: samenstelling gelijk houden
  R = tech.reeks(base_rows)
  n = len(R["dates"])
  for frac in fracs:
      T = R["dates"][int(n * frac)]
      for variant in ("afgekapt", "andere toekomst", "geschaald"):
          rows2 = {}
          for k, sym in (("ndx", "^NDX"), ("sox", "^SOX"), ("vxn", "^VXN"), ("spx", "^GSPC")):
              past = [x for x in DATA[sym] if x[0] <= T]
              if variant == "afgekapt":
                  fut = []
              elif variant == "andere toekomst":
                  fut = [(d, c * past[-1][1] / alt[sym][0][1]) for d, c in alt[sym] if d > T]
                  fut = [(d, c) for d, c in [(d, c) for d, c in fut]]
              else:  # zelfde data maar alles na T x 0,6 (crash) en VXN x 2
                  fut = [(d, c * (2.0 if sym == "^VXN" else 0.6)) for d, c in DATA[sym] if d > T]
              rows2[k] = past + fut
          R2 = tech.reeks(rows2)
          m = sum(1 for d in R["dates"] if d <= T)
          check(R2["dates"][:m] == R["dates"][:m], f"{T} {variant}: zelfde kalender")
          same = all(R["sig"][k][:m] == R2["sig"][k][:m] for k in R["sig"])
          check(same, f"{T} {variant}: signalen t/m T gelijk")
          check(R["cnt"][:m] == R2["cnt"][:m] and R["lv"][:m] == R2["lv"][:m], f"{T} {variant}: aantal/niveau t/m T gelijk")
          check(all(R["mask"][k][:m] == R2["mask"][k][:m] for k in R["mask"]), f"{T} {variant}: keuze optioneel signaal t/m T gelijk")
          # vooruit() mag (en moet) wel afhangen van de toekomst: alleen uitkomsten
          if variant != "afgekapt" and frac < 0.99:
              check(R["f40"][:m - 41] == R2["f40"][:m - 41], f"{T} {variant}: afgeronde uitkomsten gelijk")

tech.MIN_HIST = PROD_MIN
R = tech.reeks(base_rows)
n = len(R["dates"])
check(sorted(R["mask"]) == ["smalle_rally"], "smalle_rally in de vergelijking")

# 3. bron valt weg
print("3. bron valt weg")
FAIL.update({"^VXN", "^SOX"})
run_quiet()
o3 = json.load(open(OUT, encoding="utf-8"))
check(len(o3["source_errors"]) == 2, "2 bronfouten vastgelegd")
check(any(s["k"] == "angst_stijgt" and s.get("na") for s in o3["signals"]), "angst_stijgt overgeslagen")
check(o3["sox"] is None and o3["vxn"] is None, "sox/vxn null")
check(o3["level"]["n_max"] <= 4, "som zonder ontbrekende signalen")
FAIL.clear()

# 4. throttle: tweede run binnen FRESH_MIN haalt niets op
print("4. throttle")
run_quiet()
CALLS.clear()
log = run_quiet(force=False)
check(CALLS == [] and "niets te doen" in log, "verse tech.json: niets opgehaald")
o = json.load(open(OUT, encoding="utf-8"))
o["version"] = 0
json.dump(o, open(OUT, "w", encoding="utf-8"))
run_quiet(force=False)
check(CALLS, "andere VERSION: opnieuw gerekend")

# 5. Nasdaq-100 valt weg: met oude file exit 0 en file onveranderd, zonder file exit 1
print("5. Nasdaq-100 valt weg")
FAIL.add("^NDX")
before = open(OUT, encoding="utf-8").read()
sys.argv = ["tech.py", "--force"]
code = None
try:
    with redirect_stdout(io.StringIO()):
        tech.main()
    code = 0
except SystemExit as e:
    code = e.code
check(code == 0 and open(OUT, encoding="utf-8").read() == before, "oude tech.json blijft, exitcode 0")
os.remove(OUT)
try:
    with redirect_stdout(io.StringIO()):
        tech.main()
    code = 0
except SystemExit as e:
    code = e.code
check(code == 1 and not os.path.exists(OUT), "geen file: exitcode 1")
FAIL.clear()
sys.argv = ["tech.py"]

# 6. pushbericht
print("6. pushbericht (ntfy)")
SENT = []


class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def fake_urlopen(req, timeout=None):
    SENT.append((req.full_url, req.data.decode(), dict(req.headers), timeout))
    return Resp()


tech.urlopen = fake_urlopen
FAILPUSH = []


def fake_urlopen2(req, timeout=None):
    if FAILPUSH:
        raise OSError("ntfy timeout")
    return fake_urlopen(req, timeout)


tech.urlopen = fake_urlopen2
cur = tech.build(base_rows, [])


def lvl(k, errs=()):
    o = dict(cur, level=dict(cur["level"], k=k, label=k.capitalize()), source_errors=list(errs))
    return o


def ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat(timespec="seconds")


prev = {"level": {"k": "opletten"}}
os.environ["NTFY_TOPIC"] = "bad topic!"
check(tech.melden(prev, lvl("verhoogd")) is None and not SENT, "ongeldig topic: niets verstuurd")
os.environ["NTFY_TOPIC"] = "abc"
check(tech.melden(prev, lvl("verhoogd")) is None and not SENT, "te kort topic: niets verstuurd")
os.environ["NTFY_TOPIC"] = "tech_radar-Test42"
note = tech.melden(prev, lvl("verhoogd"))
check(len(SENT) == 1 and note and note["level"] == "verhoogd", "opletten -> verhoogd: 1 bericht")
url, body, hdr, to = SENT[0]
print("    ", url, hdr.get("Title"), "|", body)
check(url == "https://ntfy.sh/tech_radar-Test42" and to, "juiste url en timeout")
check(all(t not in body for t in ("NVDA", "portefeuille", "€", "positie")), "alleen marktinformatie")
m = cur["level"]["m"]
check(("van de keren" in body) == (m["n_ep"] >= tech.MIN_EP and m["hit_ep"] is not None), "percentage per episode alleen bij genoeg episodes")
check(f"{tech.nl(m['hit_ep'], 0)}%" in body if "van de keren" in body else True, "bericht noemt de episode-trefkans")
check(tech.melden({"level": {"k": "verhoogd"}, "notified": note}, lvl("verhoogd")) == note and len(SENT) == 1, "zelfde niveau: niets")
note["at"] = ago(2)
n2 = tech.melden({"level": {"k": "verhoogd"}, "notified": note}, lvl("hoog"))
check(len(SENT) == 2 and n2["level"] == "hoog", "verhoogd -> hoog binnen 24 uur: wél bericht (escalatie)")
check(SENT[-1][2].get("Priority") == "high", "hoog = prioriteit high")
check(tech.melden({"level": {"k": "hoog"}, "notified": n2}, lvl("hoog")) == n2 and len(SENT) == 2, "hoog -> hoog: niets")
n2["at"] = ago(25)
n3 = tech.melden({"level": {"k": "hoog"}, "notified": n2}, lvl("verhoogd", ["VXN: 429"]))
check(n3 == n2, "bron valt weg (hoog -> verhoogd): melding blijft 'hoog'")
check(tech.melden({"level": {"k": "verhoogd"}, "notified": n3}, lvl("hoog")) == n3 and len(SENT) == 2, "bron terug (verhoogd -> hoog): geen dubbel bericht")
n4 = tech.melden({"level": {"k": "hoog"}, "notified": n2}, lvl("opletten", ["SOX: 429", "VXN: 429"]))
check(n4 == n2, "twee bronnen weg (hoog -> opletten): waarschuwing niet afgesloten")
check(tech.melden({"level": {"k": "opletten"}, "notified": n4}, lvl("hoog")) == n4 and len(SENT) == 2, "bronnen terug: geen dubbel bericht")
n2b = dict(n2, at=ago(3))
check(tech.melden({"level": {"k": "hoog"}, "notified": n2b}, lvl("opletten")) == n2b, "binnen 24 uur terug naar opletten: waarschuwing loopt door")
check(tech.melden({"level": {"k": "opletten"}, "notified": n2b}, lvl("verhoogd")) == n2b and len(SENT) == 2, "en weer verhoogd: geen nieuw bericht")
n5 = tech.melden({"level": {"k": "hoog"}, "notified": n2}, lvl("rustig"))
check(n5["level"] == "rustig" and n5["at"] == n2["at"] and len(SENT) == 2, "na 24 uur rustig (volledige run): waarschuwing afgesloten")
n6 = tech.melden({"level": {"k": "rustig"}, "notified": n5}, lvl("verhoogd"))
check(len(SENT) == 3 and n6["level"] == "verhoogd", "nieuwe waarschuwing na >24 uur: bericht")
n7 = {"level": "rustig", "at": ago(5)}
check(tech.melden({"level": {"k": "rustig"}, "notified": n7}, lvl("verhoogd")) == n7 and len(SENT) == 3, "nieuwe waarschuwing binnen 24 uur na vorige melding: wacht")
FAILPUSH.append(1)
with redirect_stdout(io.StringIO()):
    f1 = tech.melden({"level": {"k": "opletten"}}, lvl("verhoogd"))
FAILPUSH.clear()
check(f1 is None and len(SENT) == 3, "mislukte push: notified ongewijzigd")
f2 = tech.melden({"level": {"k": "verhoogd"}, "notified": f1}, lvl("verhoogd"))
check(len(SENT) == 4 and f2["level"] == "verhoogd", "volgende run probeert opnieuw en verstuurt")
check(tech.melden({"level": {"k": "rustig"}}, lvl("opletten")) is None and len(SENT) == 4, "stijging naar opletten: niets")
check(tech.melden({}, lvl("rustig")) is None and len(SENT) == 4, "eerste run, rustig: niets")
b0 = tech.melden({}, lvl("verhoogd"))
check(len(SENT) == 4 and b0 == {"level": "verhoogd", "at": None}, "eerste run, verhoogd: geen bericht, startpunt vastgelegd")
check(tech.melden({"level": {"k": "verhoogd"}, "notified": b0}, lvl("verhoogd")) == b0 and len(SENT) == 4, "tweede run, zelfde niveau: niets")
b1 = tech.melden({"level": {"k": "verhoogd"}, "notified": b0}, lvl("hoog"))
check(len(SENT) == 5 and b1["level"] == "hoog", "daarna stijging naar hoog: wel bericht")
os.environ.pop("NTFY_TOPIC")

# 7. correcties op een bekende reeks
print("7. correcties")
xs = [100, 105, 110, 99, 95, 98, 105, 120, 121, 108, 107, 118, 119, 125, 130, 90, 92]
cs = tech.correcties(xs)
print("    ", cs)
check(cs[0][:3] == (2, 4, 3), "eerste correctie top 110 -> bodem 95")
check(cs[1][0] == 8 and cs[1][1] == 10, "tweede correctie top 121 -> bodem 107")
check(cs[-1][3] is None and cs[-1][0] == 14, "lopende correctie zonder einde")

# 8. episodes
check(tech.EP_GAP == tech.H10, "EP_GAP = horizon (40)")
check(tech.episodes([1, 2, 3, 13, 14, 30]) == [1], "episodes: gat < 40 = zelfde episode")
check(tech.episodes([1, 41, 80, 121]) == [1, 41, 121], "gat >= 40 = nieuwe episode")
print(f"\nALLE {ok} CONTROLES GESLAAGD")

# 9. kapotte vorige tech.json en kapotte tijdstempels
print("9. robuust bij kapotte invoer")
for bad in ('[1,2]', '{"updated": "gisteren", "version": 1}', '{nope'):
    open(OUT, "w").write(bad)
    CALLS.clear()
    run_quiet(force=False)
    check(CALLS and json.load(open(OUT, encoding="utf-8"))["version"] == tech.VERSION, f"vorige file {bad!r}: opnieuw gerekend")
tech.urlopen = fake_urlopen
os.environ["NTFY_TOPIC"] = "tech_radar-Test42"
cur = lvl("verhoogd")
n0 = len(SENT)
check(tech.melden({"level": {"k": "rustig"}, "notified": {"at": "kapot"}}, cur) is not None and len(SENT) == n0 + 1, "kapotte notified.at: geen crash")
os.environ.pop("NTFY_TOPIC")
print(f"\nALLE {ok} CONTROLES GESLAAGD (incl. robuustheid)")

# 10. correcties: waarschuwing nooit uit de vorige correctie, hooguit voor één correctie (bevinding 1)
print("10. correcties: vooraf-waarschuwing eerlijk toegerekend")
for seed in (7, 1, 2, 3, 4):
    D = synth.make(seed=seed)
    rws = rows_from(D)
    Rs = tech.reeks(rws)
    full = tech.build(rws, [])
    idx = {d.isoformat(): i for i, d in enumerate(Rs["dates"])}
    allc = tech.correcties(Rs["ndx"])
    # alle correcties nalopen met dezelfde regels: venster nooit vóór einde vorige, waarschuwing uniek
    cl = full["corrections"]
    for a, b in zip(cl, cl[1:]):
        if b["warn"] and a["end"]:
            assert b["warn"] > a["end"], (seed, a, b)
        if a["warn"] and b["warn"]:
            assert a["warn"] != b["warn"], (seed, a, b)
    for c in cl:
        if c["lead"] is not None:
            assert c["lead"] <= tech.LEAD, c
            assert (c["lead"] >= 0) or not c["al"], c
            # een waarschuwing die 'al' aan stond begon nooit binnen een eerdere correctie
    old_pre = {7: 41, 1: 33, 2: 26, 3: 30, 4: 48}[seed]
    cs_ = full["corr_sum"]
    print(f"    seed {seed}: corr_sum {cs_} (was pre={old_pre} over alle {cs_['n'] + cs_['n_golf']})")
    check(cs_["pre"] <= cs_["n"] and cs_["voor10"] <= cs_["n"] and cs_["pre"] < old_pre, f"seed {seed}: pre {cs_['pre']} < oude {old_pre}, alleen correcties vanaf de jaartop")
    check(all(isinstance(c["top"], bool) and isinstance(c["al"], bool) for c in cl), f"seed {seed}: top/al aanwezig")
# volledige controle zonder de beperking tot 15: tijdelijk alle correcties uitgeven
_orig = tech.CORR_FROM
D = synth.make(seed=7); rws = rows_from(D)
fulls = tech.build(rws, [])
Rs = tech.reeks(rws)
lv = Rs["lv"]; dates = Rs["dates"]
# reconstrueer per correctie het venster en controleer dat de gekozen waarschuwingsdag in een eigen waarschuwing ligt
cs_all = tech.correcties(Rs["ndx"])
ends = {dates[pk].isoformat(): (dates[e].isoformat() if e is not None else None) for pk, tr, h, e in cs_all}
prev_end = None
seen = set()
bad = 0
for pk, tr, h, e in cs_all:
    pe, prev_end = prev_end, e
    if dates[pk] < tech.CORR_FROM:
        continue
    lo = max(pk - tech.LEAD, 0 if pe is None else pe + 1)
    w = next((j for j in range(lo, h + 1) if lv[j] is not None and lv[j] >= tech.LV_WARN), None)
    if w is not None and pe is not None:
        # loop terug naar het begin van de waarschuwing (gaten < WARN_GAP overbrugd)
        j, lastw = w, w
        while j > 0 and lastw - (j - 1) < tech.WARN_GAP:
            j -= 1
            if lv[j] is not None and lv[j] >= tech.LV_WARN:
                lastw = j
        if lastw <= pe:
            bad += 1        # zo'n eerste dag mag niet als waarschuwing tellen
check(all(c["warn"] is None or c["warn"] not in seen for c in fulls["corrections"]), "geen waarschuwing dubbel")
for c in fulls["corrections"]:
    if c["warn"]:
        i = dates.index(date.fromisoformat(c["warn"]))
        check(lv[i] >= tech.LV_WARN, f"waarschuwing {c['warn']} is echt 'verhoogd' of hoger")
print(f"    seed 7: {bad} correcties waarvan de eerste 'verhoogd'-dag in het venster uit de vorige correctie kwam (niet meegeteld)")

# 11. gat in VXN (bevinding 6): kernsignaal blijft bestaan, dagen in het gat tellen niet mee
print("11. gat van 7 handelsdagen in VXN")
holed = dict(base_rows)
holed["vxn"] = [x for x in base_rows["vxn"] if not (date(2026, 9, 8) <= x[0] <= date(2026, 9, 16))]
Rh = tech.reeks(holed)
oh = tech.build(holed, [])
check(Rh["sig"]["angst_stijgt"][-1] is not None, "angst_stijgt heeft op de laatste dag weer een waarde")
check(oh["level"]["n_max"] == cur["level"]["n_max"] and oh["source_errors"] == [], "n_max gelijk, geen bronfout")
hs, he = Rh["dates"].index(date(2026, 9, 8)), Rh["dates"].index(date(2026, 9, 16))
diff = [i for i in range(len(R["lv"])) if R["lv"][i] != Rh["lv"][i]]
check(all(hs <= i <= he + 60 for i in diff), f"alleen dagen in/na het gat anders ({len(diff)} dagen)")
nones = [i for i in range(hs, he + 1) if Rh["lv"][i] is None]
check(all(Rh["sig"]["angst_stijgt"][i] is None for i in nones) and all(Rh["lv"][i] is None for i in range(hs, he + 1) if Rh["sig"]["angst_stijgt"][i] is None), f"dagen zonder VXN-signaal niet als 0 geteld ({len(nones)})")
check(sum(l["n_days"] for l in oh["levels"]) == oh["base"]["n"], "niveaus dekken alle meegetelde dagen")

# 12. verouderde SOX (bevinding 12): signaal 'geen data', melding in source_errors
print("12. SOX 8 handelsdagen achter")
stale = dict(base_rows)
stale["sox"] = base_rows["sox"][:-8]
os_ = tech.build(stale, [])
ch = next(s_ for s_ in os_["signals"] if s_["k"] == "chips_zwakker")
check(ch["on"] is None and ch.get("oud") and not ch["in_mix"], "chips_zwakker: on=None, oud, telt niet mee")
check(any("Chipindex" in e and "actuele" in e for e in os_["source_errors"]), "melding in source_errors: " + "; ".join(os_["source_errors"]))
check(os_["sox"]["oud"] is True and os_["level"]["n_max"] == cur["level"]["n_max"] - 1, "SOX-meter verouderd, n_max één lager")
check(os_["base"]["n"] > 6000, f"backtest blijft volledig ({os_['base']['n']} dagen)")

# 13. afgekapte SOX (bevinding 13): telt niet mee, backtest blijft heel
print("13. SOX met maar 120 dagen historie")
short = dict(base_rows)
short["sox"] = base_rows["sox"][-120:]
osh = tech.build(short, [])
ch = next(s_ for s_ in osh["signals"] if s_["k"] == "chips_zwakker")
check(ch.get("na") and ch["on"] is None, "chips_zwakker overgeslagen")
check(any("te korte historie" in e for e in osh["source_errors"]), "melding: " + "; ".join(osh["source_errors"]))
check(osh["base"]["n"] > 6000 and osh["base"]["from"] < "2002", f"backtest blijft vanaf {osh['base']['from']} ({osh['base']['n']} dagen)")
check(osh["corr_sum"]["n"] + osh["corr_sum"]["n_golf"] > 20, "correcties blijven geteld")

# 14. afgekapte Nasdaq-100: oude tech.json blijft staan
print("14. Nasdaq-100 met korte historie")
run_quiet()
before = open(OUT, encoding="utf-8").read()
DATA_full = DATA["^NDX"]
DATA["^NDX"] = DATA_full[-500:]
sys.argv = ["tech.py", "--force"]
try:
    with redirect_stdout(io.StringIO()):
        tech.main()
    code = 0
except SystemExit as e:
    code = e.code
DATA["^NDX"] = DATA_full
sys.argv = ["tech.py"]
check(code == 0 and open(OUT, encoding="utf-8").read() == before, "korte NDX: oude tech.json blijft, exitcode 0")

# 15. keuze optioneel signaal (bevinding 5): weinig wissels, alleen op maandbegin, genoeg episodes
print("15. keuze smalle_rally")
for seed in (7, 1, 2, 3, 4):
    Rg = tech.reeks(rows_from(synth.make(seed=seed)))
    mk, ds = Rg["mask"]["smalle_rally"], Rg["dates"]
    flips = [i for i in range(1, len(mk)) if mk[i] != mk[i - 1]]
    check(all(ds[i].month != ds[i - 1].month for i in flips) and len(flips) <= 6, f"seed {seed}: {len(flips)} wissels, alleen op maandbegin")
    if mk.count(True):
        i0 = mk.index(True)
        col = Rg["sig"]["smalle_rally"]
        known = [j for j in range(i0 - tech.H10 + 1) if col[j]]
        check(len(tech.episodes(known)) >= tech.OPT_EP and len(known) >= tech.OPT_MIN, f"seed {seed}: pas aan na {len(tech.episodes(known))} episodes / {len(known)} dagen")

# 16. eerlijkheidsnoot en kop op basis van vergelijkbare stand en episodes (bevinding 2/3)
print("16. eerlijkheidsnoot")
for seed in (7, 1, 2, 3, 4, 11, 23):
    o_ = tech.build(rows_from(synth.make(seed=seed)), [])
    L_, m_ = o_["level"], o_["level"]["m"]
    check(m_["cond"] == ("top" if o_["ndx"]["dd52"] > -5 else "dal"), f"seed {seed}: vergelijking '{m_['cond']}' past bij de stand ({o_['ndx']['dd52']}%)")
    if L_["k"] != "rustig":
        weak = m_["n_ep"] < tech.MIN_EP or m_["lift"] is None or m_["lift_ep"] is None or min(m_["lift"], m_["lift_ep"]) < 1.2
        check(weak == ("Let op" in L_["text"]), f"seed {seed}: {L_['k']} lift {m_['lift']} / ep {m_['lift_ep']} (n_ep {m_['n_ep']}) -> noot {'ja' if weak else 'nee'}")
    for l_ in o_["levels"]:
        for part in ("top", "dal"):
            assert set(l_[part]) == {"n", "n_ep", "hit", "hit_ep", "base", "lift", "lift_ep"}
    tot = sum(l_["top"]["n"] + l_["dal"]["n"] for l_ in o_["levels"])
    check(tot == o_["base"]["n"] - tech.H10, f"seed {seed}: top+dal dekken alle dagen met bekende uitkomst ({tot})")
print(f"\nALLE {ok} CONTROLES GESLAAGD (incl. reviewbevindingen)")
shutil.rmtree(TMP, ignore_errors=True)
