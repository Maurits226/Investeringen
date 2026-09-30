#!/usr/bin/env python3
"""
Top 250 — genereert screener.json voor screener.html.

Zoekt kansen BUITEN je portefeuille: ~500 Amerikaanse bedrijven (S&P 500 + extra's)
en ~250 bedrijven uit de eurozone. Alleen euro- en dollarbedrijven; andere valuta
(pond, frank, kroon, ...) worden overgeslagen.

Per stock:
  * Formuleblad (Investments_formuleblad.xlsx) — 10 criteria van 0/5/10 punten
  * Kwaliteit — moat, CEO, recessiebestendig (whiteboard stap 5-7), 0-10 per stuk.
    Handmatig oordeel uit kwaliteit.json; ontbreekt dat, dan een schatting uit de cijfers.
  * De 7 stappen van het whiteboard als checklist
  * Verwacht rendement komende 12 maanden (analisten-koersdoel + dividend)

Amerikaanse bedrijven: cijfers komen van de dollarnotering, koers en koersdoel worden
getoond op de euro-notering (Xetra, anders Frankfurt/Düsseldorf).

Fundamentele cijfers veranderen per kwartaal. Ze worden per stock bewaard in
screener_cache.json en hooguit eens per FUND_REFRESH_DAYS opnieuw opgehaald.
De eerste keer vullen duurt daarom 1-2 runs; elke run pakt eerst de oudste stocks.

Alleen standaardbibliotheek: geen pip install nodig in GitHub Actions.
"""
import csv
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# ── Instellingen ─────────────────────────────────────────────────────────
TOP_N = 320                 # in screener.json; de pagina toont er 250 na het verbergen van eigen posities
TIME_BUDGET_MIN = float(os.environ.get("SCREENER_BUDGET_MIN", "45"))
FUND_REFRESH_DAYS = 7       # fundamentele cijfers per stock hooguit wekelijks verversen
ERROR_RETRY_H = 20          # mislukte stock pas na zoveel uur opnieuw proberen
LISTING_RETRY_DAYS = 30     # geen euro-notering gevonden: maandelijks opnieuw zoeken
MIN_CRITERIA = 6            # minstens zoveel van de 10 formuleblad-criteria moeten data hebben
PRICE_MATCH = 0.12          # euro-koers en omgerekende dollarkoers mogen max 12% verschillen
W_FORMULE = 0.75            # totaalscore = 75% formuleblad + 25% kwaliteit
MIN_ANALYSTS = 3            # vanaf zoveel analisten telt hun koersdoel
SLEEP = 0.35                # pauze tussen Yahoo-verzoeken
MAX_FAILS = 25              # zoveel fouten achter elkaar = Yahoo blokkeert, stoppen voor deze run

SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "screener.json")
CACHE = os.path.join(ROOT, "screener_cache.json")
UNIVERSE = os.path.join(ROOT, "screener_universe.txt")
QUALITY = os.path.join(ROOT, "kwaliteit.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

EU_SUFFIX = {"DE", "PA", "AS", "MI", "MC", "BR", "HE", "IR", "VI", "LS", "DU", "F"}
EXCH_NAME = {
    "GER": "Xetra", "FRA": "Frankfurt", "DUS": "Düsseldorf", "PAR": "Euronext Parijs",
    "AMS": "Euronext Amsterdam", "MIL": "Borsa Italiana", "MCE": "BME Madrid",
    "BRU": "Euronext Brussel", "HEL": "Nasdaq Helsinki", "ISE": "Euronext Dublin",
    "VIE": "Wiener Börse", "LIS": "Euronext Lissabon", "STU": "Stuttgart", "MUN": "München",
}
EU_LISTING_PREF = ["GER", "FRA", "DUS"]
US_EXCH = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "NYSE American",
           "PCX": "NYSE Arca", "BTS": "Cboe"}
CACHE_V = 2                                 # v2: sector ook uit summaryProfile
# S&P 500-lijst (GICS) -> Yahoo-sectornamen, als Yahoo zelf geen sector geeft
GICS_TO_YAHOO = {
    "Information Technology": "Technology", "Health Care": "Healthcare", "Financials": "Financial Services",
    "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive",
    "Industrials": "Industrials", "Communication Services": "Communication Services", "Energy": "Energy",
    "Utilities": "Utilities", "Real Estate": "Real Estate", "Materials": "Basic Materials",
}     # euro-notering voor VS-bedrijven, in deze volgorde

# Sector -> basispunten recessiebestendigheid (schatting als er geen handmatig oordeel is)
SECTOR_DEFENSIVE = {
    "Consumer Defensive": 4, "Healthcare": 4, "Utilities": 4,
    "Communication Services": 2, "Technology": 2, "Industrials": 2,
    "Financial Services": 2, "Real Estate": 2,
    "Consumer Cyclical": 1, "Energy": 1, "Basic Materials": 1,
}


def compact(o):
    """Getallen inkorten (5 significante cijfers) zodat de cache klein blijft in git."""
    if isinstance(o, float):
        x = float(f"{o:.5g}")
        return int(x) if x.is_integer() and abs(x) < 1e15 else x
    if isinstance(o, dict):
        return {k: compact(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [compact(v) for v in o]
    return o


def now():
    return datetime.now(timezone.utc)


def iso(d=None):
    return (d or now()).isoformat(timespec="seconds")


def age_h(stamp):
    if not stamp:
        return 1e9
    try:
        return (now() - datetime.fromisoformat(stamp)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 1e9


def rnd(x, n=2):
    return None if x is None else round(x, n)


# ── Yahoo ────────────────────────────────────────────────────────────────
class Yahoo:
    """Yahoo Finance met cookie + 'crumb' (nodig voor quoteSummary, quote en timeseries)."""

    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = [("User-Agent", UA), ("Accept", "application/json,text/plain,*/*")]
        self.crumb = None

    def _get_crumb(self):
        if self.crumb:
            return self.crumb
        try:
            self.op.open("https://fc.yahoo.com", timeout=15)
        except Exception:  # noqa: BLE001
            pass                                   # geeft een foutcode, maar zet wel de cookie
        for host in ("query2", "query1"):
            try:
                c = self.op.open(f"https://{host}.finance.yahoo.com/v1/test/getcrumb",
                                 timeout=15).read().decode().strip()
                if c and "<" not in c and len(c) < 40:
                    self.crumb = c
                    return c
            except Exception:  # noqa: BLE001
                pass
        return ""

    def get(self, url, crumb=False, tries=3):
        last = None
        for i in range(tries):
            full = url
            if crumb:
                full += ("&" if "?" in url else "?") + "crumb=" + quote(self._get_crumb())
            try:
                with self.op.open(full, timeout=25) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403) and crumb:
                    self.crumb = None              # crumb verlopen: nieuwe halen
                elif e.code == 404:
                    raise
                elif e.code == 429:
                    time.sleep(20 * (i + 1))       # te veel verzoeken: even rustig aan
                else:
                    time.sleep(2 * (i + 1))
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 * (i + 1))
        raise RuntimeError(str(last))


def chart(y, sym, rng="5d", interval="1d"):
    j = y.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{quote(sym)}"
              f"?range={rng}&interval={interval}&includePrePost=false")
    res = j["chart"]["result"][0]
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    pts = [(t, c) for t, c in zip(res.get("timestamp") or [], closes) if c]
    return pts, res.get("meta") or {}


def fx_eurusd(y):
    """Dollars per euro."""
    pts, meta = chart(y, "EURUSD=X")
    return meta.get("regularMarketPrice") or pts[-1][1]


TS_ANNUAL = ["TotalRevenue", "OperatingRevenue", "OperatingIncome", "OperatingExpense",
             "CashAndCashEquivalents", "OtherShortTermInvestments",
             "CashCashEquivalentsAndShortTermInvestments",
             "CurrentAssets", "CurrentLiabilities", "TotalDebt"]
TS_QUARTER = ["CashAndCashEquivalents", "CashCashEquivalentsAndShortTermInvestments", "TotalDebt"]


def timeseries(y, sym):
    """Jaar- en kwartaalcijfers (resultatenrekening en balans)."""
    types = ",".join(["annual" + k for k in TS_ANNUAL] + ["quarterly" + k for k in TS_QUARTER])
    p1 = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    j = y.get(f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
              f"{quote(sym)}?symbol={quote(sym)}&type={types}&period1={p1}&period2={p2}", crumb=True)
    out = {}
    for res in (j.get("timeseries") or {}).get("result") or []:
        t = (res.get("meta") or {}).get("type") or []
        if not t:
            continue
        rows = []
        for e in res.get(t[0]) or []:
            if not e:
                continue
            v = (e.get("reportedValue") or {}).get("raw")
            d = e.get("asOfDate")
            if v is not None and d:
                rows.append([d, v])
        rows.sort()
        if rows:
            out[t[0]] = rows[-2:] if t[0].startswith("quarterly") else rows[-5:]
    # dubbele reeksen weglaten
    if out.get("annualTotalRevenue"):
        out.pop("annualOperatingRevenue", None)
    if out.get("annualCashCashEquivalentsAndShortTermInvestments"):
        out.pop("annualCashAndCashEquivalents", None)
        out.pop("annualOtherShortTermInvestments", None)
    return out


def summary(y, sym):
    """Kerngegevens, eigendom, analisten en profiel."""
    mods = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,summaryProfile"
    j = y.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(sym)}"
              f"?modules={mods}", crumb=True)
    r = j["quoteSummary"]["result"][0]

    def raw(mod, k):
        v = (r.get(mod) or {}).get(k)
        return v.get("raw") if isinstance(v, dict) else (v if isinstance(v, (int, float, str)) else None)

    pr = r.get("price") or {}
    ap = dict(r.get("summaryProfile") or {}, **{k: v for k, v in (r.get("assetProfile") or {}).items() if v})
    return {
        "name": pr.get("longName") or pr.get("shortName") or sym,
        "cur": pr.get("currency"),
        "exch": pr.get("exchange"),
        "price": raw("price", "regularMarketPrice"),
        "mcap": raw("price", "marketCap"),
        "pe": raw("summaryDetail", "trailingPE"),
        "fpe": raw("summaryDetail", "forwardPE"),
        "eps": raw("defaultKeyStatistics", "trailingEps"),
        "beta": raw("summaryDetail", "beta"),
        "div": raw("summaryDetail", "dividendYield"),
        "hi52": raw("summaryDetail", "fiftyTwoWeekHigh"),
        "lo52": raw("summaryDetail", "fiftyTwoWeekLow"),
        "short": raw("defaultKeyStatistics", "shortPercentOfFloat"),
        "inst": raw("defaultKeyStatistics", "heldPercentInstitutions"),
        "ins": raw("defaultKeyStatistics", "heldPercentInsiders"),
        "tgt": raw("financialData", "targetMeanPrice"),
        "tgt_px": raw("financialData", "currentPrice"),
        "n_an": raw("financialData", "numberOfAnalystOpinions"),
        "rating": (r.get("financialData") or {}).get("recommendationKey"),
        "gm": raw("financialData", "grossMargins"),
        "om": raw("financialData", "operatingMargins"),
        "roe": raw("financialData", "returnOnEquity"),
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "country": ap.get("country"),
    }


def first_price(y, sym):
    """Eerste koers in de Yahoo-historie (benadering van de IPO-koers, gecorrigeerd voor splits)."""
    pts, meta = chart(y, sym, "max", "3mo")
    if not pts:
        return None
    t, c = pts[0]
    first = meta.get("firstTradeDate") or t
    return {"p": c, "d": datetime.fromtimestamp(first, tz=timezone.utc).date().isoformat()}


def find_eu_listing(y, name, us_price, fx):
    """Zoekt de euro-notering van een VS-bedrijf. Controle: euro-koers ≈ dollarkoers / EURUSD."""
    if not us_price or not fx:
        return None
    q = re.sub(r"[,.]|\b(Inc|Corp|Corporation|Co|Company|plc|Ltd|Holdings?|Group|Class [A-C])\b", " ",
               name or "", flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return None
    res = y.get("https://query2.finance.yahoo.com/v1/finance/search?"
                f"q={quote(q)}&quotesCount=20&newsCount=0&listsCount=0")
    cands = [c for c in res.get("quotes", [])
             if c.get("exchange") in EU_LISTING_PREF and c.get("quoteType") == "EQUITY"]
    cands.sort(key=lambda c: EU_LISTING_PREF.index(c["exchange"]))
    for c in cands[:5]:
        try:
            pts, meta = chart(y, c["symbol"])
        except Exception:  # noqa: BLE001
            continue
        eu = meta.get("regularMarketPrice") or (pts[-1][1] if pts else None)
        if meta.get("currency") != "EUR" or not eu:
            continue
        if abs(eu / (us_price / fx) - 1) <= PRICE_MATCH:
            return {"sym": c["symbol"].upper(), "exch": EXCH_NAME.get(c["exchange"], c["exchange"])}
        time.sleep(SLEEP)
    return None


def batch_quotes(y, syms):
    """Actuele koersen in blokken van 40 (één verzoek per blok)."""
    out, syms = {}, sorted(set(syms))
    for i in range(0, len(syms), 40):
        chunk = syms[i:i + 40]
        try:
            j = y.get("https://query2.finance.yahoo.com/v7/finance/quote?symbols="
                      + ",".join(quote(s) for s in chunk), crumb=True)
        except Exception as e:  # noqa: BLE001
            print(f"  koersblok {i // 40 + 1} mislukt: {e}")
            continue
        for q in (j.get("quoteResponse") or {}).get("result") or []:
            out[q["symbol"].upper()] = {
                "price": q.get("regularMarketPrice"), "cur": q.get("currency"),
                "pe": q.get("trailingPE"), "hi52": q.get("fiftyTwoWeekHigh"),
                "lo52": q.get("fiftyTwoWeekLow"), "mcap": q.get("marketCap"),
                "day": q.get("regularMarketChangePercent"),
            }
        time.sleep(SLEEP)
    return out


# ── Universum en eigen posities ──────────────────────────────────────────
def load_universe(cache):
    uni = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(SP500_URL, headers={"User-Agent": UA}),
                                    timeout=30) as r:
            for row in csv.DictReader(io.StringIO(r.read().decode("utf-8"))):
                s = row["Symbol"].strip().upper().replace(".", "-")
                uni[s] = {"region": "VS", "src": "S&P 500", "name": row.get("Security"),
                          "gics": GICS_TO_YAHOO.get((row.get("GICS Sector") or "").strip())}
        print(f"S&P 500: {len(uni)} bedrijven")
    except Exception as e:  # noqa: BLE001
        print(f"S&P 500-lijst niet opgehaald ({e}); vorige lijst uit de cache gebruikt")
        for s, c in cache.items():
            if c.get("src") == "S&P 500":
                uni[s] = {"region": "VS", "src": "S&P 500", "name": c.get("name")}
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE, encoding="utf-8-sig") as f:
            for line in f:
                s = line.split("#")[0].strip().upper()
                if not s:
                    continue
                if s.startswith("-"):                 # "-XYZ" = uitsluiten
                    uni.pop(s[1:], None)
                    continue
                suf = s.rsplit(".", 1)[1] if "." in s else None
                if suf and suf not in EU_SUFFIX:
                    print(f"  {s}: beurs buiten de eurozone, overgeslagen")
                    continue
                uni.setdefault(s, {"region": "EU" if suf else "VS", "src": "lijst", "name": None})
    return uni


def norm_name(n):
    n = (n or "").lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    stop = {"inc", "corp", "corporation", "co", "company", "plc", "nv", "n", "v", "se", "sa", "ag",
            "holding", "holdings", "group", "class", "a", "b", "c", "ltd", "the", "limited", "spa", "reg"}
    words = [w for w in n.split() if w not in stop]
    return " ".join(words[:2])


def owned_positions():
    """Eigen posities (uitsluiten) en watchlist (markeren) uit portfolio.html, tickers.txt,
    signals.json (Amerikaanse tegenhanger van euro-noteringen) en data.json (namen)."""
    own, watch = set(), set()
    html_path = os.path.join(ROOT, "portfolio.html")
    if os.path.exists(html_path):
        html = open(html_path, encoding="utf-8").read()
        for m in re.finditer(r"\{[^{}]*?ticker:\s*['\"]([^'\"]+)['\"][^{}]*?\}", html):
            (watch if "watch: true" in m.group(0) else own).add(m.group(1).upper())
    txt = os.path.join(ROOT, "tickers.txt")
    if os.path.exists(txt):
        for line in open(txt, encoding="utf-8-sig"):
            s = line.split("#")[0].strip().upper()
            if s and s not in watch:
                own.add(s)
    # Amerikaanse notering van je euro-posities (door de Patroonradar gevonden)
    us_of = {}
    try:
        sig = json.load(open(os.path.join(ROOT, "signals.json"), encoding="utf-8"))
        for it in sig.get("items", []) + sig.get("errors", []):
            if it.get("pattern_symbol"):
                us_of[it["symbol"].upper()] = it["pattern_symbol"].upper()
    except Exception:  # noqa: BLE001
        pass
    names_own, names_watch = set(), set()
    try:
        q = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8")).get("quotes", {})
        for k, v in q.items():
            n = norm_name((v or {}).get("name"))
            if n:
                (names_watch if k.upper() in watch else names_own).add(n)
    except Exception:  # noqa: BLE001
        pass
    for s in list(own):
        if s in us_of:
            own.add(us_of[s])
    for s in list(watch):
        if s in us_of:
            watch.add(us_of[s])
    return {"own": own, "watch": watch, "names_own": names_own, "names_watch": names_watch}


def load_quality():
    try:
        q = json.load(open(QUALITY, encoding="utf-8"))
        return {k.upper(): v for k, v in q.items() if not k.startswith("_")}
    except Exception as e:  # noqa: BLE001
        print(f"kwaliteit.json niet gelezen ({e}); alleen schattingen")
        return {}


# ── Ophalen per stock ────────────────────────────────────────────────────
def needs_refresh(c, region):
    if not c or not c.get("f"):
        return not c or age_h(c.get("err_at")) > ERROR_RETRY_H
    if age_h(c["f"]) > FUND_REFRESH_DAYS * 24:
        return True
    if not c.get("sector") and not c.get("skip") and c.get("v", 1) < CACHE_V:
        return True                               # sector ontbrak: eenmalig opnieuw ophalen
    if region == "VS" and not (c.get("eu") or {}).get("sym") \
            and age_h((c.get("eu") or {}).get("checked")) > LISTING_RETRY_DAYS * 24:
        return True
    return False


def fetch_stock(y, sym, meta, old, fx):
    c = {"src": meta["src"], "region": meta["region"]}
    s = summary(y, sym)
    time.sleep(SLEEP)
    want = "USD" if meta["region"] == "VS" else "EUR"
    if s.get("cur") and s["cur"] != want:
        c.update({"f": iso(), "skip": f"valuta {s['cur']}", "name": s.get("name")})
        return c
    c.update(s)
    c["ts"] = timeseries(y, sym)
    time.sleep(SLEEP)
    c["first"] = (old or {}).get("first")
    if not c["first"]:
        try:
            c["first"] = first_price(y, sym)
        except Exception:  # noqa: BLE001
            c["first"] = None
        time.sleep(SLEEP)
    if meta["region"] == "VS":
        eu = (old or {}).get("eu") or {}
        if not eu.get("sym") and age_h(eu.get("checked")) > LISTING_RETRY_DAYS * 24:
            try:
                hit = find_eu_listing(y, s.get("name") or meta.get("name"), s.get("price"), fx)
            except Exception:  # noqa: BLE001
                hit = None
            eu = dict(hit or {}, checked=iso())
        c["eu"] = eu
    c["f"] = iso()
    c["v"] = CACHE_V
    return c


# ── Formuleblad ──────────────────────────────────────────────────────────
def series(ts, *keys):
    for k in keys:
        if ts.get(k):
            return ts[k]
    return []


def cash_sti(ts, prefix):
    s = series(ts, prefix + "CashCashEquivalentsAndShortTermInvestments")
    if s:
        return s
    cash = dict(map(tuple, series(ts, prefix + "CashAndCashEquivalents")))
    sti = dict(map(tuple, series(ts, prefix + "OtherShortTermInvestments")))
    return [[d, v + (sti.get(d) or 0)] for d, v in sorted(cash.items())]


def growth_list(rows, n=3):
    vals = [v for _, v in rows][-(n + 1):]
    return [(b - a) / abs(a) * 100 for a, b in zip(vals, vals[1:]) if a]


def avg(xs):
    return sum(xs) / len(xs) if xs else None


def band(v, lo, hi, high_is_good=True):
    """0/5/10 punten: boven hi = goed, tussen lo en hi = neutraal, onder lo = slecht."""
    if v is None:
        return None
    if v > hi:
        return 10 if high_is_good else 0
    if v < lo:
        return 0 if high_is_good else 10
    return 5


def formula(c, price):
    ts = c.get("ts") or {}
    rev = series(ts, "annualTotalRevenue", "annualOperatingRevenue")
    rev_by = dict(map(tuple, rev))
    crit = []

    # 1. Omzetgroei, gemiddeld over de laatste 3 jaar
    g = growth_list(rev)
    v1 = avg(g) if len(g) >= 2 else None
    crit.append({"k": "omzetgroei", "v": rnd(v1, 1), "p": band(v1, -2, 2)})

    # 2. Operationele marge (Operating Income / Total Revenue), laatste jaar
    oi = series(ts, "annualOperatingIncome")
    v2 = None
    if oi and rev_by.get(oi[-1][0]):
        v2 = oi[-1][1] / rev_by[oi[-1][0]] * 100
    elif c.get("om") is not None:
        v2 = c["om"] * 100
    crit.append({"k": "marge", "v": rnd(v2, 1), "p": None if v2 is None else (10 if v2 > 10 else 5 if v2 >= 0 else 0)})

    # 3. Cash & kortlopende beleggingen, gemiddelde groei laatste 3 jaar
    g3 = growth_list(cash_sti(ts, "annual"))
    v3 = avg(g3) if len(g3) >= 2 else None
    crit.append({"k": "cashgroei", "v": rnd(v3, 1), "p": band(v3, -2, 2)})

    # 4. Current Assets / Current Liabilities (±0,02 rond 1 telt als "gelijk")
    ca, cl = series(ts, "annualCurrentAssets"), series(ts, "annualCurrentLiabilities")
    v4 = ca[-1][1] / cl[-1][1] if ca and cl and cl[-1][1] else None
    crit.append({"k": "current", "v": rnd(v4, 2), "p": band(v4, 0.98, 1.02)})

    # 5. Cash / Total Debt — verandering t.o.v. vorig kwartaal (±5%)
    qc = dict(map(tuple, series(ts, "quarterlyCashAndCashEquivalents",
                                "quarterlyCashCashEquivalentsAndShortTermInvestments")))
    qd = dict(map(tuple, series(ts, "quarterlyTotalDebt")))
    dates = sorted(set(qc) & set(qd))
    v5, p5, ratio = None, None, None
    if dates:
        d1 = dates[-1]
        if qd[d1] == 0:
            p5, ratio = 10, None                  # geen schuld
        else:
            ratio = qc[d1] / qd[d1] * 100
            if len(dates) >= 2 and qd[dates[-2]]:
                r0 = qc[dates[-2]] / qd[dates[-2]] * 100
                if r0:
                    v5 = (ratio - r0) / abs(r0) * 100
                    p5 = band(v5, -5, 5)
    crit.append({"k": "cashschuld", "v": rnd(v5, 1), "p": p5, "ratio": rnd(ratio, 0)})

    # 6. Short % of float
    v6 = c["short"] * 100 if c.get("short") is not None else None
    crit.append({"k": "short", "v": rnd(v6, 1), "p": band(v6, 5, 10, high_is_good=False)})

    # 7. Institutioneel aandeelhouderschap
    v7 = c["inst"] * 100 if c.get("inst") is not None else None
    crit.append({"k": "instituten", "v": rnd(v7, 0), "p": band(v7, 30, 50)})

    # 8. Schaalbaarheid: groei operationele kosten − omzetgroei, gemiddeld 3 jaar (negatief = goed)
    opex = series(ts, "annualOperatingExpense")
    diffs = []
    for (d0, o0), (d1, o1) in zip(opex[-4:], opex[-3:]):
        r0, r1 = rev_by.get(d0), rev_by.get(d1)
        if o0 and r0 and r1:
            diffs.append((o1 - o0) / abs(o0) * 100 - (r1 - r0) / abs(r0) * 100)
    v8 = avg(diffs) if len(diffs) >= 2 else None
    crit.append({"k": "schaal", "v": rnd(v8, 1), "p": band(v8, -2, 2, high_is_good=False)})

    # 9. Koers sinds eerste notering (±5% = gelijk)
    fp = (c.get("first") or {}).get("p")
    v9 = (price / fp - 1) * 100 if price and fp else None
    crit.append({"k": "ipo", "v": rnd(v9, 0), "p": band(v9, -5, 5), "since": (c.get("first") or {}).get("d")})

    # 10. Koers/winst
    pe = c.get("pe")
    if c.get("eps") is not None and c["eps"] <= 0:
        v10, p10 = None, 0                        # verlieslatend
    else:
        v10 = pe
        p10 = None if pe is None else (10 if pe < 10 else 5 if pe <= 30 else 0)
    crit.append({"k": "kw", "v": rnd(v10, 1), "p": p10, "loss": p10 == 0 and v10 is None})

    got = [x["p"] for x in crit if x["p"] is not None]
    score = sum(got) / (10 * len(got)) * 100 if len(got) >= MIN_CRITERIA else None
    extra = {
        "rev_yoy": g[-1] if g else None,
        "opex_yoy": growth_list(opex, 1)[-1] if len(opex) >= 2 and growth_list(opex, 1) else None,
        "margin": v2,
        "rev_cagr": v1,
        "rev_drops": sum(1 for x in growth_list(rev, 4) if x < 0),
        "cash_now": (cash_sti(ts, "quarterly") or cash_sti(ts, "annual") or [[None, None]])[-1][1],
        "debt_now": (series(ts, "quarterlyTotalDebt", "annualTotalDebt") or [[None, None]])[-1][1],
    }
    return score, crit, len(got), extra


# ── Kwaliteit: moat, CEO, recessiebestendig ──────────────────────────────
def quality(c, extra, curated):
    gm, om, roe = c.get("gm"), extra.get("margin"), c.get("roe")
    om = om / 100 if om is not None else c.get("om")
    ins, beta, cagr = c.get("ins"), c.get("beta"), extra.get("rev_cagr")

    moat = 1
    if gm is not None:
        moat += 4 if gm >= .6 else 3 if gm >= .4 else 2 if gm >= .25 else 0
    if roe is not None:
        moat += 3 if roe >= .2 else 2 if roe >= .12 else 1 if roe >= .05 else 0
    if om is not None:
        moat += 2 if om >= .2 else 1 if om >= .1 else 0

    ceo = 1
    if ins is not None:
        ceo += 3 if ins >= .1 else 2 if ins >= .02 else 1
    if cagr is not None:
        ceo += 4 if cagr >= 15 else 3 if cagr >= 7 else 2 if cagr >= 2 else 0
    if roe is not None:
        ceo += 2 if roe >= .15 else 1 if roe >= .08 else 0

    rec = SECTOR_DEFENSIVE.get(c.get("sector"), 2)
    if beta is not None:
        rec += 3 if beta < .7 else 2 if beta < 1 else 1 if beta < 1.3 else 0
    drops = extra.get("rev_drops")
    if drops is not None:
        rec += 3 if drops == 0 else 1 if drops == 1 else 0

    est = {"moat": min(moat, 10), "ceo": min(ceo, 10), "recessie": min(rec, 10)}
    out = {}
    for k in ("moat", "ceo", "recessie"):
        if curated and isinstance(curated.get(k), (int, float)):
            out[k] = {"s": max(0, min(10, curated[k])), "src": "oordeel"}
        else:
            out[k] = {"s": est[k], "src": "schatting"}
    out["note"] = (curated or {}).get("noot")
    return out


# ── Verwacht rendement ───────────────────────────────────────────────────
def expected(c, price_native, extra):
    div = (c.get("div") or 0) * 100
    tgt, n = c.get("tgt"), c.get("n_an") or 0
    base = c.get("tgt_px") or price_native
    if tgt and base and n >= MIN_ANALYSTS:
        up = (tgt / (price_native or base) - 1) * 100
        return {"pct": rnd(max(-60, min(150, up + div)), 1), "src": "analisten", "upside": rnd(up, 1),
                "div": rnd(div, 1), "n": int(n), "rating": c.get("rating"), "tgt": tgt}
    cagr = extra.get("rev_cagr")
    if cagr is None:
        return {"pct": rnd(div, 1) if div else None, "src": "dividend" if div else None, "div": rnd(div, 1)}
    model = max(-15, min(30, cagr)) * 0.8
    return {"pct": rnd(model + div, 1), "src": "schatting", "growth": rnd(model, 1), "div": rnd(div, 1)}


# ── Samenvoegen ──────────────────────────────────────────────────────────
def build_item(sym, c, prices, fx, curated, own, gics=None):
    region = c.get("region")
    q = prices.get(sym, {})
    price_native = q.get("price") or c.get("price")
    if not price_native:
        return None
    if q.get("pe"):
        c = dict(c, pe=q["pe"])
    if not c.get("sector") and gics:
        c = dict(c, sector=gics)
    score_f, crit, n_crit, extra = formula(c, price_native)
    if score_f is None:
        return None
    qual = quality(c, extra, curated)
    kwal = qual["moat"]["s"] + qual["ceo"]["s"] + qual["recessie"]["s"]
    total = W_FORMULE * score_f + (1 - W_FORMULE) * kwal / 30 * 100

    if region == "VS":
        eu = c.get("eu") or {}
        eq = prices.get(eu.get("sym") or "", {})
        if eu.get("sym") and eq.get("price") and eq.get("cur") == "EUR":
            price_eur, approx, eu_sym, exch = eq["price"], False, eu["sym"], eu.get("exch")
        else:
            price_eur, approx, eu_sym, exch = price_native / fx, True, eu.get("sym"), eu.get("exch")
    else:
        price_eur, approx, eu_sym, exch = price_native, False, sym, EXCH_NAME.get(c.get("exch"), c.get("exch"))
    k = price_eur / price_native

    exp = expected(c, price_native, extra)
    if exp.get("tgt"):
        exp["tgt_eur"] = rnd(exp.pop("tgt") * k, 2)

    cash, debt = extra.get("cash_now"), extra.get("debt_now")
    steps = [
        None if cash is None or debt is None else int(cash > debt),
        None if extra.get("rev_yoy") is None else int(extra["rev_yoy"] >= 10),
        None if extra.get("margin") is None else int(extra["margin"] >= 15),
        None if extra.get("rev_yoy") is None or extra.get("opex_yoy") is None
        else int(extra["rev_yoy"] > extra["opex_yoy"]),
        int(qual["moat"]["s"] >= 7), int(qual["ceo"]["s"] >= 7), int(qual["recessie"]["s"] >= 7),
    ]
    hi, lo = q.get("hi52") or c.get("hi52"), q.get("lo52") or c.get("lo52")
    nm = norm_name(c.get("name"))
    return {
        "sym": sym, "eu": eu_sym, "exch": exch, "name": c.get("name") or sym,
        "us_exch": US_EXCH.get(c.get("exch"), c.get("exch")) if region == "VS" else None,
        "region": region, "country": c.get("country"), "sector": c.get("sector") or gics,
        "industry": c.get("industry"),
        "price": rnd(price_eur, 2), "approx": approx,
        "native": rnd(price_native, 2), "cur": "USD" if region == "VS" else "EUR",
        "day": rnd(q.get("day"), 2),
        "hi52": rnd(hi * k, 2) if hi else None, "lo52": rnd(lo * k, 2) if lo else None,
        "mcap": rnd(((q.get("mcap") or c.get("mcap") or 0) * k) / 1e9, 1) or None,
        "score": rnd(total, 1), "formule": rnd(score_f, 1), "n_crit": n_crit,
        "kwal": kwal, "crit": crit, "q": qual, "steps": steps, "exp": exp,
        "cashdebt": [rnd(cash / 1e9 * (1 / fx if region == "VS" else 1), 2) if cash is not None else None,
                     rnd(debt / 1e9 * (1 / fx if region == "VS" else 1), 2) if debt is not None else None],
        "watch": sym in own["watch"] or (eu_sym or "") in own["watch"] or nm in own["names_watch"],
        "_own": sym in own["own"] or (eu_sym or "") in own["own"] or (nm and nm in own["names_own"]),
    }


def main():
    t0 = time.time()
    y = Yahoo()
    try:
        cache = json.load(open(CACHE, encoding="utf-8")).get("stocks", {})
    except Exception:  # noqa: BLE001
        cache = {}
    uni = load_universe(cache)
    own = owned_positions()
    quality_map = load_quality()
    print(f"Universum: {len(uni)} stocks · eigen posities: {len(own['own'])} · kwaliteitsoordelen: {len(quality_map)}")

    try:
        fx = fx_eurusd(y)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"EUR/USD niet opgehaald ({e}); probeer het later opnieuw.")
    print(f"EUR/USD {fx:.4f}")

    def is_owned(sym):
        c = cache.get(sym) or {}
        return sym in own["own"] or ((c.get("eu") or {}).get("sym") or "") in own["own"]

    # 1. Fundamentele cijfers verversen: oudste eerst, binnen het tijdsbudget
    todo = [s for s in uni if not is_owned(s) and needs_refresh(cache.get(s), uni[s]["region"])]
    todo.sort(key=lambda s: (cache.get(s) or {}).get("f") or "")
    print(f"Te verversen: {len(todo)}")
    fails, done = 0, 0
    for sym in todo:
        if time.time() - t0 > TIME_BUDGET_MIN * 60:
            print("Tijdsbudget op; de volgende run gaat verder.")
            break
        try:
            cache[sym] = fetch_stock(y, sym, uni[sym], cache.get(sym), fx)
            fails, done = 0, done + 1
            c = cache[sym]
            print(f"  {sym:<10} {'OVERGESLAGEN (' + c['skip'] + ')' if c.get('skip') else 'ok'}"
                  f"{'  €: ' + c['eu']['sym'] if (c.get('eu') or {}).get('sym') else ''}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            old = cache.get(sym) or {}
            old.update({"err": str(e)[:160], "err_at": iso(), "src": uni[sym]["src"], "region": uni[sym]["region"]})
            cache[sym] = old
            print(f"  {sym:<10} FOUT: {e}")
            if fails >= MAX_FAILS:
                print("Te veel fouten achter elkaar — Yahoo blokkeert waarschijnlijk; stoppen voor deze run.")
                break

    # 2. Actuele koersen (dollar- en euro-noteringen)
    ready = [s for s in uni if (cache.get(s) or {}).get("ts") and not cache[s].get("skip")]
    syms = set(ready) | {(cache[s].get("eu") or {}).get("sym") for s in ready if (cache[s].get("eu") or {}).get("sym")}
    prices = batch_quotes(y, syms)
    print(f"Koersen: {len(prices)} van {len(syms)}")

    # 3. Scoren
    items = []
    for sym in ready:
        try:
            it = build_item(sym, cache[sym], prices, fx, quality_map.get(sym)
                            or quality_map.get((cache[sym].get("eu") or {}).get("sym") or ""), own,
                            uni[sym].get("gics"))
        except Exception as e:  # noqa: BLE001
            print(f"  {sym:<10} score mislukt: {e}")
            continue
        if it and not it.pop("_own"):
            items.append(it)
    items.sort(key=lambda x: (-x["score"], -x["formule"], x["name"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i

    tried = lambda s: (cache.get(s) or {}).get("f") or (cache.get(s) or {}).get("err_at")
    pending = sum(1 for s in uni if not is_owned(s) and not tried(s))
    failed = sum(1 for s in uni if not is_owned(s) and not (cache.get(s) or {}).get("f") and (cache.get(s) or {}).get("err_at"))
    out = {
        "updated": iso(),
        "fx": rnd(fx, 4),
        "universe": len(uni),
        "scanned": sum(1 for s in uni if tried(s)),
        "failed": failed,
        "scored": len(items),
        "pending": pending,
        "weights": {"formule": W_FORMULE, "kwaliteit": round(1 - W_FORMULE, 2)},
        "items": items[:TOP_N],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"updated": iso(), "stocks": compact(dict(sorted(cache.items())))}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"Klaar: {len(items)} gescoord, top {min(TOP_N, len(items))} weggeschreven · "
          f"{done} ververst · nog {pending} nooit gescand · {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
