#!/usr/bin/env python3
"""
Haalt koersdata op bij Yahoo Finance voor de tickers in tickers.txt
en schrijft het resultaat naar data.json.
Draait op GitHub Actions (server-side, dus geen CORS-probleem).
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

TICKERS_FILE = "tickers.txt"
OUTPUT_FILE = "data.json"

def read_tickers():
    """Lees tickers uit tickers.txt, één per regel, negeer lege regels en # commentaar."""
    tickers = []
    try:
        with open(TICKERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tickers.append(line.upper())
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

    return {
        "ticker": ticker,
        "price": meta.get("regularMarketPrice"),
        "prevClose": prev_close,
        "currency": meta.get("currency", "USD"),
        "name": meta.get("longName") or meta.get("shortName") or ticker,
        "high52": high52,
        "low52": low52,
        "exchange": meta.get("exchangeName"),
        "exchangeFull": meta.get("fullExchangeName"),
    }

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
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Geschreven naar {OUTPUT_FILE}: {len(quotes)} tickers")

if __name__ == "__main__":
    main()
