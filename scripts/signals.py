#!/usr/bin/env python3
"""
Patroonradar — genereert signals.json voor signals.html.

Haalt per ticker ~2 jaar dagkoersen op bij Yahoo Finance (server-side, dus
geen CORS), zoekt bekende technische patronen, schat de verwachte beweging
binnen HORIZON handelsdagen en test het model terug op de eigen historie.

Staat een Europese notering (.DE/.AS/.MI/.DU) ook op NASDAQ/NYSE, dan wordt het patroon
berekend op de Amerikaanse notering (langere historie, meer handel). Koop- en verkooppunten
worden daarna omgerekend naar de euro-notering, zodat je in euro kunt handelen.

Alleen standaardbibliotheek: geen pip install nodig in GitHub Actions.

Tickerbron (eerste die iets oplevert):
  1. tickers.json  — optionele eigen lijst, bv. [{"symbol":"1YD.DE","name":"Broadcom"}]
  2. tickers.txt   — dezelfde lijst die het portfolio-dashboard gebruikt
  3. data.json     — de bestaande dashboard-feed; tickers worden er automatisch uit gehaald
"""
import json
import math
import os
import re
import sys
import time
from bisect import bisect_right
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

# ── Instellingen ─────────────────────────────────────────────────────────
HORIZON = 10          # handelsdagen vooruit (~2 weken)
DROP_PCT = 5.0        # daling vanaf deze grootte = interessant
RISE_PCT = 5.0        # stijging vanaf deze grootte = interessant
EVAL_RISE_PCT = 5.0   # stijging die de backtest als 'raak' telt
SIGNAL_SCORE = 35     # |score| vanaf hier is er een richting (schaal -100..100)
PIVOT_K = 5           # bars links/rechts voor een swing-top/-bodem
SPARK_BARS = 120
RETRACE = 0.5         # na het koersdoel: terugveer-/terugvalniveau (50% van de beweging)
US_HISTORY = "5y"     # patroon op de Amerikaanse notering: langere historie
US_EXCHANGES = {"NMS", "NGM", "NCM", "NAS", "NYQ", "ASE", "PCX", "BTS"}
PRICE_MATCH = 0.12    # euro-koers en omgerekende dollarkoers mogen max 12% verschillen
# Handmatig vastleggen of uitsluiten, bv. {"45C.DE": "XYZ"} of {"PHAU.AS": None}
US_LISTING_OVERRIDES = {}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "signals.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-\^=]{0,11}(\.[A-Z]{1,3})?$")
TICKER_KEYS = {"ticker", "symbol", "yahoo", "yahooticker", "yahoo_symbol", "sym", "code"}


# ── Tickers ──────────────────────────────────────────────────────────────
def load_tickers():
    manual = os.path.join(ROOT, "tickers.json")
    if os.path.exists(manual):
        with open(manual, encoding="utf-8") as f:
            raw = json.load(f)
        out = []
        for t in raw:
            if isinstance(t, str):
                out.append({"symbol": t})
            elif isinstance(t, dict) and t.get("symbol"):
                out.append(t)
        if out:
            return out, "tickers.json"

    txt = os.path.join(ROOT, "tickers.txt")
    if os.path.exists(txt):
        out = parse_tickers_txt(txt)
        if out:
            return out, "tickers.txt"

    feed = os.path.join(ROOT, "data.json")
    if not os.path.exists(feed):
        sys.exit("Geen tickers.json en geen data.json gevonden.")
    with open(feed, encoding="utf-8") as f:
        data = json.load(f)

    found = {}

    def walk(node, category=None):
        if isinstance(node, dict):
            sym = None
            for k, v in node.items():
                if k.lower() in TICKER_KEYS and isinstance(v, str) and TICKER_RE.match(v.strip()):
                    sym = v.strip()
            if sym and sym not in found:
                found[sym] = {
                    "symbol": sym,
                    "name": node.get("name") or node.get("naam") or node.get("title"),
                    "category": node.get("category") or node.get("categorie") or node.get("section") or category,
                }
            for k, v in node.items():
                # {"1YD.DE": {...}} — ticker als sleutel
                if isinstance(v, dict) and TICKER_RE.match(k) and k not in found and (
                        "price" in v or "regularMarketPrice" in v or "close" in v or "prijs" in v):
                    found[k] = {"symbol": k, "name": v.get("name"), "category": category}
                walk(v, category)
        elif isinstance(node, list):
            for v in node:
                walk(v, category)

    walk(data)
    if not found:
        sys.exit("Geen tickers herkend in data.json — maak een tickers.json aan.")
    return list(found.values()), "data.json"


def portfolio_info():
    """Categorie en label per ticker uit de DEFAULT-lijst in index.html."""
    info = {}
    path = os.path.join(ROOT, "index.html")
    if not os.path.exists(path):
        return info
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for m in re.finditer(r"\{[^{}]*?ticker:\s*['\"]([^'\"]+)['\"][^{}]*?\}", html):
        obj = m.group(0)
        sec = re.search(r"section:\s*(['\"])(.*?)\1", obj)
        lab = re.search(r"label:\s*(['\"])(.*?)\1", obj)
        num = lambda k: (lambda r: float(r.group(1)) if r else None)(  # noqa: E731
            re.search(k + r":\s*(-?[0-9.]+)", obj))
        info[m.group(1).upper()] = {"section": sec.group(2) if sec else None,
                                    "label": lab.group(2) if lab else None,
                                    "watch": "watch: true" in obj,
                                    "cost": num("cost"), "qty": num("qty"), "thresh": num("thresh")}
    ver = re.search(r"DEFAULT_VERSION\s*=\s*(\d+)", html)
    info["__version__"] = int(ver.group(1)) if ver else 0
    return info


def enrich(tickers):
    info = portfolio_info()
    names = {}
    feed = os.path.join(ROOT, "data.json")
    if os.path.exists(feed):
        try:
            with open(feed, encoding="utf-8") as f:
                q = json.load(f).get("quotes", {})
            names = {k.upper(): v.get("name") for k, v in q.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            pass
    for t in tickers:
        sym = t["symbol"].upper()
        p = info.get(sym, {})
        t["category"] = p.get("section") or t.get("category")
        t["label"] = p.get("label")
        t["watch"] = bool(p.get("watch")) or (t.get("category") or "").lower() == "watchlist"
        t["name"] = t.get("name") or names.get(sym)
        t["position"] = {"ticker": sym, "section": t["category"], "label": t["label"], "watch": t["watch"],
                         "cost": p.get("cost"), "qty": p.get("qty"), "thresh": p.get("thresh")}
    return tickers, info.get("__version__", 0)


def parse_tickers_txt(path):
    """Leest tickers.txt van het portfolio-dashboard.

    Werkt met o.a.:  1YD.DE            1YD.DE, Broadcom, Stocks
                     1YD.DE Broadcom   1YD.DE;Broadcom      [Stocks] / Stocks: / # Stocks als kopje
    """
    out, seen, category = [], set(), None
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            head = re.match(r"^\[(.+)\]$|^#+\s*(.+)$|^([^,;|\t]+):$", line)
            if head:
                label = next(g for g in head.groups() if g).strip()
                first = label.split()[0].upper() if label.split() else ""
                if len(label) <= 25 and len(label.split()) <= 3 and not ("." in first and TICKER_RE.match(first)):
                    category = label
                continue
            line = re.split(r"\s+#", line)[0].strip()          # inline commentaar
            if any(sep in line for sep in (",", ";", "|", "\t")):
                parts = [x.strip().strip('"\'') for x in re.split(r"[,;|\t]", line)]
            else:
                parts = line.split(None, 1)
                if len(parts) > 1:
                    parts[1] = parts[1].strip(" =-:").strip('"\'')
            sym = parts[0].upper()
            if not TICKER_RE.match(sym) or sym in seen:
                continue
            seen.add(sym)
            out.append({"symbol": sym,
                        "name": parts[1] if len(parts) > 1 and parts[1] else None,
                        "category": parts[2] if len(parts) > 2 and parts[2] else category})
    return out


# ── Data ─────────────────────────────────────────────────────────────────
def add_live_bar(bars, meta):
    """Yahoo levert de dagkaars van vandaag bij Europese noteringen vaak pas de volgende dag.
    Daarom de laatste koers (regularMarketPrice) als kaars van vandaag toevoegen of bijwerken."""
    p, t = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if not p or not t or not bars:
        return bars
    off = meta.get("gmtoffset") or 0
    day = lambda ts: (ts + off) // 86400  # noqa: E731
    ts, o, h, l, c, v = bars[-1]
    if day(t) > day(ts):
        hi = meta.get("regularMarketDayHigh") or p
        lo = meta.get("regularMarketDayLow") or p
        bars.append((t, c, max(hi, p), min(lo, p), p, meta.get("regularMarketVolume") or 0))
    elif day(t) == day(ts):
        bars[-1] = (ts, o, max(h, p), min(l, p), p, v)
    return bars


def fetch_bars(symbol, rng="2y", min_bars=60):
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
               f"?range={rng}&interval=1d&includePrePost=false")
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": UA}), timeout=20) as r:
                    j = json.load(r)
                res = j["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                bars = []
                for i, ts in enumerate(res.get("timestamp") or []):
                    o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
                    if None in (o, h, l, c):
                        continue
                    bars.append((ts, o, h, l, c, v or 0))
                meta = res.get("meta", {})
                bars = add_live_bar(bars, meta)
                if len(bars) < min_bars:
                    raise ValueError(f"te weinig koersdata ({len(bars)} dagen)")
                return bars, meta
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_err))


def get_json(url):
    with urlopen(Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        return json.load(r)


def eurusd():
    """Dollars per euro (laatste slot)."""
    bars, _ = fetch_bars("EURUSD=X", "5d", 1)
    return bars[-1][4]


def find_us_listing(tk, eu_price, fx, cache):
    """Zoekt dezelfde stock op NASDAQ/NYSE. Controle: omgerekende dollarkoers ~ euro-koers."""
    sym = tk["symbol"].upper()
    if "." not in sym:
        return None                         # is zelf al de Amerikaanse notering
    if sym in US_LISTING_OVERRIDES:
        return US_LISTING_OVERRIDES[sym]
    hit = cache.get(sym)
    if hit is not None and hit.get("checked") == datetime.now(timezone.utc).strftime("%Y-%W"):
        return hit.get("us") or None        # deze week al gecontroleerd
    name = (tk.get("name") or "").split(",")[0]
    if not name:
        return None
    try:
        res = get_json("https://query2.finance.yahoo.com/v1/finance/search?"
                       f"q={quote(name)}&quotesCount=8&newsCount=0")
    except Exception:  # noqa: BLE001
        return None
    for q in res.get("quotes", []):
        if q.get("exchange") not in US_EXCHANGES or q.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        try:
            bars, meta = fetch_bars(q["symbol"], "5d", 1)
        except Exception:  # noqa: BLE001
            continue
        if meta.get("currency") != "USD":
            continue
        ratio = eu_price / (bars[-1][4] / fx)
        if abs(ratio - 1) <= PRICE_MATCH:
            return q["symbol"]
    return ""                               # gezocht, niets gevonden


def load_listing_cache():
    """Gevonden Amerikaanse noteringen uit de vorige run (staat al in signals.json)."""
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        return {it["symbol"].upper(): {"us": it.get("pattern_symbol") or "", "checked": it.get("listing_checked")}
                for it in old.get("items", []) + old.get("errors", []) if it.get("listing_checked")}
    except Exception:  # noqa: BLE001
        return {}


def to_euro(item, eu_bars, eu_meta, us_symbol):
    """Patroon komt van de Amerikaanse notering; alle koersen omzetten naar de euro-notering.
    Omrekening in verhouding: het doel ligt evenveel procent boven/onder de euro-koers."""
    eu_price = eu_bars[-1][4]
    k = eu_price / item["price"]
    sc = lambda x: round(x * k, 4) if x is not None else None  # noqa: E731
    for f in ("target", "support", "resistance"):
        item[f] = sc(item[f])
    if item.get("plan"):
        item["plan"] = {a: sc(b) for a, b in item["plan"].items()}
    item["spark"] = [round(x * k, 4) for x in item["spark"]]
    for p in item["patterns"]:
        p["detail"] = re.sub(r"neklijn (\d+(?:\.\d+)?)", lambda m: f"neklijn {float(m.group(1)) * k:.2f}", p["detail"])
    prev = eu_bars[-2][4] if len(eu_bars) > 1 else eu_meta.get("chartPreviousClose")
    item.update({
        "price": round(eu_price, 4),
        "day_pct": round((eu_price / prev - 1) * 100, 2) if prev else None,
        "currency": eu_meta.get("currency", "EUR"),
        "asof": datetime.fromtimestamp(eu_bars[-1][0], tz=timezone.utc).isoformat(),
        "pattern_symbol": us_symbol,
    })
    return item


# ── Indicatoren ──────────────────────────────────────────────────────────
def sma(a, n):
    out, s = [None] * len(a), 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(a, n):
    out, k, prev = [None] * len(a), 2 / (n + 1), None
    for i, x in enumerate(a):
        if x is None:
            continue
        prev = x if prev is None else x * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, n + 1)]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, n + 1)]
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n, len(c)):
        if i > n:
            d = c[i] - c[i - 1]
            ag = (ag * (n - 1) + max(d, 0)) / n
            al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    out, prev = [None] * len(c), None
    for i in range(len(c)):
        if i == n - 1:
            prev = sum(tr[:n]) / n
        elif i >= n:
            prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def rolling_std(a, n):
    out = [None] * len(a)
    for i in range(n - 1, len(a)):
        w = a[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return out


def pivots(h, l, k):
    """Swing-toppen/-bodems. Een pivot op p is pas bekend op p+k (geen vooruitkijken)."""
    highs, lows = [], []
    for p in range(k, len(h) - k):
        if h[p] == max(h[p - k:p + k + 1]):
            highs.append(p)
        if l[p] == min(l[p - k:p + k + 1]):
            lows.append(p)
    return highs, lows


class Series:
    def __init__(self, bars):
        self.t = [b[0] for b in bars]
        self.o = [b[1] for b in bars]
        self.h = [b[2] for b in bars]
        self.l = [b[3] for b in bars]
        self.c = [b[4] for b in bars]
        self.v = [b[5] for b in bars]
        c = self.c
        self.sma20, self.sma50, self.sma200 = sma(c, 20), sma(c, 50), sma(c, 200)
        e12, e26 = ema(c, 12), ema(c, 26)
        self.macd = [a - b if a is not None and b is not None else None for a, b in zip(e12, e26)]
        self.macd_sig = ema(self.macd, 9)
        self.rsi = rsi(c)
        self.atr = atr(self.h, self.l, c)
        sd = rolling_std(c, 20)
        self.bb_up = [m + 2 * s if m is not None else None for m, s in zip(self.sma20, sd)]
        self.bb_lo = [m - 2 * s if m is not None else None for m, s in zip(self.sma20, sd)]
        self.bb_w = [(u - lo) / m if m else None for u, lo, m in zip(self.bb_up, self.bb_lo, self.sma20)]
        self.vavg = sma(self.v, 20)
        rets = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
        self.sigma = rolling_std(rets, 60)
        self.ph, self.pl = pivots(self.h, self.l, PIVOT_K)

    def known_pivots(self, arr, i, window):
        """Pivots die op dag i al bevestigd zijn en binnen `window` dagen liggen."""
        idx = bisect_right(arr, i - PIVOT_K)
        return [p for p in arr[:idx] if p >= i - window]


def crossed(a, b, i, lookback, up):
    for j in range(i - lookback + 1, i + 1):
        if j < 1 or None in (a[j], b[j], a[j - 1], b[j - 1]):
            continue
        if up and a[j - 1] <= b[j - 1] and a[j] > b[j]:
            return True
        if not up and a[j - 1] >= b[j - 1] and a[j] < b[j]:
            return True
    return False


# ── Patronen ─────────────────────────────────────────────────────────────
def evaluate(S, i):
    """Alle patronen op dag i. Geeft (lijst patronen, lijst koersdoelen)."""
    c, h, l, o = S.c, S.h, S.l, S.o
    P, targets = [], []

    def add(label, w, detail=""):
        P.append({"label": label, "dir": "up" if w > 0 else "down" if w < 0 else "info",
                  "w": round(w, 2), "detail": detail})

    price = c[i]

    # Trend
    if S.sma200[i]:
        if crossed(S.sma50, S.sma200, i, 10, True):
            add("Golden cross", 2.0, "50-daags gemiddelde kruist boven het 200-daagse")
        elif crossed(S.sma50, S.sma200, i, 10, False):
            add("Death cross", -2.0, "50-daags gemiddelde kruist onder het 200-daagse")
        add("Boven 200-daags gemiddelde" if price > S.sma200[i] else "Onder 200-daags gemiddelde",
            0.5 if price > S.sma200[i] else -0.5, "langetermijntrend")
    if S.sma50[i]:
        if crossed(c, S.sma50, i, 3, False):
            add("Zakt door 50-daags gemiddelde", -1.0)
        elif crossed(c, S.sma50, i, 3, True):
            add("Stijgt door 50-daags gemiddelde", 1.0)

    # Momentum
    r = S.rsi[i]
    if r is not None:
        recent = [x for x in S.rsi[i - 5:i] if x is not None]
        if r < 70 and recent and max(recent) >= 70:
            add("RSI keert uit overbought", -2.0, f"RSI {r:.0f}")
        elif r >= 70:
            add("Overbought", -1.0, f"RSI {r:.0f}")
        elif r > 30 and recent and min(recent) <= 30:
            add("RSI keert uit oversold", 2.0, f"RSI {r:.0f}")
        elif r <= 30:
            add("Oversold", 1.0, f"RSI {r:.0f}")
    if S.macd_sig[i] is not None:
        if crossed(S.macd, S.macd_sig, i, 3, False):
            add("MACD kruist omlaag", -1.5 if S.macd[i] > 0 else -1.0)
        elif crossed(S.macd, S.macd_sig, i, 3, True):
            add("MACD kruist omhoog", 1.5 if S.macd[i] < 0 else 1.0)

    # Divergentie: koers en RSI gaan uit elkaar
    ph = S.known_pivots(S.ph, i, 70)
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        if b - a >= 8 and h[b] > h[a] and S.rsi[a] and S.rsi[b] and S.rsi[b] < S.rsi[a] - 3 and i - b <= 20:
            add("Bearish divergentie", -2.5, "hogere top in koers, lagere top in RSI")
    pl = S.known_pivots(S.pl, i, 70)
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        if b - a >= 8 and l[b] < l[a] and S.rsi[a] and S.rsi[b] and S.rsi[b] > S.rsi[a] + 3 and i - b <= 20:
            add("Bullish divergentie", 2.5, "lagere bodem in koers, hogere bodem in RSI")

    # Bollinger
    if S.bb_up[i]:
        if price < S.bb_up[i] and any(c[j] > S.bb_up[j] for j in range(i - 3, i) if S.bb_up[j]):
            add("Terugval vanaf bovenste Bollinger-band", -1.0)
        elif price > S.bb_lo[i] and any(c[j] < S.bb_lo[j] for j in range(i - 3, i) if S.bb_lo[j]):
            add("Herstel vanaf onderste Bollinger-band", 1.0)
        w = [x for x in S.bb_w[i - 120:i] if x is not None]
        if len(w) > 60 and S.bb_w[i - 1] is not None and S.bb_w[i - 1] <= min(w) * 1.05:
            if price > S.bb_up[i]:
                add("Squeeze-uitbraak omhoog", 1.5, "lage beweeglijkheid breekt open")
            elif price < S.bb_lo[i]:
                add("Squeeze-uitbraak omlaag", -1.5, "lage beweeglijkheid breekt open")

    # Uitbraak / breakdown 55 dagen
    if i >= 56:
        hh, ll = max(h[i - 55:i]), min(l[i - 55:i])
        vol_ok = S.vavg[i] and S.v[i] > 1.5 * S.vavg[i]
        if price > hh:
            add("Uitbraak boven 55-daags hoogste", 2.0 if vol_ok else 1.0, "met hoog volume" if vol_ok else "")
        elif price < ll:
            add("Breekt onder 55-daags laagste", -2.0 if vol_ok else -1.0, "met hoog volume" if vol_ok else "")

    # Double top / bottom
    ph = S.known_pivots(S.ph, i, 90)
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        top = max(h[a], h[b])
        neck = min(l[a:b + 1])
        if 10 <= b - a <= 80 and abs(h[b] - h[a]) / top <= 0.03 and (top - neck) / top >= 0.04 \
                and i - b <= 30 and price < h[b] * 0.98:
            if price < neck:
                add("Double top bevestigd", -3.0, "koers onder de neklijn")
                targets.append(("down", neck - (top - neck)))
            else:
                add("Mogelijke double top", -1.5, f"neklijn {neck:.2f}")
                targets.append(("down", neck))
    pl = S.known_pivots(S.pl, i, 90)
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        bot = min(l[a], l[b])
        neck = max(h[a:b + 1])
        if 10 <= b - a <= 80 and abs(l[b] - l[a]) / bot <= 0.03 and (neck - bot) / neck >= 0.04 \
                and i - b <= 30 and price > l[b] * 1.02:
            if price > neck:
                add("Double bottom bevestigd", 3.0, "koers boven de neklijn")
                targets.append(("up", neck + (neck - bot)))
            else:
                add("Mogelijke double bottom", 1.5, f"neklijn {neck:.2f}")
                targets.append(("up", neck))

    # Kop-schouders (en omgekeerd)
    ph = S.known_pivots(S.ph, i, 130)
    if len(ph) >= 3:
        ls, hd, rs = ph[-3], ph[-2], ph[-1]
        if h[hd] > h[ls] * 1.03 and h[hd] > h[rs] * 1.03 and abs(h[ls] - h[rs]) / h[hd] <= 0.05 and i - rs <= 25:
            neck = (min(l[ls:hd + 1]) + min(l[hd:rs + 1])) / 2
            if price < neck:
                add("Kop-schouderformatie bevestigd", -3.5, "koers onder de neklijn")
                targets.append(("down", neck - (h[hd] - neck)))
            elif price < h[rs]:
                add("Mogelijke kop-schouderformatie", -1.5, f"neklijn {neck:.2f}")
                targets.append(("down", neck))
    pl = S.known_pivots(S.pl, i, 130)
    if len(pl) >= 3:
        ls, hd, rs = pl[-3], pl[-2], pl[-1]
        if l[hd] < l[ls] * 0.97 and l[hd] < l[rs] * 0.97 and abs(l[ls] - l[rs]) / l[hd] <= 0.05 and i - rs <= 25:
            neck = (max(h[ls:hd + 1]) + max(h[hd:rs + 1])) / 2
            if price > neck:
                add("Omgekeerde kop-schouders bevestigd", 3.5, "koers boven de neklijn")
                targets.append(("up", neck + (neck - l[hd])))
            elif price > l[rs]:
                add("Mogelijke omgekeerde kop-schouders", 1.5, f"neklijn {neck:.2f}")
                targets.append(("up", neck))

    # Candlesticks (laatste dag, alleen met trendcontext)
    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    up_ctx = S.sma20[i] and c[i - 1] > S.sma20[i - 1] if S.sma20[i - 1] else False
    dn_ctx = S.sma20[i] and c[i - 1] < S.sma20[i - 1] if S.sma20[i - 1] else False
    if rng > 0:
        upper, lower = h[i] - max(o[i], c[i]), min(o[i], c[i]) - l[i]
        if up_ctx and c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
            add("Bearish engulfing", -1.0, "candlestick")
        elif dn_ctx and c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
            add("Bullish engulfing", 1.0, "candlestick")
        elif up_ctx and body > 0 and upper >= 2 * body and lower <= body * 0.5:
            add("Shooting star", -0.75, "candlestick")
        elif dn_ctx and body > 0 and lower >= 2 * body and upper <= body * 0.5:
            add("Hammer", 0.75, "candlestick")

    return P, targets


def score_of(P):
    return round(100 * math.tanh(sum(p["w"] for p in P) / 5))


def levels(S, i):
    """Steun en weerstand uit swing-punten van het afgelopen jaar."""
    price = S.c[i]
    lows = [S.l[p] for p in S.known_pivots(S.pl, i, 250)]
    highs = [S.h[p] for p in S.known_pivots(S.ph, i, 250)]
    extra = [x for x in (S.sma50[i], S.sma200[i]) if x]
    gap = 0.5 * (S.atr[i] or 0)
    sup = [x for x in lows + extra if x < price - gap]
    res = [x for x in highs + extra if x > price + gap]
    return (max(sup) if sup else None), (min(res) if res else None)


def project(S, i, score, targets):
    price = S.c[i]
    sig = (S.sigma[i] or 0.02) * math.sqrt(HORIZON)
    sup, res = levels(S, i)
    if score <= -SIGNAL_SCORE:
        t = [x for d, x in targets if d == "down" and x < price]
        target = min(t) if t else (sup if sup else price * math.exp(-2 * sig))
    elif score >= SIGNAL_SCORE:
        t = [x for d, x in targets if d == "up" and x > price]
        target = max(t) if t else (res if res else price * math.exp(2 * sig))
    else:
        target = None
    return target, sup, res, sig


def make_plan(price, direction, target, sup, res, sig):
    """Verkoop/koop nu, doel, vervolgstap na terugveer en de grens waarop het plan vervalt."""
    if not target or direction == "flat":
        return None
    if direction == "down":   # verkopen nu, terugkopen op doel, weer verkopen na herstel
        nxt = target + RETRACE * (price - target)
        stop = res if res and res > price else price * math.exp(sig)
    else:                     # kopen nu, verkopen op doel, terugkopen na terugval
        nxt = target - RETRACE * (target - price)
        stop = sup if sup and sup < price else price * math.exp(-sig)
    return {"entry": price, "target": target, "next": nxt, "stop": stop}


def plan_outcome(S, i, d, plan):
    """Speel het plan na: eerst het doel gehaald = winst, eerst de grens geraakt = verlies.
    Raakt een dag beide, dan telt het als verlies (voorzichtig)."""
    entry, target, stop = plan["entry"], plan["target"], plan["stop"]
    for j in range(i + 1, i + HORIZON + 1):
        if d == "up":
            if S.l[j] <= stop:
                return "loss", stop / entry - 1
            if S.h[j] >= target:
                return "win", target / entry - 1
        else:
            if S.h[j] >= stop:
                return "loss", 1 - stop / entry
            if S.l[j] <= target:
                return "win", 1 - target / entry
    end = S.c[i + HORIZON]
    return "open", (end / entry - 1) if d == "up" else (1 - end / entry)


# ── Backtest ─────────────────────────────────────────────────────────────
def backtest(S, start):
    n = len(S.c)
    res = {"down": [0, 0], "up": [0, 0]}      # [signalen, raak]
    base = {"down": [0, 0], "up": [0, 0]}
    plan = {d: {"plan_n": 0, "wins": 0, "losses": 0, "ret_sum": 0.0} for d in ("down", "up")}
    cool = {"down": -99, "up": -99}
    for i in range(start, n - HORIZON):
        fut_lo = min(S.l[i + 1:i + HORIZON + 1])
        fut_hi = max(S.h[i + 1:i + HORIZON + 1])
        hit_dn = fut_lo <= S.c[i] * (1 - DROP_PCT / 100)
        hit_up = fut_hi >= S.c[i] * (1 + EVAL_RISE_PCT / 100)
        base["down"][0] += 1
        base["down"][1] += hit_dn
        base["up"][0] += 1
        base["up"][1] += hit_up
        P, targets = evaluate(S, i)
        s = score_of(P)
        d = "down" if s <= -SIGNAL_SCORE else "up" if s >= SIGNAL_SCORE else None
        if d and i - cool[d] >= HORIZON // 2:   # geen overlappende signalen dubbel tellen
            target, sup, rs, sig = project(S, i, s, targets)
            move = (target / S.c[i] - 1) * 100 if target else 0
            # alleen signalen die het dashboard ook zou tonen: verwachte beweging >= 5%
            if (d == "down" and -move < DROP_PCT) or (d == "up" and move < RISE_PCT):
                continue
            cool[d] = i
            res[d][0] += 1
            res[d][1] += hit_dn if d == "down" else hit_up
            pl = make_plan(S.c[i], d, target, sup, rs, sig)
            if pl:
                uitkomst, ret = plan_outcome(S, i, d, pl)
                plan[d]["plan_n"] += 1
                plan[d]["wins"] += uitkomst == "win"
                plan[d]["losses"] += uitkomst == "loss"
                plan[d]["ret_sum"] += ret * 100
    out = {}
    for d in ("down", "up"):
        out[d] = {"n": res[d][0], "hits": res[d][1], "base_n": base[d][0], "base_hits": base[d][1]}
        out[d].update({k: (round(v, 2) if isinstance(v, float) else v) for k, v in plan[d].items()})
    return out


# ── Hoofdprogramma ───────────────────────────────────────────────────────
def analyse(tk, bars, meta):
    S = Series(bars)
    i = len(S.c) - 1
    P, targets = evaluate(S, i)
    score = score_of(P)
    direction = "down" if score <= -SIGNAL_SCORE else "up" if score >= SIGNAL_SCORE else "flat"
    target, sup, res, sig = project(S, i, score, targets)
    price = S.c[i]
    proj = round((target / price - 1) * 100, 1) if target else None
    flag = bool(proj is not None and (
        (direction == "down" and -proj >= DROP_PCT) or (direction == "up" and proj >= RISE_PCT)))
    plan = make_plan(price, direction, target, sup, res, sig)
    if plan:
        plan = {k: round(v, 4) for k, v in plan.items()}

    # Nieuw signaal? Vergelijk met de vorige handelsdag (slotkoers van gisteren).
    def signal_at(j):
        Pj, tj = evaluate(S, j)
        sj = score_of(Pj)
        dj = "down" if sj <= -SIGNAL_SCORE else "up" if sj >= SIGNAL_SCORE else "flat"
        tgt = project(S, j, sj, tj)[0]
        mv = (tgt / S.c[j] - 1) * 100 if tgt else None
        ok = mv is not None and ((dj == "down" and -mv >= DROP_PCT) or (dj == "up" and mv >= RISE_PCT))
        return dj if ok else None
    prev_signal = signal_at(i - 1) if i > 210 else None
    new_signal = bool(flag and prev_signal != direction)
    prev = meta.get("chartPreviousClose") if i == 0 else S.c[i - 1]
    lo_spark = max(0, len(S.c) - SPARK_BARS)
    r2 = lambda x: round(x, 4) if x is not None else None  # noqa: E731
    P.sort(key=lambda p: -abs(p["w"]))
    return {
        "symbol": tk["symbol"],
        "name": tk.get("name") or meta.get("longName") or meta.get("shortName") or tk["symbol"],
        "label": tk.get("label"),
        "category": tk.get("category"),
        "watch": tk.get("watch", False),
        "position": tk.get("position"),
        "currency": meta.get("currency", ""),
        "price": r2(price),
        "day_pct": round((price / prev - 1) * 100, 2) if prev else None,
        "asof": datetime.fromtimestamp(S.t[i], tz=timezone.utc).isoformat(),
        "score": score,
        "direction": direction,
        "flag": flag,
        "target": r2(target),
        "projected_pct": proj,
        "sigma_pct": round(sig * 100, 1),
        "support": r2(sup),
        "resistance": r2(res),
        "plan": plan,
        "new_signal": new_signal,
        "prev_signal": prev_signal,
        "rsi": round(S.rsi[i], 1) if S.rsi[i] is not None else None,
        "patterns": P,
        "backtest": backtest(S, 210),
        "spark": [round(x, 4) for x in S.c[lo_spark:]],
    }


def main():
    tickers, source = load_tickers()
    tickers, portfolio_version = enrich(tickers)
    print(f"{len(tickers)} tickers uit {source}")
    items, errors = [], []
    cache = load_listing_cache()
    week = datetime.now(timezone.utc).strftime("%Y-%W")
    try:
        fx = eurusd()
    except Exception:  # noqa: BLE001
        fx = None
        print("  EUR/USD niet opgehaald: patronen alleen op de eigen notering")
    for tk in tickers:
        try:
            eu_bars, eu_meta = fetch_bars(tk["symbol"], "2y", 2)
            us = find_us_listing(tk, eu_bars[-1][4], fx, cache) if fx else None
            item = None
            if us:
                try:
                    us_bars, us_meta = fetch_bars(us, US_HISTORY)
                    item = to_euro(analyse(tk, us_bars, us_meta), eu_bars, eu_meta, us)
                except Exception as e:  # noqa: BLE001
                    print(f"  {tk['symbol']:<10} {us} niet bruikbaar ({e}), eigen notering gebruikt")
            if item is None:
                if len(eu_bars) < 60:
                    raise ValueError(f"te weinig koersdata ({len(eu_bars)} dagen)")
                item = analyse(tk, eu_bars, eu_meta)
                item["pattern_symbol"] = None
            item["listing_checked"] = week if us is not None else None
            items.append(item)
            print(f"  {tk['symbol']:<10} score {item['score']:>4}  {item['direction']:<5} "
                  f"{'' if item['projected_pct'] is None else item['projected_pct']:>6}"
                  f"{'  patroon via ' + item['pattern_symbol'] if item.get('pattern_symbol') else ''}")
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": tk["symbol"], "name": tk.get("name"), "label": tk.get("label"),
                           "category": tk.get("category"), "watch": tk.get("watch", False),
                           "position": tk.get("position"), "error": str(e)[:200],
                           "listing_checked": week})
            print(f"  {tk['symbol']:<10} FOUT: {e}")
        time.sleep(0.4)

    agg = {d: {"n": 0, "hits": 0, "base_n": 0, "base_hits": 0,
               "plan_n": 0, "wins": 0, "losses": 0, "ret_sum": 0.0} for d in ("down", "up")}
    for it in items:
        for d in agg:
            for k in agg[d]:
                agg[d][k] += it["backtest"][d][k]

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "portfolio_version": portfolio_version,
        "config": {"horizon": HORIZON, "drop_pct": DROP_PCT, "rise_pct": RISE_PCT,
                   "eval_rise_pct": EVAL_RISE_PCT, "signal_score": SIGNAL_SCORE,
                   "retrace": RETRACE},
        "stats": agg,
        "items": items,
        "errors": errors,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"signals.json geschreven: {len(items)} ok, {len(errors)} fout")
    if not items:
        sys.exit(1)


if __name__ == "__main__":
    main()
