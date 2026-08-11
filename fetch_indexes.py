"""Fetch live index levels + multi-market top-down analysis; publish docs/indexes-live.json.

Runs on GitHub Actions. Personal, non-commercial use.

Part 1 - Index regime feed (strict bull/bear definition, cycle highs merged with the
Bull/Bear template workbook, data to Oct 2021).

Part 2 - Top-down analysis per market (methodology from the ITPM statistics guide):
 - Quadnomial scoring: quarterly GDP direction vs index quarterly direction lagged
   6 months (index leads GDP by 2 quarters): (1,1) both up, (0,0) both down,
   (0,1) index down/GDP up, (1,0) index up/GDP down.
 - YoY layer: index YoY vs GDP YoY, Pearson correlation at 0/3/6/9/12-month lags,
   rolling correlation (10yr US, 5yr others), and the 6-month-lag scatter data.
Markets: S&P 500 vs US Real GDP (FRED GDPC1, from 1950), STOXX 600 vs Euro Area
Real GDP (FRED CLVMNACSCAB1GQEA19, from 1995), and Shenzhen Composite vs China
GDP (FRED CHNGDPNQDSMEI, from 1992) when available.
Note: FRED publishes revised GDP series, so counts can differ slightly from
tables built on first-reported (vintage) figures.
"""
import csv, io, json, math, time, datetime, pathlib, urllib.parse
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

MARKETS = {
 "US":     {"index": "S&P 500",            "symbol": "^GSPC",    "gdpSeries": "GDPC1",
            "gdpName": "US Real GDP",        "start": [1950, 3], "window": 40},
 "Europe": {"index": "STOXX 600",          "symbol": "^STOXX",   "gdpSeries": "CLVMNACSCAB1GQEA19",
            "gdpName": "Euro Area Real GDP", "start": [1995, 3], "window": 20},
 "China":  {"index": "Shenzhen Composite", "symbol": "399106.SZ","gdpSeries": "CHNGDPNQDSMEI",
            "gdpName": "China GDP",          "start": [1992, 3], "window": 20},
}

HEADERS = {"User-Agent": "Mozilla/5.0 (personal market-regime feed)"}
UTC = datetime.timezone.utc

def yahoo_chart(sym, rng, interval):
    q = urllib.parse.quote(sym)
    for host in ("query1", "query2"):
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}",
                    params={"range": rng, "interval": interval},
                    headers=HEADERS, timeout=25)
                if r.status_code == 200:
                    return r.json()["chart"]["result"][0]
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
    return None

def yahoo_quarterly_closes(sym):
    res = yahoo_chart(sym, "max", "1mo")
    if not res:
        return None
    q = {}
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        d = datetime.datetime.fromtimestamp(t, UTC)
        q[(d.year, (d.month - 1) // 3 + 1)] = c   # last close seen in each quarter
    return q

def fred_quarterly(series_id):
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                     params={"id": series_id}, headers=HEADERS, timeout=25)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            d = datetime.date.fromisoformat(row.get("observation_date") or row.get("DATE"))
            out[(d.year, (d.month - 1) // 3 + 1)] = float(row[series_id])
        except (KeyError, TypeError, ValueError):
            continue
    return out

def prevq(y, q):  return (y - 1, 4) if q == 1 else (y, q - 1)
def addq(y, q, n):
    t = y * 4 + (q - 1) + n
    return (t // 4, t % 4 + 1)
def qlabel(y, q): return f"{y}Q{q}"

def pearson(pairs):
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    n = len(xs)
    if n < 8:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)

def build_market(spq, gdpq, start, window):
    start = tuple(start)
    spdir = {k: (1 if v > spq[prevq(*k)] else 0) for k, v in spq.items() if prevq(*k) in spq}
    gdir  = {k: (1 if v > gdpq[prevq(*k)] else 0) for k, v in gdpq.items() if prevq(*k) in gdpq}
    quarters = []
    for k in sorted(gdir):
        if k < start:
            continue
        sk = addq(*k, -2)
        if sk in spdir:
            quarters.append([qlabel(*k), spdir[sk], gdir[k]])
    latest = max(gdir)
    pending = []
    for k in sorted(spdir):
        tgt = addq(*k, 2)
        if tgt > latest and k > addq(*latest, -3):
            pending.append([qlabel(*k), spdir[k], qlabel(*tgt)])
    # YoY layer
    spy = {k: spq[k] / spq[addq(*k, -4)] - 1 for k in spq if addq(*k, -4) in spq}
    gy  = {k: gdpq[k] / gdpq[addq(*k, -4)] - 1 for k in gdpq if addq(*k, -4) in gdpq}
    Q = [k for k in sorted(gy) if k >= start]
    labels = [qlabel(*k) for k in Q]
    lagCorr = {}; rollSeries = {}
    for lagq in (0, 1, 2, 3, 4):
        aligned = [(gy[k], spy.get(addq(*k, -lagq))) for k in Q]
        full = [(s, g) for g, s in aligned if s is not None]
        lc = pearson(full)
        lagCorr[str(lagq * 3)] = round(lc, 3) if lc is not None else None
        roll = []
        for i in range(len(Q)):
            if i + 1 < window:
                roll.append(None)
                continue
            win = [(s, g) for g, s in aligned[i + 1 - window:i + 1] if s is not None]
            r = pearson(win) if len(win) >= int(window * 0.8) else None
            roll.append(round(r, 3) if r is not None else None)
        rollSeries[str(lagq * 3)] = roll
    yoy6 = [[qlabel(*k), round(spy[addq(*k, -2)], 4), round(gy[k], 4)]
            for k in Q if addq(*k, -2) in spy]
    return {"quarters": quarters, "pending": pending, "gdpLatest": qlabel(*latest),
            "lagCorr": lagCorr,
            "rolling": {"window": window, "labels": labels, "series": rollSeries},
            "yoy": yoy6}

# ---------- Part 1: index regimes ----------
out = {"updated": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
       "source": "Yahoo Finance + FRED (personal use)", "indexes": {}}

for name, sym in SYMBOLS.items():
    res = yahoo_chart(sym, "5y", "1d")
    if not res:
        print(f"FAILED: {name} ({sym})")
        continue
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    if not closes:
        continue
    meta = res.get("meta", {})
    now = meta.get("regularMarketPrice") or closes[-1]
    high = max(TEMPLATE_HIGHS.get(name, 0), max(closes), now)
    rec = {"symbol": sym, "now": round(now, 2), "high": round(high, 2),
           "trigger": round(high * 0.8, 2), "dd": round((high - now) / high * 100, 2),
           "regime": "Bull" if now >= high * 0.8 else "Bear",
           "asof": datetime.datetime.fromtimestamp(
               meta.get("regularMarketTime", time.time()), UTC).date().isoformat()}
    if name == "S&P 500" and len(closes) >= 253:
        rec["ago12"] = round(closes[-253], 2)
    out["indexes"][name] = rec
    print(f"OK: {name} = {rec['now']} ({rec['regime']})")
    time.sleep(1)

if len(out["indexes"]) < 10:
    raise SystemExit("Too few indexes fetched - keeping the last good feed published.")

# ---------- Part 2: multi-market top-down ----------
markets = {}
for mname, cfg in MARKETS.items():
    try:
        spq = yahoo_quarterly_closes(cfg["symbol"])
        gdpq = fred_quarterly(cfg["gdpSeries"])
        if not spq or not gdpq:
            raise ValueError("missing series")
        m = build_market(spq, gdpq, cfg["start"], cfg["window"])
        m["index"] = cfg["index"]; m["gdpName"] = cfg["gdpName"]
        markets[mname] = m
        print(f"Top-down {mname}: {len(m['quarters'])} quarters to {m['gdpLatest']}, "
              f"6mo-lag r={m['lagCorr'].get('6')}")
    except Exception as e:
        print(f"Top-down {mname} failed ({e}) - skipping this market.")
    time.sleep(1)

if markets:
    out["topdown"] = {"markets": markets,
                      "note": "revised GDP series (FRED); index leads GDP by the stated lag"}
    if "US" in markets:  # backward compatibility with earlier tool versions
        for key in ("quarters", "pending", "gdpLatest"):
            out["topdown"][key] = markets["US"][key]

pathlib.Path("docs").mkdir(exist_ok=True)
with open("docs/indexes-live.json", "w") as f:
    json.dump(out, f, separators=(",", ":"))
print("Wrote docs/indexes-live.json")
