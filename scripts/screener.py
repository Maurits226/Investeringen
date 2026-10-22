#!/usr/bin/env python3
"""
Top 250 — genereert screener.json voor screener.html.

Zoekt kansen BUITEN je portefeuille in een breed universum:
  * alle Amerikaanse bedrijven boven $2 mld marktwaarde op NYSE/Nasdaq (Yahoo-screener, max. 1.500, geen OTC) + de S&P 500
  * alle eurozone-bedrijven boven €1 mld op hun thuisbeurs (Xetra, Parijs, Amsterdam, Milaan,
    Madrid, Brussel, Helsinki, Dublin, Wenen, Lissabon)
  * plus screener_universe.txt voor eigen toevoegingen
Alleen euro- en dollarbedrijven: bedrijven die in een andere valuta noteren of rapporteren vallen af.

Per stock:
  * De 12 stappen en de 125-punten scorecard uit 'How to Find the Best Stocks' (Tom Nash):
    alles wat uit de cijfers te halen is wordt nagerekend, de rest staat als checklist klaar.
    Moat, CEO e.d.: handmatig oordeel uit kwaliteit.json, anders een strenge schatting uit de cijfers.
  * Marktrang (Score 2.0): scorecard + kwaliteit, groei, waardering en momentum t.o.v. de sector
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
import unicodedata
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# ── Instellingen ─────────────────────────────────────────────────────────
TOP_N = 650                 # in screener.json, op volgorde van de scorecard; de pagina toont scorecard 75+ (max. 500)
CARD_SELECT = 75            # vanaf deze scorecard staat een bedrijf in de kansenlijst (75-100 watchlist, 100+ once-in-a-lifetime)
# Breed universum via de Yahoo-screener: de grootste bedrijven van de markt, wekelijks ververst
DYN_REFRESH_DAYS = 7
DYN_V = 2                                                  # v2: alleen hoofdbeurzen in de VS (geen OTC); oudere lijst direct opnieuw ophalen
DYN_US = {"min_cap": 2e9, "limit": 1500}                   # VS: alle bedrijven boven $2 mld op de hoofdbeurzen, grootste eerst
DYN_EU = {"min_cap": 1e9, "limit": 200,                    # eurozone: per thuisbeurs boven €1 mld
          "exchanges": ["GER", "PAR", "AMS", "MIL", "MCE", "BRU", "HEL", "ISE", "VIE", "LIS"]}
OK_FIN_CUR = {"USD", "EUR"}
# Score 2.0 (marktrang): vijf pijlers. F = scorecard van de gids (absoluut), de andere vier zijn percentielen binnen de sector.
PILLAR_W = {"F": 0.30, "Q": 0.20, "G": 0.20, "V": 0.15, "M": 0.15}
MIN_PEERS = 15               # minder bedrijven in een sector? Dan vergelijken met de hele markt
METRICS = {                  # kenmerk: (pijler, hoger = beter)
    "gp_assets": ("Q", True), "roic": ("Q", True), "fcf_margin": ("Q", True), "cash_conv": ("Q", True),
    "dilution": ("Q", False),
    "rev_cagr3": ("G", True), "rev_yoy": ("G", True), "eps_g": ("G", True), "fwd_g": ("G", True),
    "fpe": ("V", False), "peg": ("V", False), "fcf_yield": ("V", True), "ev_ebitda": ("V", False),
    "chg12": ("M", True), "near_high": ("M", True), "revisions": ("M", True),
}
# Jouw branches met een referentie-ETF (in euro op Xetra)
BRANCHES = {
    "tech":    {"label": "Technologie",     "etf": "QDVE.DE", "etf_name": "iShares S&P 500 Information Technology"},
    "fin":     {"label": "Financieel",      "etf": "QDVH.DE", "etf_name": "iShares S&P 500 Financials"},
    "zorg":    {"label": "Gezondheidszorg", "etf": "QDVG.DE", "etf_name": "iShares S&P 500 Health Care"},
    "energie": {"label": "Energie & energietransitie", "etf": "IQQH.DE", "etf_name": "iShares Global Clean Energy"},
}
ENERGY_RE = re.compile(r"solar|renewable|electrical equipment|uranium", re.I)
ENERGY_EXTRA = {"ENR.DE", "GEV", "BE", "NDX1.DE"}   # Siemens Energy, GE Vernova, Bloom Energy, Nordex
# ETF-doorkijk voor je eigen ETF's: eerst het ETF zelf, anders een (Amerikaanse) tegenhanger met dezelfde index
ETF_LOOKTHROUGH = {
    "QDVE.DE": ["QDVE.DE", "IUIT.L", "XLK"],
    "VUAA.MI": ["VUAA.MI", "VUAA.L", "VOO"],
    "VHYL.MI": ["VHYL.MI", "VHYL.L", "VYM"],
}
ETF_REFRESH_DAYS = 7
ETF_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etf_holdings.json")  # bedrijven die in een andere valuta rapporteren (bv. Taiwanese ADR's) vallen af
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
KOOP_OUT = os.path.join(ROOT, "koopmoment.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

EU_SUFFIX = {"DE", "PA", "AS", "MI", "MC", "BR", "HE", "IR", "VI", "LS", "DU", "F"}
EXCH_NAME = {
    "GER": "Xetra", "FRA": "Frankfurt", "DUS": "Düsseldorf", "PAR": "Euronext Parijs",
    "AMS": "Euronext Amsterdam", "MIL": "Borsa Italiana", "MCE": "BME Madrid",
    "BRU": "Euronext Brussel", "HEL": "Nasdaq Helsinki", "ISE": "Euronext Dublin",
    "VIE": "Wiener Börse", "LIS": "Euronext Lissabon", "STU": "Stuttgart", "MUN": "München",
}
# euro-notering voor VS-bedrijven, in deze volgorde: Nederland → Duitsland → Oostenrijk
EU_LISTING_PREF = ["AMS", "GER", "FRA", "DUS", "VIE"]
# hoofdbeurzen in de VS (Yahoo-codes); al het andere (PNK, OQX, OQB e.d.) is OTC en valt af
US_EXCH = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "NYSE American",
           "PCX": "NYSE Arca", "BTS": "Cboe", "TEX": "NYSE Texas"}
CACHE_V = 4                                 # v4: extra cijfers voor de 12 stappen (balans, kwartalen, insiders, winstverrassingen)
# S&P 500-lijst (GICS) -> Yahoo-sectornamen, als Yahoo zelf geen sector geeft
GICS_TO_YAHOO = {
    "Information Technology": "Technology", "Health Care": "Healthcare", "Financials": "Financial Services",
    "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive",
    "Industrials": "Industrials", "Communication Services": "Communication Services", "Energy": "Energy",
    "Utilities": "Utilities", "Real Estate": "Real Estate", "Materials": "Basic Materials",
}

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

    def post(self, url, payload, crumb=True, tries=3):
        last = None
        for i in range(tries):
            full = url + (("&" if "?" in url else "?") + "crumb=" + quote(self._get_crumb()) if crumb else "")
            req = urllib.request.Request(full, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with self.op.open(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403):
                    self.crumb = None
                time.sleep(3 * (i + 1))
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3 * (i + 1))
        raise RuntimeError(str(last))

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
             "CurrentAssets", "CurrentLiabilities", "TotalDebt",
             "GrossProfit", "TotalAssets", "FreeCashFlow", "OperatingCashFlow", "NetIncome",
             "DilutedAverageShares", "EBITDA", "InterestExpense",
             # 12 stappen: Loan Officer Test (stap 8) en rode vlaggen (stap 11)
             "TotalLiabilitiesNetMinorityInterest", "StockholdersEquity", "Goodwill", "Inventory"]
TS_QUARTER = ["CashAndCashEquivalents", "CashCashEquivalentsAndShortTermInvestments", "TotalDebt",
              "FreeCashFlow", "TotalRevenue"]     # stap 3: vrije kasstroom en netto cash over 4-6 kwartalen


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
            out[t[0]] = rows[-6:] if t[0].startswith("quarterly") else rows[-5:]
    # dubbele reeksen weglaten
    if out.get("annualTotalRevenue"):
        out.pop("annualOperatingRevenue", None)
    if out.get("annualCashCashEquivalentsAndShortTermInvestments"):
        out.pop("annualCashAndCashEquivalents", None)
        out.pop("annualOtherShortTermInvestments", None)
    return out


def summary(y, sym):
    """Kerngegevens, eigendom, analisten en profiel."""
    mods = ("price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,summaryProfile,"
            "earningsTrend,calendarEvents,earningsHistory,netSharePurchaseActivity")
    j = y.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(sym)}"
              f"?modules={mods}", crumb=True)
    r = j["quoteSummary"]["result"][0]

    def raw(mod, k):
        v = (r.get(mod) or {}).get(k)
        return v.get("raw") if isinstance(v, dict) else (v if isinstance(v, (int, float, str)) else None)

    pr = r.get("price") or {}
    ap = dict(r.get("summaryProfile") or {}, **{k: v for k, v in (r.get("assetProfile") or {}).items() if v})

    # Analistenverwachtingen: groei volgend jaar en bijstellingen van de winstverwachting (30 dagen)
    fwd_growth, up30, down30 = None, 0, 0
    for t in ((r.get("earningsTrend") or {}).get("trend") or []):
        per = t.get("period")
        g = (t.get("growth") or {}).get("raw") if isinstance(t.get("growth"), dict) else None
        if per == "+1y" and g is not None:
            fwd_growth = g
        if per in ("0y", "+1y"):
            rv = t.get("epsRevisions") or {}
            up30 += ((rv.get("upLast30days") or {}).get("raw") or 0) if isinstance(rv.get("upLast30days"), dict) else 0
            down30 += ((rv.get("downLast30days") or {}).get("raw") or 0) if isinstance(rv.get("downLast30days"), dict) else 0
    ed = (((r.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or [])
    earn = None
    for e in ed:
        ts_ = e.get("raw") if isinstance(e, dict) else None
        if ts_:
            earn = datetime.fromtimestamp(ts_, tz=timezone.utc).date().isoformat()
            break
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
        "rating": (r.get("financialData") or {}).get("recommendationKey")
        if (r.get("financialData") or {}).get("recommendationKey") in ("strong_buy", "buy", "hold", "underperform", "sell") else None,
        "fcur": (r.get("financialData") or {}).get("financialCurrency"),
        "gm": raw("financialData", "grossMargins"),
        "om": raw("financialData", "operatingMargins"),
        "roe": raw("financialData", "returnOnEquity"),
        "fcf": raw("financialData", "freeCashflow"),
        "ocf": raw("financialData", "operatingCashflow"),
        "rev_ttm": raw("financialData", "totalRevenue"),
        "eps_g": raw("financialData", "earningsGrowth"),
        "rev_g": raw("financialData", "revenueGrowth"),
        "peg": raw("defaultKeyStatistics", "pegRatio"),
        "ev_ebitda": raw("defaultKeyStatistics", "enterpriseToEbitda"),
        "chg52": raw("defaultKeyStatistics", "52WeekChange"),
        "sma50": raw("summaryDetail", "fiftyDayAverage"),
        "sma200": raw("summaryDetail", "twoHundredDayAverage"),
        "fwd_growth": fwd_growth,
        "rev_up": up30, "rev_down": down30,
        "earnings": earn,
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "country": ap.get("country"),
        "emp": ap.get("fullTimeEmployees") if isinstance(ap.get("fullTimeEmployees"), (int, float)) else None,
        "founder": any("founder" in (o.get("title") or "").lower() for o in ap.get("companyOfficers") or []) or None,
        # 12 stappen
        "co_ceo": any(re.search(r"co-?\s?(ceo|chief executive)", (o.get("title") or ""), re.I)
                      for o in ap.get("companyOfficers") or []) or None,
        "about": (ap.get("longBusinessSummary") or "")[:240] or None,
        "eps_hist": [[(h.get("epsActual") or {}).get("raw"), (h.get("epsEstimate") or {}).get("raw")]
                     for h in ((r.get("earningsHistory") or {}).get("history") or [])][-4:] or None,
        "ins_buy": raw("netSharePurchaseActivity", "buyInfoShares"),
        "ins_sell": raw("netSharePurchaseActivity", "sellInfoShares"),
        "ins_net": raw("netSharePurchaseActivity", "netInfoShares"),
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
                "chg52": q.get("fiftyTwoWeekChangePercent"), "fpe": q.get("forwardPE"),
                "sma50": q.get("fiftyDayAverage"), "sma200": q.get("twoHundredDayAverage"),
            }
        time.sleep(SLEEP)
    return out


def branch_of(sym, sector, industry):
    if sym in ENERGY_EXTRA:
        return "energie"
    if sector == "Technology":
        return "tech"
    if sector == "Financial Services":
        return "fin"
    if sector == "Healthcare":
        return "zorg"
    if sector in ("Energy", "Utilities") or ENERGY_RE.search(industry or ""):
        return "energie"
    return None


def returns_from_chart(pts):
    """Rendement 12 maanden en 3 maanden uit een dagreeks."""
    if len(pts) < 30:
        return None, None
    last = pts[-1][1]
    r12 = last / pts[0][1] - 1
    r3 = last / pts[-min(63, len(pts))][1] - 1
    return r12, r3


def benchmarks(y):
    """Referentie-ETF's per branche + EUR/USD over 12 maanden (om dollarrendementen naar euro om te rekenen)."""
    out = {}
    for key, b in BRANCHES.items():
        try:
            pts, meta = chart(y, b["etf"], "1y", "1d")
            r12, r3 = returns_from_chart(pts)
            out[key] = dict(b, price=rnd(meta.get("regularMarketPrice") or (pts[-1][1] if pts else None), 2),
                            ret12=rnd(r12 * 100 if r12 is not None else None, 1),
                            ret3m=rnd(r3 * 100 if r3 is not None else None, 1))
        except Exception as e:  # noqa: BLE001
            print(f"  referentie-ETF {b['etf']} niet opgehaald: {e}")
            out[key] = dict(b)
        time.sleep(SLEEP)
    fxchg = None
    try:
        pts, _ = chart(y, "EURUSD=X", "1y", "1d")
        fxchg = pts[-1][1] / pts[0][1] - 1 if len(pts) > 30 else None
    except Exception:  # noqa: BLE001
        pass
    return out, fxchg


def etf_lookthrough(y, prev):
    """Grootste posities van je ETF's (weekelijks). Resultaat naar etf_holdings.json."""
    if prev and age_h(prev.get("at")) < ETF_REFRESH_DAYS * 24 and prev.get("etfs"):
        return prev
    res = {"at": iso(), "etfs": {}}
    for etf, cands in ETF_LOOKTHROUGH.items():
        for cand in cands:
            try:
                j = y.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(cand)}?modules=topHoldings",
                          crumb=True)
                th = (j["quoteSummary"]["result"][0].get("topHoldings") or {}).get("holdings") or []
                hold = [{"sym": (h.get("symbol") or "").upper(), "name": h.get("holdingName"),
                         "w": rnd(((h.get("holdingPercent") or {}).get("raw") or 0) * 100, 2)} for h in th]
                hold = [h for h in hold if h["w"]]
                if hold:
                    res["etfs"][etf] = {"src": cand, "proxy": cand != etf, "holdings": hold}
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        if etf not in res["etfs"]:
            print(f"  ETF-doorkijk {etf}: geen posities gevonden")
    if not res["etfs"] and prev:
        return prev
    return res


def pct_ranks(items, key, higher, group_of):
    """Percentiel (0-100) per bedrijf binnen zijn groep; te kleine groepen vergelijken met de hele markt."""
    from bisect import bisect_left, bisect_right
    vals = [(it, it["m"].get(key)) for it in items if it["m"].get(key) is not None]
    groups = {}
    for it, v in vals:
        groups.setdefault(group_of(it), []).append(v)
    groups = {g: sorted(v) for g, v in groups.items()}
    allv = sorted(v for _, v in vals)
    for it, v in vals:
        g = groups.get(group_of(it)) or []
        ref = g if len(g) >= MIN_PEERS else allv
        n = len(ref)
        if not n:
            continue
        lo, hi = bisect_left(ref, v), bisect_right(ref, v)
        p = (lo + 0.5 * (hi - lo)) / n * 100
        it["mp"][key] = round(p if higher else 100 - p, 1)


# ── Universum en eigen posities ──────────────────────────────────────────
def yahoo_screen(y, operands, limit, keep=None):
    """Yahoo-screener: aandelen die aan de voorwaarden voldoen, grootste marktwaarde eerst.
    keep: alleen wat hieraan voldoet telt mee voor de limiet, zodat die niet opgaat aan regels die toch afvallen."""
    out, offset = [], 0
    while len(out) < limit:
        payload = {"offset": offset, "size": 250, "sortField": "intradaymarketcap", "sortType": "DESC",
                   "quoteType": "EQUITY", "userId": "", "userIdType": "guid",
                   "query": {"operator": "AND", "operands": operands}}
        j = y.post("https://query2.finance.yahoo.com/v1/finance/screener?lang=en-US&region=US&formatted=false", payload)
        res = ((j.get("finance") or {}).get("result") or [{}])[0]
        quotes = res.get("quotes") or []
        for q in quotes:
            if q.get("symbol"):
                row = {"sym": q["symbol"].upper(), "name": q.get("longName") or q.get("shortName"),
                       "cur": q.get("currency"), "exch": q.get("exchange")}
                if keep is None or keep(row):
                    out.append(row)
        offset += len(quotes)
        if not quotes or offset >= (res.get("total") or 0):
            break
        time.sleep(SLEEP)
    return out[:limit]


def us_main_row(q):
    """Gewoon aandeel op een hoofdbeurs in de VS. OTC (o.a. de F/Y-ADR's van buitenlandse bedrijven, zoals ERNXY)
    en preferente aandelen (JPM-PC) vallen af: anders gaan honderden plekken van de limiet daaraan op."""
    return (q.get("exch") in US_EXCH and "." not in q["sym"] and not re.search(r"-P[A-Z]?$", q["sym"])
            and (q.get("cur") or "USD") == "USD")


def eu_row(q):
    """Euro-notering op een beurs in de eurozone."""
    return "." in q["sym"] and q["sym"].rsplit(".", 1)[1] in EU_SUFFIX and (q.get("cur") or "EUR") == "EUR"


def dynamic_universe(y, dyn):
    """Breed universum (VS + eurozone) uit de Yahoo-screener; wekelijks ververst, anders uit de cache."""
    if dyn and age_h(dyn.get("at")) < DYN_REFRESH_DAYS * 24 and dyn.get("us") and dyn.get("v") == DYN_V:
        return dyn
    try:
        us = yahoo_screen(y, [{"operator": "EQ", "operands": ["region", "us"]},
                              {"operator": "GT", "operands": ["intradaymarketcap", DYN_US["min_cap"]]}],
                          DYN_US["limit"], us_main_row)
        eu = []
        for ex in DYN_EU["exchanges"]:
            try:
                eu += yahoo_screen(y, [{"operator": "EQ", "operands": ["exchange", ex]},
                                       {"operator": "GT", "operands": ["intradaymarketcap", DYN_EU["min_cap"]]}],
                                   DYN_EU["limit"], eu_row)
            except Exception as e:  # noqa: BLE001
                print(f"  screener {ex} mislukt: {e}")
        if len(us) < 100:
            raise RuntimeError(f"te weinig VS-resultaten ({len(us)})")
        dyn = {"at": iso(), "v": DYN_V, "us": [[q["sym"], q["name"]] for q in us], "eu": [[q["sym"], q["name"]] for q in eu]}
        print(f"Yahoo-screener: {len(us)} VS (hoofdbeurzen), {len(eu)} eurozone")
    except Exception as e:  # noqa: BLE001
        print(f"Yahoo-screener niet gelukt ({e}); vorige lijst gebruikt")
    return dyn or {}


def merge_dyn(uni, dyn, cache):
    """Screenerlijsten toevoegen aan het universum, per bedrijf via één kant (gekoppeld op de volledige naam):
      * eurozone-bedrijf met zijn thuisnotering in de lijst (ASML.AS, SAP.DE): de VS-notering (ADR) valt af;
      * elk ander bedrijf (VS, Taiwan, VK, Zwitserland...): de VS-hoofdnotering blijft en de euro-noteringen
        op Xetra, Wenen, Milaan e.d. vallen af (TSM blijft, TSFA.VI niet).
    Het land komt uit de cache (eerder opgehaald). Niet bekend en een notering op Xetra of Wenen? Dan blijven beide
    staan: na het ophalen is het land bekend en ruimt dedupe_listings de dubbele op (en de volgende run deze regel).
    Een euro-notering die je zelf in screener_universe.txt zet (SHELL.AS) komt daarna toch weer binnen en gaat dan voor."""
    country = lambda s: (cache.get(s) or {}).get("country")  # noqa: E731
    syms_of = {}                                   # volledige naam -> alle noteringen met die naam
    for s, n in dyn.get("us", []) + dyn.get("eu", []):
        if name_key(n):
            syms_of.setdefault(name_key(n), set()).add(s)

    def country_of(k, s):
        """Land van deze notering, anders van een andere notering van hetzelfde bedrijf."""
        return country(s) or next((country(x) for x in sorted(syms_of.get(k) or ()) if country(x)), None)
    n_us, n_eu = 0, 0
    for sym, name in dyn.get("us", []):
        if sym in uni:
            continue
        k = name_key(name)
        if any(ez_home(e, country_of(k, e)) for e in syms_of.get(k) or () if "." in e):
            n_us += 1
            continue                               # ADR van een eurozone-bedrijf: we nemen de thuisnotering
        uni[sym] = {"region": "VS", "src": "screener", "name": name}
    # namen van de VS-bedrijven in het universum: uit de screener, de cache en de S&P 500-lijst ('Amazon' naast 'Amazon.com, Inc.')
    # plus de euro-notering die al bij een VS-bedrijf hoort (GCP.DE bij GE, ook al heet die nu 'GE Aerospace')
    dyn_name = dict(dyn.get("us", []))
    full, short, eu_line = set(), set(), set()
    for s, v in uni.items():
        if v["region"] == "VS":
            for n in (v.get("name"), dyn_name.get(s), (cache.get(s) or {}).get("name")):
                if n:
                    full.add(name_key(n))
                    short.add(norm_name(n))
            eu_line.add(((cache.get(s) or {}).get("eu") or {}).get("sym"))
    for sym, name in dyn.get("eu", []):
        if sym in uni:
            continue
        k = name_key(name)
        if (k in full or norm_name(name) in short or sym in eu_line) and ez_home(sym, country_of(k, sym)) is False:
            n_eu += 1
            continue                               # euro-notering van een VS- of ander niet-eurozonebedrijf
        uni[sym] = {"region": "EU", "src": "screener", "name": name}
    print(f"Screener: {n_us} ADR's van eurozone-bedrijven en {n_eu} tweede noteringen van VS-bedrijven e.d. weggelaten")


def load_universe(cache, y=None, dyn=None):
    uni = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(SP500_URL, headers={"User-Agent": UA}),
                                    timeout=30) as r:
            sp = {}
            for row in csv.DictReader(io.StringIO(r.read().decode("utf-8"))):
                s = (row.get("Symbol") or "").strip().upper().replace(".", "-")
                if not re.fullmatch(r"[A-Z]{1,5}(-[A-Z]{1,2})?", s):
                    continue                      # geen geldige ticker: overslaan
                sp[s] = {"region": "VS", "src": "S&P 500", "name": row.get("Security"),
                         "gics": GICS_TO_YAHOO.get((row.get("GICS Sector") or "").strip())}
        if not 450 <= len(sp) <= 550:             # externe lijst ziet er vreemd uit: niet vertrouwen
            raise ValueError(f"onverwacht aantal bedrijven ({len(sp)})")
        uni.update(sp)
        print(f"S&P 500: {len(uni)} bedrijven")
    except Exception as e:  # noqa: BLE001
        print(f"S&P 500-lijst niet opgehaald ({e}); vorige lijst uit de cache gebruikt")
        for s, c in cache.items():
            if c.get("src") == "S&P 500":
                uni[s] = {"region": "VS", "src": "S&P 500", "name": c.get("name")}
    # Breed universum uit de Yahoo-screener (grootste bedrijven van de markt)
    if y is not None:
        dyn = dynamic_universe(y, dyn)
        merge_dyn(uni, dyn, cache)
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
    return uni, dyn


# ── Dubbele noteringen ───────────────────────────────────────────────────
# Hetzelfde bedrijf staat vaak op meerdere beurzen (Xetra, Milaan, Wenen, VS, OTC).
# Per bedrijf blijft één notering over: Nederland → Duitsland → Oostenrijk → daarna:
# thuisbeurs in de eurozone, dan de hoofdnotering in de VS, dan overige, OTC als laatste.
# Uitzondering: een VS-bedrijf houdt zijn hoofdnotering in de VS (zie dedupe_listings).
LISTING_TIER = {"AS": 0, "DE": 1, "F": 2, "DU": 3, "VI": 4}
HOME_SUFFIX = {"France": "PA", "Italy": "MI", "Spain": "MC", "Belgium": "BR", "Finland": "HE",
               "Ireland": "IR", "Portugal": "LS", "Netherlands": "AS", "Germany": "DE", "Austria": "VI"}
EUROZONE = set(HOME_SUFFIX) | {"Luxembourg", "Greece", "Slovenia", "Slovakia", "Estonia", "Latvia", "Lithuania",
                               "Malta", "Cyprus", "Croatia"}
EZ_HOME = {"AS", "PA", "MI", "MC", "BR", "HE", "IR", "LS"}    # thuisbeurzen; Xetra en Wenen noteren ook veel buitenlandse aandelen
US_MAIN = set(US_EXCH.values())
LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "nv", "se", "sa", "ag", "spa",
         "sab", "cv", "de", "the", "and", "ltd", "limited", "oyj", "asa", "ab", "aktiengesellschaft",
         "societe", "anonyme", "group", "holding", "holdings"}


def company_key(name, country):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", n)          # S.p.A. / S.A.B. -> spa / sab
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    words = [w for w in n.split() if w not in LEGAL and len(w) > 1]
    return (" ".join(words), country or "") if words else None


def name_key(name):
    """Volledige bedrijfsnaam zonder rechtsvorm (als company_key), om noteringen te koppelen.
    Preciezer dan norm_name: 'Bank of America' en 'Bank of Ireland' zijn verschillende bedrijven."""
    k = company_key(name, None)
    return k[0] if k else None


def ez_home(sym, country):
    """Thuisnotering van een eurozone-bedrijf? True/False, of None als dat zonder land niet te zeggen is
    (Xetra en Wenen noteren ook veel buitenlandse aandelen in euro, zoals NVD.DE en TSFA.VI)."""
    if "." not in sym:
        return False
    suf = sym.rsplit(".", 1)[1]
    if (suf == "MI" and sym[:1].isdigit()) or (suf == "MC" and sym[:1] == "X"):
        return False                               # buitenlandse aandelen in Milaan (1AMZN.MI) en Madrid/Latibex (XPBR.MC)
    if country:
        return country in EUROZONE and (suf in EZ_HOME or suf == HOME_SUFFIX.get(country))
    return True if suf in EZ_HOME else None


def listing_rank(it):
    sym = it["sym"]
    if it["region"] == "VS":
        tier = 6 if (it.get("us_exch") or "") in US_MAIN else 8
    else:
        suf = sym.rsplit(".", 1)[1] if "." in sym else ""
        tier = LISTING_TIER.get(suf, 5 if HOME_SUFFIX.get(it.get("country")) == suf else 7)
    preferred = 1 if re.search(r"-P[A-Z]?$", sym) else 0     # preferente aandelen achteraan
    no_eur = 0 if it.get("eu") and not it.get("approx") else 1  # bij gelijke beurs: met euro-notering eerst
    return (tier, preferred, no_eur, -(it.get("mcap") or 0), sym)


def dedupe_listings(items):
    """Eén regel per bedrijf. Groepeert op (genormaliseerde naam, land) en op gedeelde euro-notering in hetzelfde land
    (de koerscontrole koppelde MRK, Merck & Co., aan MRK.DE, Merck KGaA: dat blijven twee bedrijven)."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, it in enumerate(items):
        keys = [("n",) + k for k in [company_key(it.get("name"), it.get("country"))] if k]
        keys += [("s", s, it.get("country") or "") for s in {it["sym"], it.get("eu")} if s]
        for k in keys:
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(items[i])
    out = []
    for g in groups.values():
        g.sort(key=listing_rank)
        # VS-bedrijf: de hoofdnotering in de VS gaat voor (koers en cijfers in dollars, analisten en short interest
        # compleet). AMZ.DE mag AMZN niet verdringen; de euro-koers komt dan via de euro-notering van AMZN.
        if any(x.get("country") == "United States" for x in g):
            g.sort(key=lambda x: 0 if x["region"] == "VS" and (x.get("us_exch") or "") in US_MAIN else 1)
        best = g[0]
        if len(g) > 1:
            best["own"] = any(x["own"] for x in g)
            best["watch"] = any(x["watch"] for x in g)
            best["alt"] = [x["sym"] for x in g[1:]]
        out.append(best)
    return out


def norm_name(n):
    n = (n or "").lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    stop = {"inc", "corp", "corporation", "co", "company", "plc", "nv", "n", "v", "se", "sa", "ag",
            "holding", "holdings", "group", "class", "a", "b", "c", "ltd", "the", "limited", "spa", "reg"}
    words = [w for w in n.split() if w not in stop]
    return " ".join(words[:2])


ETC_US = {"GLD", "SLV"}     # Amerikaanse tegenhanger van je goud/zilver-ETC's (US_LISTING_OVERRIDES in signals.py): geen aandeel


def owned_positions():
    """Eigen posities (uitsluiten) en watchlist (markeren) uit portfolio.html, tickers.txt,
    signals.json (Amerikaanse tegenhanger van euro-noteringen) en data.json (namen).
    stock_us: dollarnotering van elke eigen aandelenpositie -> de ticker die je hebt (voor het universum)."""
    own, watch, stocks, in_html = set(), set(), set(), set()
    html_path = os.path.join(ROOT, "portfolio.html")
    if os.path.exists(html_path):
        html = open(html_path, encoding="utf-8").read()
        for m in re.finditer(r"\{[^{}]*?ticker:\s*['\"]([^'\"]+)['\"][^{}]*?\}", html):
            t = m.group(1).upper()
            in_html.add(t)
            if "watch: true" in m.group(0):
                watch.add(t)
                continue
            own.add(t)
            sec = re.search(r"section:\s*(?:'([^']*)'|\"([^\"]*)\")", m.group(0))
            if sec and (sec.group(1) or sec.group(2)) == "Stocks":
                stocks.add(t)
    txt = os.path.join(ROOT, "tickers.txt")
    if os.path.exists(txt):
        for line in open(txt, encoding="utf-8-sig"):
            s = line.split("#")[0].strip().upper()
            if s and s not in watch:
                own.add(s)
                if s not in in_html and "." not in s:
                    stocks.add(s)                  # geen sectie bekend: een dollarticker telt als aandeel (ETC's vallen hieronder af)
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
    stock_us = {}
    for s in sorted(stocks):
        us = s if "." not in s else us_of.get(s)
        if us and us not in ETC_US and re.fullmatch(r"[A-Z][A-Z0-9-]{0,6}", us):
            stock_us.setdefault(us, s)
    return {"own": own, "watch": watch, "names_own": names_own, "names_watch": names_watch, "stock_us": stock_us}


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
    if not c.get("skip") and c.get("v", 1) < CACHE_V:
        return True                               # oudere cache: eenmalig opnieuw ophalen voor de nieuwe cijfers
    if region == "VS" and not (c.get("eu") or {}).get("sym") \
            and age_h((c.get("eu") or {}).get("checked")) > LISTING_RETRY_DAYS * 24:
        return True
    return False


def five_year(y, sym):
    """Koersrendement over (maximaal) 5 jaar, maandreeks. 'yrs' = hoe lang de reeks echt is."""
    pts, _ = chart(y, sym, "5y", "1mo")
    if len(pts) < 12:
        return None
    return {"r": round(pts[-1][1] / pts[0][1] - 1, 4), "yrs": round((pts[-1][0] - pts[0][0]) / 31557600, 2)}


def sp500_5y(y):
    """S&P 500 over 5 jaar: in dollars (VS-bedrijven) en omgerekend naar euro (eurozone-bedrijven)."""
    try:
        pts, _ = chart(y, "^GSPC", "5y", "1mo")
        r = pts[-1][1] / pts[0][1] - 1
        time.sleep(SLEEP)
        fpts, _ = chart(y, "EURUSD=X", "5y", "1mo")
        fxr = fpts[-1][1] / fpts[0][1] - 1
        return {"VS": r, "EU": (1 + r) / (1 + fxr) - 1, "lvl": pts[-1][1]}
    except Exception as e:  # noqa: BLE001
        print(f"  S&P 500 (5 jaar) niet opgehaald: {e}")
        return None


def fetch_stock(y, sym, meta, old, fx):
    c = {"src": meta["src"], "region": meta["region"]}
    s = summary(y, sym)
    time.sleep(SLEEP)
    want = "USD" if meta["region"] == "VS" else "EUR"
    # ook bij overslaan het land bewaren: merge_dyn kiest daarmee de juiste notering (TSM of TSFA.VI)
    if s.get("cur") and s["cur"] != want:
        c.update({"f": iso(), "skip": f"valuta {s['cur']}", "name": s.get("name"), "country": s.get("country")})
        return c
    if s.get("fcur") and s["fcur"] not in OK_FIN_CUR:
        c.update({"f": iso(), "skip": f"rapporteert in {s['fcur']}", "name": s.get("name"), "country": s.get("country")})
        return c
    c.update(s)
    c["ts"] = timeseries(y, sym)
    time.sleep(SLEEP)
    try:
        c["r5"] = five_year(y, sym)
    except Exception:  # noqa: BLE001
        c["r5"] = (old or {}).get("r5")
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


# ── Scorecard 125: 13 criteria van 0-10, omgerekend naar 125 punten ──────
# 100-125 = once-in-a-lifetime · 75-100 = watchlist / investeerbaar · onder 75 = afvallen.
# Staat naast Score 2.0 en verandert de 85+ selectie niet.
CARD_MAX = 125
CARD_MIN = 10               # minstens zoveel van de 13 criteria moeten data hebben
# Secular trend (schatting): branches die meeliften op een langetermijntrend scoren hoog
TREND_INDUSTRY = {
    "Semiconductors": 9, "Semiconductor Equipment & Materials": 9, "Software - Infrastructure": 9,
    "Software - Application": 8, "Solar": 8, "Utilities - Renewable": 8, "Electrical Equipment & Parts": 8,
    "Aerospace & Defense": 8, "Uranium": 8, "Computer Hardware": 7, "Electronic Components": 7,
    "Internet Content & Information": 7, "Internet Retail": 7, "Biotechnology": 7, "Medical Devices": 7,
    "Health Information Services": 7, "Financial Data & Stock Exchanges": 7, "Engineering & Construction": 7,
    "Information Technology Services": 6, "Communication Equipment": 6, "Scientific & Technical Instruments": 6,
    "Diagnostics & Research": 6, "Drug Manufacturers - General": 6, "Medical Instruments & Supplies": 6,
    "Credit Services": 6, "Specialty Industrial Machinery": 6, "Waste Management": 6,
    "Security & Protection Services": 6, "Utilities - Regulated Electric": 6, "REIT - Specialty": 6,
    "Banks - Diversified": 4, "Banks - Regional": 4, "Insurance - Life": 4, "Packaged Foods": 3,
    "Beverages - Non-Alcoholic": 4, "Confectioners": 4, "Beverages - Brewers": 3, "Beverages - Wineries & Distilleries": 3,
    "Auto Manufacturers": 3, "Auto Parts": 3, "Steel": 3, "Telecom Services": 3, "Chemicals": 4,
    "Oil & Gas Integrated": 2, "Oil & Gas E&P": 2, "Oil & Gas Refining & Marketing": 2, "Oil & Gas Midstream": 3,
    "Department Stores": 2, "Publishing": 2, "Broadcasting": 2, "REIT - Office": 2, "Tobacco": 1, "Thermal Coal": 1,
}
NO_CASHDEBT = re.compile(r"^(Banks|Insurance)")   # bij banken en verzekeraars zegt cash vs schuld weinig


def pts3(v, good, ok, high_is_good=True):
    """0/5/10: voorbij 'good' = 10, voorbij 'ok' = 5, anders 0."""
    if v is None:
        return None
    if high_is_good:
        return 10 if v >= good else 5 if v >= ok else 0
    return 10 if v <= good else 5 if v <= ok else 0


def pricing_power(gm_rows):
    """Brutomarge over 3-5 jaar: stabiel of stijgend = prijzen kunnen doorberekenen."""
    if len(gm_rows) < 3:
        return None, None
    delta = (gm_rows[-1] - gm_rows[0]) * 100            # procentpunt
    if delta >= 1:
        p = 10
    elif delta >= -1:
        p = 8 if gm_rows[-1] >= 0.4 else 6
    elif delta >= -3:
        p = 4
    else:
        p = 1
    return p, round(delta, 1)


def pts_q(v):
    """Kwalitatief oordeel 0-10 naar de 0/5/10 van de scorecard: sterk (8+) = 10, enigszins (4-7) = 5, geen = 0."""
    if v is None:
        return None
    return 10 if v >= 8 else 5 if v >= 4 else 0


def scorecard(it, sp5):
    """Scorecard volgens 'How to Find the Best Stocks' (stap 6): 13 categorieën, elk 0, 5 of 10 punten,
    op de laatste 12 maanden. 100-125 uitzonderlijk · 75-100 watchlist/investeerbaar · onder 75 absoluut niet.
    Ontbreekt een cijfer (bv. short interest in Europa), dan telt die categorie neutraal (5) en staat 'geen data'."""
    d = it["_card"]
    qual, m, mp = it["q"], it["m"], it["mp"]
    crit = []

    # 1. Cash vs. schuld (laatste kwartaal): meer schuld dan cash 0 · meer cash dan schuld 5 · 2x+ cash 10
    cash, debt = d["cash"], d["debt"]
    if cash is None or debt is None:
        p1 = None
    elif debt <= 0 or cash >= 2 * debt:
        p1 = 10
    elif cash > debt:
        p1 = 5
    else:
        p1 = 0
    crit.append({"k": "cashdebt", "v": rnd(cash / debt, 2) if cash is not None and debt else None, "p": p1})

    # 2. Omzetgroei laatste 12 mnd: < 10% 0 · 10-20% 5 · > 20% 10
    g = d["g12"]
    crit.append({"k": "omzet", "v": rnd(g, 1), "p": None if g is None else 10 if g > 20 else 5 if g >= 10 else 0})
    # 3. Operationele marge laatste 12 mnd: < 10% 0 · 10-25% 5 · > 25% 10
    om = d["om12"]
    crit.append({"k": "marge", "v": rnd(om, 1), "p": None if om is None else 10 if om > 25 else 5 if om >= 10 else 0})
    # 4. Short interest: > 10% 0 · 5-10% 5 · < 5% 10
    si = d["short"]
    crit.append({"k": "short", "v": rnd(si, 1), "p": None if si is None else 10 if si < 5 else 5 if si <= 10 else 0})
    # 5. Institutioneel bezit: < 30% 0 · 30-60% 5 · > 60% 10
    inst = d["inst"]
    crit.append({"k": "inst", "v": rnd(inst, 0), "p": None if inst is None else 10 if inst > 60 else 5 if inst >= 30 else 0})
    # 6. Omzet groeit harder dan de kosten: nee 0 · ja 10
    ry, oy = d["rev_yoy"], d["opex_yoy"]
    crit.append({"k": "schaal", "v": rnd(ry - oy, 1) if ry is not None and oy is not None else None,
                 "p": None if ry is None or oy is None else 10 if ry > oy else 0})

    # 7. 5 jaar koers t.o.v. de S&P 500 (zelfde valuta): slechter 0 · 10-20 procentpunt beter 5 · 20+ beter 10
    r5, p7, v7 = d["r5"], None, None
    sp = (sp5 or {}).get("VS" if it["region"] == "VS" else "EU")
    if r5 and sp is not None and r5.get("yrs", 0) >= 4.5:
        v7 = (r5["r"] - sp) * 100
        p7 = 10 if v7 >= 20 else 5 if v7 >= 10 else 0
    crit.append({"k": "sp5", "v": rnd(v7, 0), "p": p7, "r": rnd(r5["r"] * 100, 0) if r5 else None,
                 "sp": rnd(sp * 100, 0) if sp is not None else None})

    # 8-13. Kwalitatief (zo streng mogelijk): oordeel uit kwaliteit.json, anders een schatting uit de cijfers
    cur = d["curated"]
    pp, gm_delta = pricing_power(d["gm"])
    rec = qual["recessie"]["s"]
    rec_p = round(0.7 * rec + 0.3 * pp) if pp is not None else rec

    trend = TREND_INDUSTRY.get(it.get("industry") or "", 5)
    fwd = m.get("fwd_g")
    if fwd is not None:
        trend += 1 if fwd >= 20 else -1 if fwd <= 0 else 0
    if (d["rev_cagr"] or 0) >= 15:
        trend += 1
    cult = 5 + (2 if d["founder"] else 0)
    ins = d["ins"]
    if ins is not None:
        cult += 2 if ins >= .10 else 1 if ins >= .03 else 0
    if (d["rev_cagr"] or 0) >= 10:
        cult += 1
    tal_src = [x for x in (mp.get("rpe"), mp.get("roic")) if x is not None]
    talent = round(sum(tal_src) / len(tal_src) / 10) if tal_src else None
    est = {"trend": trend, "cultuur": cult, "talent": talent}

    def q(key):
        if isinstance(cur.get(key), (int, float)):
            return {"s": max(0, min(10, cur[key])), "src": "oordeel"}
        v = est[key]
        return None if v is None else {"s": max(0, min(10, v)), "src": "schatting"}

    quali = {"moat": qual["moat"], "ceo": qual["ceo"],
             "recessie": {"s": rec_p, "src": qual["recessie"]["src"], "rec": rec, "pricing": pp, "gm_delta": gm_delta},
             "trend": q("trend"), "cultuur": q("cultuur"), "talent": q("talent")}
    for k, o in quali.items():
        crit.append(dict({"k": k, "p": pts_q(o["s"]) if o else None, "raw": o["s"] if o else None},
                         **({kk: vv for kk, vv in (o or {}).items() if kk != "s"})))

    missing = [c["k"] for c in crit if c["p"] is None]
    if len(crit) - len(missing) < CARD_MIN:
        it["card"] = {"n": len(crit) - len(missing), "crit": crit}
        return
    for c in crit:                                       # geen data = neutraal (5), wel zichtbaar
        if c["p"] is None:
            c["p"], c["nodata"] = 5, True
    total = min(CARD_MAX, sum(c["p"] for c in crit))
    it["card"] = {"s": total, "n": len(crit) - len(missing), "nodata": missing, "dil": rnd(m.get("dilution"), 1),
                  "lvl": "top" if total >= 100 else "watch" if total >= 75 else "low", "crit": crit}


# ── De 12 stappen ('How to Find the Best Stocks', Tom Nash) ─────────────────
# Survivor: zakt een bedrijf voor één stap, dan valt het af. Wat niet uit de cijfers te halen is,
# staat als 'zelf beoordelen' bij de stap (status 'pass' zegt dan: de cijfers zijn in orde).
SKIP_INDUSTRY = re.compile(r"^(Oil & Gas|Coal|Thermal Coal|Coking Coal|Gold|Silver|Copper|Aluminum|Steel|Other Industrial Metals|"
                           r"Other Precious Metals|Agricultural Inputs|Farm Products|Lumber|Uranium|Airlines)", re.I)
SKIP_COUNTRY = {"China", "Hong Kong", "Macau"}
SOFTWARE = re.compile(r"^Software", re.I)       # Rule of 40 is hard voor software/abonnementen
DCF_R = {"bull": 0.08, "base": 0.10, "bear": 0.12}
DCF_TG = 0.04                     # eindgroei: 4%, 'een regel die je niet breekt'


def _series(ts, key):
    return [v for _, v in (ts.get(key) or [])]


def _yoy(vals):
    return [(b / a - 1) * 100 for a, b in zip(vals, vals[1:]) if a and a > 0]


def dcf_value(rev0, g, margin, r, cash, debt, shares):
    """10-20 minuten-DCF: 5 jaar vrije kasstroom, eindwaarde met 4% groei, plus cash min schuld, per aandeel."""
    if not rev0 or not shares or margin is None or r <= DCF_TG:
        return None
    pv, rev = 0.0, rev0
    for t in range(1, 6):
        rev *= 1 + g
        pv += rev * margin / (1 + r) ** t
    tv = rev * margin * (1 + DCF_TG) / (r - DCF_TG) / (1 + r) ** 5
    return (pv + tv + (cash or 0) - (debt or 0)) / shares


def implied_growth(price, rev0, margin, cash, debt, shares):
    """Omgekeerde DCF: welke jaarlijkse omzetgroei (5 jaar) zit er in de koers van vandaag?"""
    if not price or not rev0 or not shares or not margin or margin <= 0:
        return None
    lo, hi = -0.5, 1.5
    if dcf_value(rev0, hi, margin, 0.10, cash, debt, shares) < price:
        return hi
    if dcf_value(rev0, lo, margin, 0.10, cash, debt, shares) > price:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if dcf_value(rev0, mid, margin, 0.10, cash, debt, shares) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def twelve_steps(it, med):
    """Alle 12 stappen per bedrijf. med = sectormedianen voor de juiste multiple (stap 12)."""
    d, card = it["_card"], it.get("card") or {}
    ts, c = d["ts"], d["c"]
    pts = {x["k"]: x["p"] for x in card.get("crit", [])}
    steps = []

    # De vaste zelf-te-beoordelen punten per stap staan in steps.js (Steps.MANUAL); hier alleen de extra's per bedrijf
    def step(n, status, checks, manual=None, note=None, **extra):
        steps.append(dict({"n": n, "s": status, "c": checks}, **({"m": manual} if manual else {}), **({"note": note} if note else {}), **extra))

    def chk(text, ok, val=None):
        return [text, ok, val]

    rev = _series(ts, "annualTotalRevenue") or _series(ts, "annualOperatingRevenue")
    oi = _series(ts, "annualOperatingIncome")
    fcf = _series(ts, "annualFreeCashFlow")
    ni = _series(ts, "annualNetIncome")
    rev_g = _yoy(rev)
    om_hist = [o / r * 100 for o, r in zip(oi[-len(rev):], rev[-len(oi):]) if r] if oi and rev else []
    g12, om12 = d["g12"], d["om12"]
    cash, debt = d["cash"], d["debt"]
    mcap_usd = d["mcap_usd"]
    age = d["age"]

    # 1. De seculiere trend en de zones die je overslaat
    skip = []
    if mcap_usd is not None and mcap_usd < 2e9:
        skip.append("small cap")
    if (c.get("country") or "") in SKIP_COUNTRY:
        skip.append("Chinees bedrijf")
    if SKIP_INDUSTRY.search(it.get("industry") or ""):
        skip.append("grondstoffen" if "Airlines" not in (it.get("industry") or "") else "luchtvaart")
    c1 = [chk("Echte vraag en betalende klanten", bool(rev[-1] > 0 and (rev_g[-1] if rev_g else 0) > -5) if rev else None),
          chk("Duurzame groei van omzet en winst", None if d["rev_cagr"] is None else (d["rev_cagr"] > 0 and bool(ni) and ni[-1] > 0),
              f"omzet {d['rev_cagr']:+.0f}% per jaar" if d["rev_cagr"] is not None else None),
          chk("Een moat, geen grondstof", None if pts.get("moat") is None else pts["moat"] >= 5),
          chk("Lift mee op een trend", None if pts.get("trend") is None else pts["trend"] >= 5)]
    bad1 = skip or any(x[1] is False for x in c1)
    step(1, "fail" if bad1 else "pass", c1,
         note=("Overslaan: " + ", ".join(skip)) if skip else None)

    # 2. Sanity check (No B***t Rule): minstens 2 van 3
    grew = None if len(rev_g) < 2 else (sum(1 for x in rev_g[-3:] if x > 0) >= max(2, len(rev_g[-3:]) - 1) and rev_g[-1] > 0)
    prof = None if not ni and c.get("eps") is None else ((ni[-1] > 0) if ni else c["eps"] > 0)
    more_cash = None if cash is None or debt is None else cash > debt
    c2 = [chk("Omzet groeide de afgelopen jaren", grew), chk("Winstgevend", prof), chk("Meer cash dan schuld", more_cash)]
    okn = sum(1 for x in c2 if x[1])
    known = sum(1 for x in c2 if x[1] is not None)
    st2 = "pass" if okn >= 2 else "fail" if known - okn >= 2 else "check"
    step(2, st2, c2, ["Eén gemist? Alleen goed bij infrastructuur die schuld nodig heeft of een duidelijk pad naar winst"] if okn == 2 else [])

    # 3. Pre-screen: alle zes, twee 30-secondentests en de directe afvallers
    qf = _series(ts, "quarterlyFreeCashFlow")
    qc = dict(map(tuple, ts.get("quarterlyCashCashEquivalentsAndShortTermInvestments") or ts.get("quarterlyCashAndCashEquivalents") or []))
    qd = dict(map(tuple, ts.get("quarterlyTotalDebt") or []))
    qdates = sorted(set(qc) & set(qd))
    fcf_pos = (all(x > 0 for x in qf[-6:]) if len(qf) >= 4 else (all(x > 0 for x in fcf[-2:]) if len(fcf) >= 2 else None))
    net_cash = (all(qc[q] > qd[q] for q in qdates[-6:]) if len(qdates) >= 4 else more_cash)
    fcf_margin = (fcf[-1] / rev[-1] * 100) if fcf and rev and rev[-1] else None
    r40 = g12 + fcf_margin if g12 is not None and fcf_margin is not None else None
    soft = bool(SOFTWARE.search(it.get("industry") or ""))
    scal = None if d["rev_yoy"] is None or d["opex_yoy"] is None else d["rev_yoy"] > d["opex_yoy"]
    c3 = [chk("Positieve vrije kasstroom (4-6 kwartalen)", fcf_pos),
          chk("Netto cash positief (4-6 kwartalen)", net_cash),
          chk("Omzetgroei 10% of meer", None if g12 is None else g12 >= 10, f"{g12:.0f}%" if g12 is not None else None),
          chk("Een echte moat", None if pts.get("moat") is None else pts["moat"] >= 5),
          chk("CEO en team met trackrecord", None if pts.get("ceo") is None else pts["ceo"] >= 5),
          chk(f"Rule of 40 (groei + FCF-marge){' · software' if soft else ''}", None if r40 is None else (r40 >= 40 if soft else None if r40 < 40 else True),
              f"{r40:.0f}" if r40 is not None else None),
          chk("Schaalbaar: omzet groeit harder dan kosten", scal)]
    misses = None
    if d["eps_hist"]:
        h = [x for x in d["eps_hist"] if x and x[0] is not None and x[1] is not None]
        misses = sum(1 for a, e in h if a < e) if len(h) >= 3 else None
    falling = len(rev_g) >= 2 and rev_g[-1] < rev_g[-2]
    kills = [k for k, hit in [
        ("beurswaarde onder $1 mld", mcap_usd is not None and mcap_usd < 1e9),
        ("meer schuld dan cash", cash is not None and debt is not None and debt > cash),
        ("omzetgroei onder 10% en dalend", g12 is not None and g12 < 10 and falling),
        ("short interest boven 10%", d["short"] is not None and d["short"] > 10),
        ("operationele marge onder 20%", om12 is not None and om12 < 20),
        ("2+ keer winstverwachting gemist (4 kwartalen)", misses is not None and misses >= 2),
    ] if hit]
    bad3 = kills or any(x[1] is False for x in c3)
    young = age is not None and age < 10
    step(3, "fail" if bad3 else "pass", c3,
         [f"Pas {age:.0f} jaar op de beurs: is het bedrijf zelf minstens 10 jaar oud (oprichting)?"] if young else None,
         note=("Directe afvaller: " + ", ".join(kills)) if kills else None, misses=misses)

    # 4. Definieer het bedrijf (zelf invullen)
    step(4, "manual", [], about=c.get("about"))

    # 5. Marktpotentieel (zelf checken)
    step(5, "manual", [chk("Omzet nu", None, f"{(rev[-1] if rev else 0) / 1e9:.1f} mld" if rev else None)])

    # 6. De scorecard
    s6 = card.get("s")
    step(6, "pass" if s6 is not None and s6 >= 75 else "fail" if s6 is not None else "check",
         [chk("Scorecard 75 of hoger (100+ = uitzonderlijk)", None if s6 is None else s6 >= 75, f"{s6}/125" if s6 is not None else None)])

    # 7. Omzetmotor en 5 jaar geschiedenis
    cash_y = _series(ts, "annualCashCashEquivalentsAndShortTermInvestments") or _series(ts, "annualCashAndCashEquivalents")
    debt_y = _series(ts, "annualTotalDebt")
    n_cd = min(len(cash_y), len(debt_y))
    c7 = [chk("Omzet groeit elk jaar", None if len(rev_g) < 2 else all(x > 0 for x in rev_g), f"{len(rev)} jaar"),
          chk("Operationele marge stijgt", None if len(om_hist) < 3 else om_hist[-1] > om_hist[0],
              f"{om_hist[0]:.0f}% → {om_hist[-1]:.0f}%" if len(om_hist) >= 2 else None),
          chk("Vrije kasstroom positief en stijgend", None if len(fcf) < 3 else (all(x > 0 for x in fcf) and fcf[-1] > fcf[0])),
          chk("Elk jaar meer cash dan schuld", None if n_cd < 3 else all(a > b for a, b in zip(cash_y[-n_cd:], debt_y[-n_cd:])))]
    f7 = sum(1 for x in c7 if x[1] is False)
    step(7, "fail" if f7 >= 2 else "check" if f7 == 1 or sum(1 for x in c7 if x[1] is None) >= 2 else "pass", c7)

    # 8. Loan Officer Test
    last = lambda k: (_series(ts, k) or [None])[-1]  # noqa: E731
    intexp = d["intexp"]
    cov = (oi[-1] / intexp) if oi and intexp else (None if not oi else 99)
    ta, gw, tl, eq = last("annualTotalAssets"), last("annualGoodwill") or 0, last("annualTotalLiabilitiesNetMinorityInterest"), last("annualStockholdersEquity")
    ca, cl = last("annualCurrentAssets"), last("annualCurrentLiabilities")
    a2l = (ta - gw) / tl if ta and tl else None
    cr = ca / cl if ca and cl else None
    de = (tl / eq) if tl and eq and eq > 0 else (99 if tl and eq is not None and eq <= 0 else None)
    c8 = [chk("Rentedekking 4x of meer", None if cov is None else cov >= 4, "geen rente" if cov == 99 else f"{cov:.1f}x" if cov is not None else None),
          chk("Bezittingen (zonder goodwill) / schulden 2:1+", None if a2l is None else a2l >= 2, f"{a2l:.1f}:1" if a2l is not None else None),
          chk("Current ratio 1,0+", None if cr is None else cr >= 1.0, f"{cr:.2f}" if cr is not None else None),
          chk("Schuld / eigen vermogen onder 4", None if de is None else de < 4, "negatief eigen vermogen" if de == 99 else f"{de:.1f}" if de is not None else None)]
    f8 = sum(1 for x in c8 if x[1] is False)
    step(8, "fail" if f8 >= 2 or (de is not None and de >= 4) else "check" if f8 == 1 else "pass", c8,
         note="Vergelijk met de sector: een retailer lijkt altijd minder liquide door zijn voorraad." if cr is not None and cr < 1 else None)

    # 9. Concurrentievoordeel (moat)
    mt = pts.get("moat")
    c9 = [chk("Moat (oordeel)", None if mt is None else mt >= 10 if mt != 5 else None, {10: "sterk", 5: "zwak", 0: "geen"}.get(mt)),
          chk("Marge bewijst het (operationele marge 25%+)", None if om12 is None else om12 >= 25, f"{om12:.0f}%" if om12 is not None else None)]
    step(9, "fail" if mt == 0 else "pass" if mt == 10 and (om12 or 0) >= 20 else "check", c9,
         types=d["curated"].get("moat_types"))

    # 10. Management
    ceo = pts.get("ceo")
    insider = None
    if d["ins_buy"] is not None or d["ins_sell"] is not None:
        insider = (d["ins_buy"] or 0) > (d["ins_sell"] or 0)
    ta_hist = _series(ts, "annualTotalAssets")
    gw_hist = _series(ts, "annualGoodwill")
    spree = len(gw_hist) >= 3 and gw_hist[-3] > 0 and gw_hist[-1] / gw_hist[-3] >= 1.5 and ta_hist and gw_hist[-1] >= 0.2 * ta_hist[-1]
    c10 = [chk("CEO (oordeel): superster", None if ceo is None else ceo >= 10 if ceo != 5 else None, {10: "superster", 5: "redelijk", 0: "clown"}.get(ceo)),
           chk("Oprichter aan het roer", True if d["founder"] else None),
           chk("Insiders kopen zelf aandelen", insider),
           chk("Verslaat de winstverwachting consequent", None if misses is None else misses <= 1, f"{4 - misses if misses is not None else '?'} van 4" if misses is not None else None),
           chk("Geen co-CEO-structuur", False if d["co_ceo"] else True),
           chk("Geen overnamerace", False if spree else True)]
    red10 = d["co_ceo"] or spree or ceo == 0
    step(10, "fail" if red10 else "pass" if ceo == 10 and (misses is None or misses <= 1) else "check", c10)

    # 11. Vernietig iets moois: rode vlaggen en de hype-val
    inv = _series(ts, "annualInventory")
    inv_pile = len(inv) >= 2 and len(rev) >= 2 and inv[-2] > 0 and rev[-2] > 0 and \
        (inv[-1] / inv[-2] - 1) * 100 > (rev[-1] / rev[-2] - 1) * 100 + 20
    margin_down = len(om_hist) >= 3 and om_hist[-1] < om_hist[-2] < om_hist[-3]
    chg = d["chg12"]
    ins_dump = (d["ins_sell"] or 0) > 2 * max(1, d["ins_buy"] or 0) and chg is not None and chg > 30
    dil = d["dilution"]
    hype = [rev and rev[-1] <= 0, (card.get("s") or 0) < 60, dil is not None and dil > 3]
    flags = [k for k, hit in [("dalende marges (2 jaar)", margin_down), ("voorraad stijgt veel harder dan omzet", inv_pile),
                              ("insiders verkopen flink terwijl de koers loopt", ins_dump), ("overnamerace / overexpansie", spree),
                              ("hype-val: geen omzet, zwakke cijfers en steeds nieuwe aandelen", all(hype))] if hit]
    c11 = [chk("Marges dalen niet", not margin_down if len(om_hist) >= 3 else None),
           chk("Voorraad loopt niet op t.o.v. omzet", not inv_pile if len(inv) >= 2 else None),
           chk("Geen zware insider-verkopen tijdens een koersrally", not ins_dump),
           chk("Geen verwatering boven 3% per jaar", None if dil is None else dil <= 3, f"{dil:+.1f}%" if dil is not None else None)]
    step(11, "fail" if flags else "pass", c11,
         note=("Rode vlag: " + ", ".join(flags)) if flags else None)

    # 12. Waardering: DCF (bull 8% · basis 10% · bear 12%), omgekeerde DCF, juiste multiple en PEG
    price, shares = d["price"], d["shares"]
    rev3, fcf3 = rev[-3:], fcf[-3:]
    margins = [f / r for f, r in zip(fcf[-3:], rev[-3:]) if r] if len(fcf3) == len(rev3) else []
    margin = sum(margins) / len(margins) if margins else None
    cagr = ((rev3[-1] / rev3[0]) ** (1 / (len(rev3) - 1)) - 1) if len(rev3) >= 2 and rev3[0] > 0 and rev3[-1] > 0 else None
    g_used = max(-0.05, min(0.25, cagr * 0.8)) if cagr is not None else None      # groei inperken: conservatief blijven
    rev0 = d["rev_ttm"] or (rev[-1] if rev else None)
    vals = {k: dcf_value(rev0, g_used, margin, r, cash, debt, shares) if g_used is not None and margin and margin > 0 else None
            for k, r in DCF_R.items()}
    impl = implied_growth(price, rev0, margin, cash, debt, shares)
    pe, peg = d["fpe"], d["peg"]
    ps = (d["mcap_n"] / rev0) if d["mcap_n"] and rev0 else None
    unprofitable = (oi and oi[-1] <= 0) or (c.get("eps") is not None and c["eps"] <= 0)
    if soft:
        mult = ("Rule of 40", f"{r40:.0f}" if r40 is not None else "—", "40+ is goed")
    elif unprofitable:
        mult = ("Koers/omzet", f"{ps:.1f}x" if ps else "—", f"sector {med.get(('ps', it.get('sector')), 0):.1f}x" if med.get(("ps", it.get("sector"))) else "")
    else:
        mult = ("Verwachte K/W", f"{pe:.1f}" if pe and pe < 9999 else "—", f"sector {med.get(('pe', it.get('sector')), 0):.1f}" if med.get(("pe", it.get("sector"))) else "")
    peg_lbl = None if not peg else "ondergewaardeerd" if peg < 1 else "redelijk" if peg <= 2 else "erg duur"
    c12 = [chk("Bear case (12%) boven de koers", None if vals["bear"] is None or not price else vals["bear"] >= price,
               f"{vals['bear']:.2f}" if vals["bear"] is not None else None),
           chk("Basis (10%) boven de koers", None if vals["base"] is None or not price else vals["base"] >= price,
               f"{vals['base']:.2f}" if vals["base"] is not None else None),
           chk("PEG onder 2", None if not peg else peg <= 2, f"{peg:.1f} · {peg_lbl}" if peg else None)]
    if vals["base"] is None:
        st12 = "check"
    elif price and vals["bear"] >= price:
        st12 = "pass"
    elif price and vals["base"] >= price:
        st12 = "check"
    else:
        st12 = "fail"
    step(12, st12, c12,
         dcf={k: rnd(v, 2) for k, v in vals.items()}, price=rnd(price, 2), g=rnd(g_used * 100 if g_used is not None else None, 1),
         margin=rnd(margin * 100 if margin is not None else None, 1), implied=rnd(impl * 100 if impl is not None else None, 1),
         actual=rnd(cagr * 100 if cagr is not None else None, 1), mult=mult)

    fails = [s_["n"] for s_ in steps if s_["s"] == "fail"]
    it["s12"] = {"list": steps, "fail": fails[0] if fails else None, "nfail": len(fails),
                 "survivor": not fails, "auto_ok": sum(1 for s_ in steps if s_["s"] == "pass")}


def valuation_check(it):
    """Waarderingscheck: een topbedrijf kan te duur zijn. Telt niet mee in de punten."""
    v, m = (it.get("pillars") or {}).get("V"), it["m"]
    fpe, peg = m.get("fpe"), m.get("peg")
    if v is None:
        return None
    extreme = (fpe is not None and 50 < fpe < 9999) or (peg is not None and peg > 3)
    lvl = "zeer duur" if v < 20 and extreme else "duur" if v < 25 else "gunstig" if v >= 75 else "normaal"
    return {"lvl": lvl, "V": rnd(v, 0), "fpe": None if fpe is None or fpe >= 9999 else rnd(fpe, 1),
            "peg": rnd(peg, 1), "loss": fpe is not None and fpe >= 9999}


# ── Koopmoment: een sterk bedrijf (wat) dat tijdelijk in de aanbieding is (wanneer) ──
KOOP_MIN_CARD = 90          # vanaf deze scorecard volgen we koopmomenten (de pagina toont standaard 100+)
KOOP_DD = (10, 25)          # zo ver (%) onder de 52-weekse top = in de aanbieding
KOOP_NEAR = 7               # vanaf zoveel % onder de top: 'bijna'
KOOP_SMA = 0.92             # koers minstens 92% van het 200-daags gemiddelde: langetermijntrend intact
KOOP_REV_MIN = -25          # bijstellingen analisten (saldo, %) niet slechter dan dit
KOOP_EARN_DAYS = 7          # geen kwartaalcijfers binnen zoveel dagen
KOOP_NEW_DAYS = 60          # zelfde aandeel pas na zoveel dagen opnieuw als nieuw koopmoment vastleggen
HORIZONS = [("1m", 30), ("3m", 91), ("6m", 182), ("12m", 365)]


HARD_STEPS = (1, 2, 3, 8, 11)   # stappen met harde afvallers in de gids: zakt een bedrijf hier, dan nooit een koopmoment


def koopmoment(it):
    """Status 'zone' (alle voorwaarden), 'bijna' (net niet diep genoeg) of None, met instapzone en risico."""
    s12 = it.get("s12") or {}
    if any(x["s"] == "fail" for x in s12.get("list", []) if x["n"] in HARD_STEPS):
        return None
    card = (it.get("card") or {}).get("s")
    price, hi, sma = it.get("price"), it.get("hi52"), it.get("sma200")
    if card is None or card < KOOP_MIN_CARD or not price or not hi:
        return None
    dd = (1 - price / hi) * 100
    val = (it.get("val") or {}).get("lvl")
    rev = (it.get("m") or {}).get("revisions")
    ed = it.get("earn_days")
    checks = [
        ("scorecard", True, f"scorecard {round(card)}"),
        ("waardering", val not in ("duur", "zeer duur"), f"waardering {val or 'onbekend'}"),
        ("korting", KOOP_DD[0] <= dd <= KOOP_DD[1], f"{dd:.0f}% onder de top"),
        ("trend", sma is None or price >= KOOP_SMA * sma,
         "boven 200-daags gem." if sma and price >= sma else f"{(1 - price / sma) * 100:.0f}% onder 200-daags gem." if sma else "trend onbekend"),
        ("analisten", rev is None or rev >= KOOP_REV_MIN, "analisten stellen bij omlaag" if rev is not None and rev < KOOP_REV_MIN else "analisten niet negatief"),
        ("cijfers", ed is None or ed > KOOP_EARN_DAYS, f"cijfers over {ed} d" if ed is not None and ed <= KOOP_EARN_DAYS else "geen cijfers deze week"),
    ]
    blockers = [txt for k, ok, txt in checks if not ok and k != "korting"]
    if blockers:
        return None
    if KOOP_DD[0] <= dd <= KOOP_DD[1]:
        status = "zone"
    elif KOOP_NEAR <= dd < KOOP_DD[0]:
        status = "bijna"
    else:
        return None
    # daaronder klopt het verhaal niet meer: 30% onder de top of 10% onder het 200-daags gemiddelde,
    # de hoogste van de twee die nog duidelijk onder de koers ligt
    opts = [x for x in (hi * 0.70, sma * 0.90 if sma else None) if x and x < price * 0.97]
    if not opts:
        return None
    stop = max(opts)
    return {"status": status, "dd": rnd(dd, 1), "zone": [rnd(hi * (1 - KOOP_DD[1] / 100), 2), rnd(hi * (1 - KOOP_DD[0] / 100), 2)],
            "stop": rnd(stop, 2), "risk": rnd((1 - stop / price) * 100, 1), "why": [txt for key, ok, txt in checks if ok and key != "scorecard"]}


def s12_brief(s12):
    """De 12 stappen in het kort (status per stap), genoeg voor de vakjes op Koopmoment."""
    if not s12:
        return None
    return {"list": [{"n": x["n"], "s": x["s"], **({"note": x["note"]} if x.get("note") else {})} for x in s12["list"]],
            "fail": s12.get("fail"), "nfail": s12.get("nfail"), "survivor": s12.get("survivor"), "auto_ok": s12.get("auto_ok")}


def koop_rows(items):
    """Compacte regels voor koopmoment.json (ook eigen posities: 'bijkopen')."""
    rows = []
    for it in items:
        k = it.get("koop")
        if not k:
            continue
        rows.append({"sym": it["sym"], "eu": it.get("eu"), "name": it["name"], "sector": it.get("sector"),
                     "industry": it.get("industry"), "region": it["region"], "exch": it.get("exch"), "us_exch": it.get("us_exch"),
                     "price": it["price"], "native": it.get("native"), "approx": it.get("approx"),
                     "hi52": it.get("hi52"), "sma200": it.get("sma200"),
                     "card": it["card"]["s"], "lvl": it["card"]["lvl"], "val": (it.get("val") or {}).get("lvl"),
                     "score": it.get("score"), "exp": (it.get("exp") or {}).get("pct"), "earn_days": it.get("earn_days"),
                     "own": it.get("own"), "watch": it.get("watch"), "s12": s12_brief(it.get("s12")), **k})
    rows.sort(key=lambda r: (r["status"] != "zone", -r["card"]))
    return rows


def _rel(entry_px, now_px, sp0, sp1):
    if not entry_px or not now_px or not sp0 or not sp1:
        return None, None
    return (now_px / entry_px - 1) * 100, (sp1 / sp0 - 1) * 100


def update_koop(prev, rows, items, prices, cache, sp, fx):
    """Koopmomenten en scorecard-groepen vastleggen en volgen t.o.v. de S&P 500 (zelfde valuta)."""
    today = now().date()
    sp_lvl = {"VS": sp["lvl"], "EU": sp["lvl"] / fx} if sp and sp.get("lvl") and fx else None
    sp_of = lambda region: (sp_lvl or {}).get("VS" if region == "VS" else "EU")  # noqa: E731

    def px_now(sym):
        return (prices.get(sym) or {}).get("price") or (cache.get(sym) or {}).get("price")

    def follow(e, start_px, region):
        """Rendement nu en vastgezet op 1/3/6/12 maanden."""
        days = (today - datetime.fromisoformat(e["date"]).date()).days
        r, b = _rel(start_px, px_now(e["sym"]) if "sym" in e else None, e.get("sp0"), sp_of(region))
        e["days"] = days
        if r is not None:
            e["ret"], e["sp"] = rnd(r, 2), rnd(b, 2)
            for h, d in HORIZONS:
                if days >= d and h not in e:
                    e[h] = [e["ret"], e["sp"]]

    # 1. Koopmomenten: eerste keer in de zone = nieuw, daarna volgen tot 12 maanden
    log = list((prev or {}).get("log") or [])
    recent = {e["sym"] for e in log if (today - datetime.fromisoformat(e["date"]).date()).days < KOOP_NEW_DAYS}
    for r in rows:
        if r["status"] == "zone" and r["sym"] not in recent and sp_of(r["region"]):
            log.append({"sym": r["sym"], "name": r["name"], "region": r["region"], "date": today.isoformat(),
                        "px0": r["native"], "card": r["card"], "dd": r["dd"], "own": bool(r.get("own")),
                        "sp0": rnd(sp_of(r["region"]), 4)})
    for e in log:
        if e.get("days", 0) > 400:
            continue
        follow(e, e["px0"], e["region"])
    log = [e for e in log if e.get("days", 0) <= 400]

    # 2. Scorecard-groepen: elke maand de stand vastleggen (100+ en 75-100), daarna volgen
    cohorts = list((prev or {}).get("cohorts") or [])
    month = today.strftime("%Y-%m")
    if sp_lvl and not any(c["month"] == month for c in cohorts):
        groups = {"top": [], "watch": []}
        for it in items:
            lvl = (it.get("card") or {}).get("lvl")
            if lvl in groups and it.get("native"):
                groups[lvl].append([it["sym"], it["region"], it["native"]])
        cohorts.append({"month": month, "date": today.isoformat(), "sp0": {k: rnd(v, 4) for k, v in sp_lvl.items()},
                        "groups": groups})
    for c in cohorts:
        days = (today - datetime.fromisoformat(c["date"]).date()).days
        c["days"] = days
        res = {}
        for g, members in c["groups"].items():
            rs, bs = [], []
            for sym, region, p0 in members:
                r, b = _rel(p0, px_now(sym), c["sp0"].get("VS" if region == "VS" else "EU"), sp_of(region))
                if r is not None:
                    rs.append(r)
                    bs.append(b)
            if rs:
                res[g] = {"n": len(rs), "ret": rnd(sum(rs) / len(rs), 2), "sp": rnd(sum(bs) / len(bs), 2),
                          "beat": rnd(sum(1 for r, b in zip(rs, bs) if r > b) / len(rs) * 100, 0)}
        c["res"] = res
        frozen = c.setdefault("at", {})
        for h, d in HORIZONS:
            if days >= d and h not in frozen and res:
                frozen[h] = res
    cohorts = cohorts[-24:]
    return {"updated": iso(), "params": {"min_card": KOOP_MIN_CARD, "dd": list(KOOP_DD), "near": KOOP_NEAR,
                                         "sma": KOOP_SMA, "earn_days": KOOP_EARN_DAYS},
            "candidates": rows, "log": log, "cohorts": cohorts}


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
FIN_FIELDS = ("fcf", "ocf", "rev_ttm")       # bedragen uit financialData, in de rapportagevaluta
TS_COUNTS = {"annualDilutedAverageShares"}   # aantallen aandelen, geen bedragen


def in_quote_currency(c, fx):
    """Rapporteert het bedrijf in de andere valuta dan de koers (euro-notering van een dollarbedrijf zoals TTE.PA,
    of een dollarnotering van een eurobedrijf zoals SPOT), dan de cijfers omrekenen naar de valuta van de koers.
    Anders zitten koers/omzet, FCF-rendement en de DCF er een factor EUR/USD naast."""
    cur = "USD" if c.get("region") == "VS" else "EUR"
    fcur = c.get("fcur")
    if not fx or fcur not in OK_FIN_CUR or fcur == cur:
        return c
    k = fx if fcur == "EUR" else 1 / fx          # fx = dollars per euro
    ts = {key: rows if key in TS_COUNTS else [[d, v * k] for d, v in rows] for key, rows in (c.get("ts") or {}).items()}
    return dict(c, ts=ts, fcur=cur, **{f: c[f] * k for f in FIN_FIELDS if c.get(f) is not None})


def build_item(sym, c, prices, fx, curated, own, gics=None):
    c = in_quote_currency(c, fx)
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
    hi, lo = q.get("hi52") or c.get("hi52"), q.get("lo52") or c.get("lo52")
    nm = norm_name(c.get("name"))

    # Ruwe kenmerken voor Score 2.0 (worden later per sector in percentielen omgezet)
    ts = c.get("ts") or {}
    last = lambda k: (series(ts, k) or [[None, None]])[-1][1]  # noqa: E731
    prev = lambda k: (series(ts, k)[-2][1] if len(series(ts, k)) >= 2 else None)  # noqa: E731
    rev = last("annualTotalRevenue") or last("annualOperatingRevenue")
    gp, ta = last("annualGrossProfit"), last("annualTotalAssets")
    oi, clb = last("annualOperatingIncome"), last("annualCurrentLiabilities")
    fcf, ocf, ni = last("annualFreeCashFlow"), last("annualOperatingCashFlow"), last("annualNetIncome")
    sh, sh0 = last("annualDilutedAverageShares"), prev("annualDilutedAverageShares")
    mcap_n = q.get("mcap") or c.get("mcap")
    fpe = q.get("fpe") or c.get("fpe")
    fwd_g = c["fwd_growth"] * 100 if c.get("fwd_growth") is not None else None
    chg12 = q.get("chg52") if q.get("chg52") is not None else (c["chg52"] * 100 if c.get("chg52") is not None else None)
    sma200 = q.get("sma200") or c.get("sma200")
    up, dn = c.get("rev_up") or 0, c.get("rev_down") or 0
    m = {
        "gp_assets": gp / ta * 100 if gp is not None and ta else None,
        "roic": (oi * 0.79 / (ta - clb) * 100 if oi is not None and ta and clb is not None and ta - clb > 0
                 else (c["roe"] * 100 if c.get("roe") is not None else None)),
        "fcf_margin": (fcf / rev * 100 if fcf is not None and rev
                       else (c["fcf"] / c["rev_ttm"] * 100 if c.get("fcf") is not None and c.get("rev_ttm") else None)),
        "cash_conv": min(3.0, ocf / ni) if ocf is not None and ni and ni > 0 else None,
        "dilution": (sh / sh0 - 1) * 100 if sh and sh0 else None,
        "rev_cagr3": extra.get("rev_cagr"),
        "rev_yoy": extra.get("rev_yoy"),
        "eps_g": c["eps_g"] * 100 if c.get("eps_g") is not None else None,
        "fwd_g": fwd_g,
        # verlieslatend (negatieve verwachte K/W) = slechtste waardering
        "fpe": (fpe if fpe and fpe > 0 else (9999 if fpe is not None else None)),
        "peg": (fpe / fwd_g if fpe and fpe > 0 and fwd_g and fwd_g > 0
                else (c["peg"] if c.get("peg") and c["peg"] > 0 else None)),
        "fcf_yield": (fcf if fcf is not None else c.get("fcf")) / mcap_n * 100
                     if (fcf is not None or c.get("fcf") is not None) and mcap_n else None,
        "ev_ebitda": c["ev_ebitda"] if c.get("ev_ebitda") and c["ev_ebitda"] > 0 else None,
        "chg12": chg12,
        "near_high": price_native / hi * 100 if hi else None,
        "revisions": (up - dn) / (up + dn) * 100 if up + dn > 0 else None,
    }
    emp = c.get("emp")
    m["rpe"] = (c.get("rev_ttm") or rev) / emp if emp and (c.get("rev_ttm") or rev) else None   # omzet per medewerker
    m = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()}
    gp_by = dict(map(tuple, series(ts, "annualGrossProfit")))
    rev_rows = series(ts, "annualTotalRevenue", "annualOperatingRevenue")
    crit_v = {x["k"]: x["v"] for x in crit}
    card_in = {
        "cash": cash, "debt": debt, "ebitda": last("annualEBITDA"), "oi": oi,
        "intexp": abs(last("annualInterestExpense")) if last("annualInterestExpense") else None,
        "rev_cagr": extra.get("rev_cagr"), "rev_yoy": extra.get("rev_yoy"), "margin": extra.get("margin"),
        "short": crit_v.get("short"), "inst": crit_v.get("instituten"), "scale": crit_v.get("schaal"),
        "r5": c.get("r5"), "founder": c.get("founder"), "ins": c.get("ins"),
        "gm": [gp_by[d] / r for d, r in rev_rows if r and gp_by.get(d) is not None],
        "curated": curated or {},
        # scorecard volgens de gids: laatste 12 maanden
        "g12": avg([x for x in (extra.get("rev_yoy"), c["rev_g"] * 100 if c.get("rev_g") is not None else None) if x is not None]),
        "om12": c["om"] * 100 if c.get("om") is not None else extra.get("margin"),
        "opex_yoy": extra.get("opex_yoy"),
        # 12 stappen
        "ts": ts, "c": c, "price": price_native, "rev_ttm": c.get("rev_ttm"),
        "mcap_n": mcap_n, "mcap_usd": (mcap_n if region == "VS" else mcap_n * fx) if mcap_n else None,
        "shares": (mcap_n / price_native) if mcap_n and price_native else (sh or None),
        "age": ((now().date() - datetime.fromisoformat((c.get("first") or {})["d"]).date()).days / 365.25)
               if (c.get("first") or {}).get("d") else None,
        "eps_hist": c.get("eps_hist"), "ins_buy": c.get("ins_buy"), "ins_sell": c.get("ins_sell"),
        "co_ceo": c.get("co_ceo"), "chg12": chg12, "dilution": m.get("dilution"),
        "fpe": fpe if fpe and fpe > 0 else None, "peg": m.get("peg"),
    }
    above200 = (price_native > sma200) if sma200 else None
    earn = c.get("earnings")
    earn_days = None
    if earn:
        try:
            earn_days = (datetime.fromisoformat(earn).date() - now().date()).days
        except Exception:  # noqa: BLE001
            earn_days = None
    return {
        "sym": sym, "eu": eu_sym, "exch": exch, "name": c.get("name") or sym,
        "us_exch": US_EXCH.get(c.get("exch"), c.get("exch")) if region == "VS" else None,
        "region": region, "country": c.get("country"), "sector": c.get("sector") or gics,
        "industry": c.get("industry"),
        "price": rnd(price_eur, 2), "approx": approx,
        "native": rnd(price_native, 2), "cur": "USD" if region == "VS" else "EUR",
        "day": rnd(q.get("day"), 2),
        "hi52": rnd(hi * k, 2) if hi else None, "lo52": rnd(lo * k, 2) if lo else None,
        "sma200": rnd(sma200 * k, 2) if sma200 else None,
        "mcap": rnd(((q.get("mcap") or c.get("mcap") or 0) * k) / 1e9, 1) or None,
        "score": rnd(total, 1), "formule": rnd(score_f, 1), "n_crit": n_crit,
        "m": m, "mp": {}, "above200": above200, "chg12": rnd(chg12, 1),
        "branch": branch_of(sym if region == "VS" else (eu_sym or sym), c.get("sector") or gics, c.get("industry")),
        "earnings": earn if earn_days is not None and earn_days >= 0 else None, "earn_days": earn_days if earn_days is not None and earn_days >= 0 else None,
        "kwal": kwal, "q": qual, "exp": exp,
        "cashdebt": [rnd(cash / 1e9 * (1 / fx if region == "VS" else 1), 2) if cash is not None else None,
                     rnd(debt / 1e9 * (1 / fx if region == "VS" else 1), 2) if debt is not None else None],
        "watch": sym in own["watch"] or (eu_sym or "") in own["watch"] or nm in own["names_watch"],
        "own": bool(sym in own["own"] or (eu_sym or "") in own["own"] or (nm and nm in own["names_own"])),
        "_card": card_in,
    }


def main():
    t0 = time.time()
    y = Yahoo()
    try:
        raw = json.load(open(CACHE, encoding="utf-8"))
        cache, dyn = raw.get("stocks", {}), raw.get("dyn")
    except Exception:  # noqa: BLE001
        cache, dyn = {}, None
    uni, dyn = load_universe(cache, y, dyn)
    own = owned_positions()
    # Elke eigen aandelenpositie met een dollarnotering hoort erin (ook als hij te klein is voor de brede scan),
    # zodat je ze allemaal op de pagina Beoordeling terugziet. Euro-noteringen van VS-bedrijven lopen via die dollarnotering.
    # Niet voor ETF's en ETC's (GLD, SLV), en niet als je euro-notering zelf al meedoet (ASML.AS: geen ASML erbij).
    for s_, src_ in sorted(own["stock_us"].items()):
        if s_ not in uni and src_ not in uni:
            uni[s_] = {"region": "VS", "src": "eigen", "name": None}
    quality_map = load_quality()
    print(f"Universum: {len(uni)} stocks · eigen posities: {len(own['own'])} · kwaliteitsoordelen: {len(quality_map)}")

    try:
        fx = fx_eurusd(y)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"EUR/USD niet opgehaald ({e}); probeer het later opnieuw.")
    print(f"EUR/USD {fx:.4f}")

    def is_owned(sym):
        """Zelfde herkenning als 'own' in build_item, dus ook op naam: GOOGL telt mee al staat alleen GOOG
        (via ABEA.DE) in je posities, want dedupe_listings maakt van GOOG een andere notering van GOOGL."""
        c = cache.get(sym) or {}
        nm = norm_name(c.get("name"))
        return (sym in own["own"] or ((c.get("eu") or {}).get("sym") or "") in own["own"]
                or bool(nm and nm in own["names_own"]))

    # 1. Fundamentele cijfers verversen: oudste eerst, binnen het tijdsbudget
    # eigen posities worden ook gescand: ze dienen als referentie en tellen mee in de vergelijking
    todo = [s for s in uni if needs_refresh(cache.get(s), uni[s]["region"])]
    # eigen posities eerst (die beoordeel je zelf), dan de oudste
    todo.sort(key=lambda s: (0 if is_owned(s) else 1, (cache.get(s) or {}).get("f") or ""))
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

    # 3. Referentie-ETF's per branche en ETF-doorkijk
    bench, fxchg = benchmarks(y)
    sp5 = sp500_5y(y)
    try:
        etf_prev = json.load(open(ETF_OUT, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        etf_prev = None
    etf_data = etf_lookthrough(y, etf_prev)
    with open(ETF_OUT, "w", encoding="utf-8") as f:
        json.dump(etf_data, f, ensure_ascii=False, separators=(",", ":"))

    # 4. Scoren
    items = []
    for sym in ready:
        try:
            it = build_item(sym, cache[sym], prices, fx, quality_map.get(sym)
                            or quality_map.get((cache[sym].get("eu") or {}).get("sym") or ""), own,
                            uni[sym].get("gics"))
        except Exception as e:  # noqa: BLE001
            print(f"  {sym:<10} score mislukt: {e}")
            continue
        if it:
            items.append(it)

    # Dubbele noteringen weg vóór het scoren, zodat een bedrijf niet meerdere keren meetelt
    before = len(items)
    items = dedupe_listings(items)
    print(f"Dubbele noteringen: {before - len(items)} weggelaten, {len(items)} bedrijven over")

    # Score 2.0: percentielen binnen de sector, vijf pijlers, samengestelde score, totaalscore = percentiel in de markt
    grp = lambda it: it.get("sector") or "?"  # noqa: E731
    for key, (_, higher) in METRICS.items():
        pct_ranks(items, key, higher, grp)
    pct_ranks(items, "rpe", True, grp)         # alleen voor de scorecard (talent), geen pijler
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    for it in items:
        mp = it["mp"]
        by = {pl: [mp[k] for k, (p2, _) in METRICS.items() if p2 == pl and k in mp] for pl in ("Q", "G", "V", "M")}
        qual_pct = it["kwal"] / 30 * 100
        fin_q = mean(by["Q"])
        mom = by["M"] + ([100.0 if it["above200"] else 0.0] if it["above200"] is not None else [])
        scorecard(it, sp5)
        cs = (it.get("card") or {}).get("s")
        # Pijler F = de scorecard van de gids (0-125 → 0-100); zonder scorecard het oude formuleblad
        pil = {"F": cs / 1.25 if cs is not None else it["formule"],
               "Q": 0.5 * fin_q + 0.5 * qual_pct if fin_q is not None else qual_pct,
               "G": mean(by["G"]), "V": mean(by["V"]), "M": mean(mom)}
        wsum = sum(PILLAR_W[k] for k, v in pil.items() if v is not None)
        it["composite"] = round(sum(PILLAR_W[k] * v for k, v in pil.items() if v is not None) / wsum, 2) if wsum else 0
        it["pillars"] = {k: (round(v, 1) if v is not None else None) for k, v in pil.items()}
        it["val"] = valuation_check(it)
    # 12 stappen (de juiste multiple vergelijkt met de sector: mediane verwachte K/W en koers/omzet)
    med = {}
    for key, fn in (("pe", lambda it: it["_card"]["fpe"]),
                    ("ps", lambda it: it["_card"]["mcap_n"] / it["_card"]["rev_ttm"] if it["_card"]["mcap_n"] and it["_card"]["rev_ttm"] else None)):
        by = {}
        for it in items:
            v = fn(it)
            if v and 0 < v < 1000:
                by.setdefault(it.get("sector"), []).append(v)
        for sec, vs in by.items():
            vs.sort()
            med[(key, sec)] = vs[len(vs) // 2]
    for it in items:
        try:
            twelve_steps(it, med)
        except Exception as e:  # noqa: BLE001
            print(f"  {it['sym']:<10} 12 stappen mislukt: {e}")
            it["s12"] = None
        it.pop("_card", None)
        it["koop"] = koopmoment(it)
        if not it["own"] and ((it.get("card") or {}).get("s") or 0) < CARD_SELECT:
            it["s12"] = s12_brief(it["s12"])      # niet getoond in de lijst: alleen de status per stap (scheelt veel data)
        # relatieve sterkte t.o.v. de referentie-ETF van de branche (12 maanden, in euro)
        b = bench.get(it.get("branch") or "")
        if b and b.get("ret12") is not None and it.get("chg12") is not None:
            r = it["chg12"] / 100
            if it["region"] == "VS" and fxchg is not None:
                r = (1 + r) / (1 + fxchg) - 1
            it["rs"] = {"etf": b["etf"].split(".")[0], "pp": round(r * 100 - b["ret12"], 1)}
    from bisect import bisect_left, bisect_right
    comps = sorted(it["composite"] for it in items)
    n = len(comps)
    for it in items:
        lo, hi = bisect_left(comps, it["composite"]), bisect_right(comps, it["composite"])
        it["score"] = round((lo + 0.5 * (hi - lo)) / n * 100, 1) if n else 0
    # Volgorde: scorecard (hoofdgetal, 0-125), dan marktrang (Score 2.0, percentiel), dan verwacht rendement
    cardv = lambda it: (it.get("card") or {}).get("s") if (it.get("card") or {}).get("s") is not None else -1  # noqa: E731
    refs = sorted([it for it in items if it["own"]], key=lambda x: (-cardv(x), -x["score"]))
    items = [it for it in items if not it["own"]]
    expo = lambda it: (it["exp"] or {}).get("pct") if (it["exp"] or {}).get("pct") is not None else -1e9  # noqa: E731
    items.sort(key=lambda x: (-cardv(x), -x["score"], -expo(x), x["name"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i
    for key in bench:
        inb = [it for it in items if it.get("branch") == key]
        bench[key]["n85"] = sum(1 for it in inb if cardv(it) >= CARD_SELECT)     # aantal kansen (scorecard 75+)

    tried = lambda s: (cache.get(s) or {}).get("f") or (cache.get(s) or {}).get("err_at")
    pending = sum(1 for s in uni if not tried(s))
    failed = sum(1 for s in uni if not (cache.get(s) or {}).get("f") and (cache.get(s) or {}).get("err_at"))
    out = {
        "updated": iso(),
        "fx": rnd(fx, 4),
        "universe": len(uni),
        "scanned": sum(1 for s in uni if tried(s)),
        "failed": failed,
        "scored": len(items),
        "pending": pending,
        "weights": PILLAR_W,
        "card_select": CARD_SELECT,
        "sp5": {k: rnd(sp5[k] * 100, 1) for k in ("VS", "EU")} if sp5 else None,
        "branches": bench,
        "refs": refs,
        "items": items[:TOP_N],
        # eigen posities die niet te scoren zijn (bv. rapporteert in een andere valuta), met de reden
        "own_skip": {s_: (cache.get(s_) or {}).get("skip") for s_ in uni
                     if s_ in own["own"] and (cache.get(s_) or {}).get("skip")},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    try:                                             # koopmomenten + scorecard-groepen; mag de Top 250 nooit laten vastlopen
        try:
            koop_prev = json.load(open(KOOP_OUT, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            koop_prev = None
        rows = koop_rows(items + refs)
        koop = update_koop(koop_prev, rows, items + refs, prices, cache, sp5, fx)
        with open(KOOP_OUT, "w", encoding="utf-8") as f:
            json.dump(koop, f, ensure_ascii=False, separators=(",", ":"))
        print(f"Koopmoment: {sum(1 for r in rows if r['status'] == 'zone')} in de zone, "
              f"{sum(1 for r in rows if r['status'] == 'bijna')} bijna · {len(koop['log'])} gevolgd")
    except Exception as e:  # noqa: BLE001
        print(f"  koopmoment niet bijgewerkt: {e}")
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"updated": iso(), "dyn": dyn, "stocks": compact(dict(sorted(cache.items())))}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"Klaar: {len(items)} gescoord, top {min(TOP_N, len(items))} weggeschreven · "
          f"{done} ververst · nog {pending} nooit gescand · {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
