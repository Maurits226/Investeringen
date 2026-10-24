#!/usr/bin/env python3
"""
Haalt koersdata op bij Yahoo Finance voor de tickers in tickers.txt
en schrijft het resultaat naar data.json.
Draait op GitHub Actions (server-side, dus geen CORS-probleem).
"""
import json
import re
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

TICKERS_FILE = "tickers.txt"
OUTPUT_FILE = "data.json"
TICKER_OK = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")      # zelfde regel als het dashboard (shared.js)

def read_tickers():
    """Lees tickers uit tickers.txt, één per regel, negeer lege regels en # commentaar.
    Alleen het eerste woord telt: 'NVDA, NVIDIA, Watchlist  # via dashboard' wordt NVDA."""
    tickers = []
    try:
        with open(TICKERS_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                t = re.split(r"[\s,;|#]", line.strip())[0].upper()
                if t and not TICKER_OK.match(t):
                    print(f"Overgeslagen, geen geldige ticker: {t[:30]!r}")
                elif t and t not in tickers:
                    tickers.append(t)
    except FileNotFoundError:
        print(f"{TICKERS_FILE} niet gevonden, gebruik standaard AVGO.DE")
        tickers = ["AVGO.DE"]
    return tickers

def fetch_one(ticker):
    """Haal koersdata voor één ticker op bij Yahoo Finance."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?range=1y&interval=1d"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36"
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = data["chart"]["result"][0]
    meta = result["meta"]

    closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
    high52 = meta.get("fiftyTwoWeekHigh") or (max(closes) if closes else None)
    low52 = meta.get("fiftyTwoWeekLow") or (min(closes) if closes else None)

    # Slotkoers van de vorige handelsdag (voor de dagbeweging).
    # 'chartPreviousClose' is bij range=1y de koers van een jaar geleden, dus
    # die gebruiken we niet. We halen hem uit de dagreeks: is de laatste dag in
    # de reeks de huidige handelsdag, dan is de dag ervoor de vorige slotkoers.
    stamps = result.get("timestamp", []) or []
    raw = result["indicators"]["quote"][0].get("close", []) or []
    pairs = [(t, c) for t, c in zip(stamps, raw) if c is not None]
    gmt = meta.get("gmtoffset", 0) or 0
    def dag(ts):
        return datetime.fromtimestamp(ts + gmt, timezone.utc).date()
    prev_close = None
    if pairs:
        last_t, last_c = pairs[-1]
        rmt = meta.get("regularMarketTime")
        if rmt and dag(last_t) == dag(rmt):
            prev_close = pairs[-2][1] if len(pairs) >= 2 else None
        else:
            prev_close = last_c

    # Beweeglijkheid (jaarbasis) en trend: voor slimme koopdrempels en de trendcheck in het dashboard
    vol = None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a and b]
    if len(rets) >= 60:
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    sma = lambda n: (sum(closes[-n:]) / n) if len(closes) >= n else None  # noqa: E731

    # Laagste slotkoers per week, een jaar terug ('from' = maandag van de eerste week, een week zonder handel is null).
    # Daarmee ziet het dashboard of de koers na een verkoop 25% is gezakt: dan gaat die winsttrede weer open,
    # ook als je het dashboard in die tijd niet opende.
    lows = {}
    for t, c in pairs:
        d = dag(t)
        m = d - timedelta(days=d.weekday())
        lows[m] = min(lows.get(m, c), c)
    wk = None
    if lows:
        w0 = min(lows)
        weeks = (w0 + timedelta(weeks=i) for i in range((max(lows) - w0).days // 7 + 1))
        wk = {"from": w0.isoformat(), "low": [float(f"{lows[w]:.5g}") if w in lows else None for w in weeks]}

    return {
        "ticker": ticker,
        "price": meta.get("regularMarketPrice"),
        "prevClose": prev_close,
        "currency": meta.get("currency", "USD"),
        "name": clean(meta.get("longName") or meta.get("shortName")) or ticker,
        "high52": high52,
        "vol": round(vol, 1) if vol else None,
        "sma50": round(sma(50), 4) if sma(50) else None,
        "sma200": round(sma(200), 4) if sma(200) else None,
        "low52": low52,
        "wk": wk,
        "exchange": clean(meta.get("exchangeName")),
        "exchangeFull": clean(meta.get("fullExchangeName")),
    }

def clean(s, n=120):
    """Tekst van de databron zonder HTML-tekens of stuurtekens (de pagina's escapen ook, dit is een extra slot)."""
    if not isinstance(s, str):
        return None
    s = "".join(ch for ch in s if ch.isprintable()).replace("<", "").replace(">", "").replace('"', "").strip()
    return s[:n] or None


def main():
    tickers = read_tickers()
    print(f"Tickers: {tickers}")
    quotes = {}
    for t in tickers:
        for attempt in range(3):
            try:
                quotes[t] = fetch_one(t)
                print(f"OK: {t} = {quotes[t]['price']} {quotes[t]['currency']}")
                break
            except Exception as e:
                print(f"Poging {attempt+1} mislukt voor {t}: {e}")
                time.sleep(2)
        else:
            print(f"FOUT: {t} kon niet worden opgehaald")
        time.sleep(1)  # vriendelijk blijven tegen Yahoo

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "quotes": quotes,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))      # compact: wordt elke 10 min opgehaald
    print(f"Geschreven naar {OUTPUT_FILE}: {len(quotes)} tickers")

if __name__ == "__main__":
    main()
