"""Fetch live index levels from Yahoo Finance and publish docs/indexes-live.json.

Runs on GitHub Actions (see .github/workflows/update.yml). Personal, non-commercial use.
Cycle highs are maintained by merging 5 years of daily closes with the highs from the
Bull/Bear template workbook (daily data to Oct 2021), so the strict-definition rolling
high is continuous across both sources.
"""
import json, time, datetime, pathlib, urllib.parse
import requests

SYMBOLS = {
 "S&P 500": "^GSPC",
 "Nasdaq 100": "^NDX",
 "Nasdaq Composite": "^IXIC",
 "DJIA": "^DJI",
 "Eurostoxx 50": "^STOXX50E",
 "Eurostoxx 600": "^STOXX",
 "FTSE 100": "^FTSE",
 "FTSE 250": "^FTMC",
 "FTSE 350": "^FTLC",
 "DAX 40": "^GDAXI",
 "HSI": "^HSI",
 "HSCEI": "^HSCE",
 "Nikkei 225": "^N225",
 "ASX 200": "^AXJO"
}

# Rolling highs from the Bull/Bear template workbook (through Oct 2021)
TEMPLATE_HIGHS = {
 "S&P 500": 4536.95,
 "Nasdaq 100": 15675.76,
 "Nasdaq Composite": 15374.33,
 "DJIA": 35625.4,
 "Eurostoxx 50": 5464.43,
 "Eurostoxx 600": 475.83,
 "FTSE 100": 7877.45,
 "FTSE 250": 24250.83,
 "FTSE 350": 4381.12,
 "DAX 40": 15977.44,
 "HSI": 33154.12,
 "HSCEI": 20400.07,
 "Nikkei 225": 38915.87,
 "ASX 200": 7628.9
}

HEADERS = {"User-Agent": "Mozilla/5.0 (personal market-regime feed)"}

def fetch(sym):
    q = urllib.parse.quote(sym)
    for host in ("query1", "query2"):
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}",
                    params={"range": "5y", "interval": "1d"},
                    headers=HEADERS, timeout=20)
                if r.status_code == 200:
                    return r.json()["chart"]["result"][0]
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
    return None

out = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "source": "Yahoo Finance (personal use)", "indexes": {}}

for name, sym in SYMBOLS.items():
    res = fetch(sym)
    if not res:
        print(f"FAILED: {name} ({sym})")
        continue
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    if not closes:
        print(f"NO DATA: {name}")
        continue
    meta = res.get("meta", {})
    now = meta.get("regularMarketPrice") or closes[-1]
    high = max(TEMPLATE_HIGHS.get(name, 0), max(closes), now)
    trig = high * 0.8
    rec = {"symbol": sym, "now": round(now, 2), "high": round(high, 2),
           "trigger": round(trig, 2), "dd": round((high - now) / high * 100, 2),
           "regime": "Bull" if now >= trig else "Bear",
           "asof": datetime.datetime.fromtimestamp(
               meta.get("regularMarketTime", time.time()),
               datetime.timezone.utc).date().isoformat()}
    if name == "S&P 500" and len(closes) >= 253:
        rec["ago12"] = round(closes[-253], 2)
    out["indexes"][name] = rec
    print(f"OK: {name} = {rec['now']} (high {rec['high']}, {rec['regime']})")
    time.sleep(1)

if len(out["indexes"]) < 10:
    raise SystemExit("Too few indexes fetched - keeping the last good feed published.")

pathlib.Path("docs").mkdir(exist_ok=True)
with open("docs/indexes-live.json", "w") as f:
    json.dump(out, f, indent=1)
print(f"Wrote docs/indexes-live.json with {len(out['indexes'])} indexes")
