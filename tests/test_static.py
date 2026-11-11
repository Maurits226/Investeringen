"""Snelle controles zonder browser of netwerk (GitHub-check en lokaal: python3 tests/test_static.py):
  * alle Python-scripts compileren
  * alle JavaScript (losse .js en de scripts in elke .html) is geldige syntaxis (via node --check)
  * alle JSON-databestanden zijn geldige JSON zoals een browser die leest (geen NaN of Infinity)
  * elke pagina heeft het beveiligingsbeleid (CSP) met de vingerafdruk van zijn script (tests/csp.py), de
    anti-inbed-regels en laadt niets van buiten
  * de rekenregels in shared.js (tests/test_rules.js): winstnemingstrap en samenvoegen van je state
"""
import glob
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csp as CSP  # noqa: E402  (vingerafdrukken van de scripts in het beveiligingsbeleid)
fails = []


def fail(msg):
    fails.append(msg)
    print("  FOUT", msg)


def strict(c):
    raise ValueError(f"ongeldige waarde {c}")


# 1. Python
for f in sorted(glob.glob(os.path.join(ROOT, "scripts", "*.py")) + glob.glob(os.path.join(ROOT, "*.py"))):
    try:
        py_compile.compile(f, cfile=os.path.join(tempfile.gettempdir(), "chk.pyc"), doraise=True)
    except py_compile.PyCompileError as e:
        fail(f"{os.path.relpath(f, ROOT)}: {e.msg}")

# 2. JavaScript
with tempfile.TemporaryDirectory() as tmp:
    sources = [(os.path.relpath(f, ROOT), open(f, encoding="utf-8").read()) for f in sorted(glob.glob(os.path.join(ROOT, "*.js")))]
    for f in sorted(glob.glob(os.path.join(ROOT, "*.html"))):
        html = open(f, encoding="utf-8").read()
        for i, m in enumerate(re.finditer(r"<script>([\s\S]*?)</script>", html)):
            sources.append((f"{os.path.relpath(f, ROOT)} (script {i + 1})", m.group(1)))
    for name, code in sources:
        p = os.path.join(tmp, "x.js")
        open(p, "w", encoding="utf-8").write(code)
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        if r.returncode:
            fail(f"{name}: {(r.stderr.strip().splitlines() or ['?'])[-1]}")

# 3. JSON-data (zoals de pagina's die met JSON.parse lezen)
files = sorted(glob.glob(os.path.join(ROOT, "*.json")) + glob.glob(os.path.join(ROOT, "screener_det", "*.json"))
               + [os.path.join(ROOT, "manifest.webmanifest")])
for f in files:
    try:
        json.load(open(f, encoding="utf-8"), parse_constant=strict)
    except Exception as e:  # noqa: BLE001
        fail(f"{os.path.relpath(f, ROOT)}: {e}")

# 4. Beveiliging in de kop van elke pagina, en niets van buiten laden
for f in sorted(glob.glob(os.path.join(ROOT, "*.html"))):
    name, html = os.path.relpath(f, ROOT), open(f, encoding="utf-8").read()
    head = html.split("</head>")[0]
    if 'http-equiv="Content-Security-Policy"' not in head:
        fail(f"{name}: geen Content-Security-Policy")
    elif "font-src 'self'" not in head or "connect-src 'self' https://api.github.com" not in head:
        fail(f"{name}: CSP niet zoals afgesproken (font-src 'self', connect-src 'self' https://api.github.com)")
    if '<style id="af">' not in head or 'src="antiframe.js"' not in head:
        fail(f"{name}: anti-inbedregels ontbreken")
    # het script in de pagina draait alleen met zijn vingerafdruk in het beleid; ingevoegde code (XSS) dus niet
    for msg in CSP.problems(name, html):
        fail(msg)
    for part in ("default-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-src 'self'"):
        if part not in head:
            fail(f"{name}: CSP mist {part}")
    for url in re.findall(r"""<(?:script|img|iframe|link|source|video|audio)\b[^>]*?(?:src|href)=["'](https?://[^"']+)""", html):
        fail(f"{name}: laadt iets van buiten: {url}")
    if re.search(r"@import\s+url\(\s*['\"]?https?://", html):
        fail(f"{name}: @import van buiten")

# 5. Patroonradar: achtergrond (beurs vandaag, nieuws) is niet terug te testen en telt niet mee in de adviesscore
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import signals as SG  # noqa: E402
    P = SG.context_patterns({"sens": [{"impact_pct": 2.5, "label": "Nasdaq 100", "today_pct": 1.6, "beta": 1.4}],
                             "news": [{"sent": 0.8}, {"sent": 0.6}]})
    if not P or any(p["w"] != 0 or not p.get("ctx") for p in P):
        fail(f"signals.py: achtergrondpatronen tellen mee in de score: {P}")
except Exception as e:  # noqa: BLE001
    fail(f"signals.py: achtergrondpatronen niet te testen: {e}")
# Rem op koopadviezen: alleen chipgevoelige aandelen (samenhang 0,5+ met de chipindex), alleen bij techradar 'verhoogd'
# of hoger of de markt in risico-uit
try:
    chip, los = {"sens": [{"key": "chips", "corr": 0.82}]}, {"sens": [{"key": "chips", "corr": 0.31}, {"key": "nasdaq", "corr": 0.9}]}
    cases = [(chip, "verhoogd", "neutral", "techradar verhoogd"), (chip, "hoog", "calm", "techradar hoog"),
             (chip, "rustig", "off", "risico-uit"), (chip, "opletten", "neutral", None), (chip, None, None, None),
             (los, "hoog", "off", None), ({}, "hoog", "off", None)]
    for ctx, lvl, st, want in cases:
        got = SG.chip_rem(ctx, lvl, st)
        if (want is None) != (got is None) or (want and want not in got):
            fail(f"signals.py: rem bij {lvl}/{st} en {ctx}: {got!r}, verwacht {want!r}")
    if SG.SIGNAL_SCORE != 60:
        fail(f"signals.py: drempel voor een advies is {SG.SIGNAL_SCORE}, afgesproken 60")
except Exception as e:  # noqa: BLE001
    fail(f"signals.py: rem op koopadviezen niet te testen: {e}")

# 6b. Marktagenda (Vandaag): geldige datums en soorten, op volgorde, en nog minstens 60 dagen Fed- en inflatiedatums vooruit
try:
    from datetime import date, timedelta
    ag = json.load(open(os.path.join(ROOT, "marktagenda.json"), encoding="utf-8"))
    ev = ag.get("events") or []
    ds = [date.fromisoformat(e["date"]) for e in ev]
    if any(e.get("kind") not in ("fed", "cpi", "verkiezing") or not str(e.get("title") or "").strip() for e in ev):
        fail("marktagenda.json: onbekende soort of lege titel")
    if ds != sorted(ds) or len(set((e["date"], e["kind"]) for e in ev)) != len(ev):
        fail("marktagenda.json: niet op datum gesorteerd of dubbele gebeurtenis")
    horizon = date.today() + timedelta(days=60)
    for k in ("fed", "cpi"):
        if not any(e["kind"] == k and date.fromisoformat(e["date"]) >= horizon for e in ev):
            fail(f"marktagenda.json: geen '{k}'-datum meer na {horizon}: lijst bijwerken (federalreserve.gov, bls.gov)")
except Exception as e:  # noqa: BLE001
    fail(f"marktagenda.json: {e}")

# 6. Rekenregels van het dashboard (winstnemingstrap, samenvoegen van je state) in shared.js
r = subprocess.run(["node", os.path.join(ROOT, "tests", "test_rules.js")], capture_output=True, text=True)
if r.returncode:
    fail("shared.js rekenregels:\n" + (r.stdout + r.stderr).strip())

print(f"STATISCH: {len(fails)} fout(en)" if fails else "STATISCH: alles in orde")
sys.exit(1 if fails else 0)
