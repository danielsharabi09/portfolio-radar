"""Prices, search and peers from Yahoo Finance's free (unofficial) endpoints,
plus the indicators computed from daily closes. Standard library only."""

import math
import re
import urllib.parse

from .net import get_json

YAHOO = "https://query2.finance.yahoo.com"
CHART_URL = YAHOO + "/v8/finance/chart/%s?range=1y&interval=1d"
SEARCH_URL = YAHOO + "/v1/finance/search?q=%s&quotesCount=10&newsCount=0&listsCount=0"
PEERS_URL = YAHOO + "/v6/finance/recommendationsbysymbol/%s"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def yahoo_symbol(sym):
    """BRK.B -> BRK-B, the way Yahoo spells US share classes. TEVA.TA stays."""
    sym = sym.strip().upper()
    if re.match(r"^[A-Z]{1,5}\.[A-Z]$", sym):
        return sym.replace(".", "-")
    return sym


# ---------------------------------------------------------------- fetch

def fetch_chart(sym, cache):
    url = CHART_URL % urllib.parse.quote(yahoo_symbol(sym))
    doc = get_json(url, cache, ttl=15 * 60, headers={"User-Agent": BROWSER_UA})
    return parse_chart(doc)


def parse_chart(doc):
    """A Yahoo chart or spark response -> {meta, ts, closes}."""
    if "spark" in doc:
        res = doc["spark"]["result"][0]["response"][0]
    else:
        result = (doc.get("chart") or {}).get("result")
        if not result:
            err = ((doc.get("chart") or {}).get("error") or {}).get("description")
            raise LookupError(err or "no chart data")
        res = result[0]
    quote = res["indicators"]["quote"][0]
    ts, closes = [], []
    for t, c in zip(res.get("timestamp") or [], quote.get("close") or []):
        if c is not None:
            ts.append(t)
            closes.append(round(c, 4))
    if not closes:
        raise LookupError("no closes")
    return {"meta": res["meta"], "ts": ts, "closes": closes}


def search(q, cache):
    doc = get_json(SEARCH_URL % urllib.parse.quote(q), cache, ttl=24 * 3600,
                   headers={"User-Agent": BROWSER_UA})
    out = []
    for it in doc.get("quotes", []):
        if it.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        out.append({"symbol": it["symbol"],
                    "name": it.get("longname") or it.get("shortname") or it["symbol"],
                    "exchange": it.get("exchDisp") or it.get("exchange") or "",
                    "type": it.get("quoteType")})
    return out


def peers(sym, cache):
    doc = get_json(PEERS_URL % urllib.parse.quote(yahoo_symbol(sym)), cache, ttl=24 * 3600,
                   headers={"User-Agent": BROWSER_UA})
    result = (doc.get("finance") or {}).get("result") or []
    if not result:
        return []
    return [r["symbol"] for r in result[0].get("recommendedSymbols", [])]


# ---------------------------------------------------------------- indicators

def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def rsi(closes, n=14):
    """Wilder's RSI on daily closes."""
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[:n], closes[1:n + 1]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for a, b in zip(closes[n:], closes[n + 1:]):
        d = b - a
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def ret(closes, bars):
    if len(closes) <= bars:
        return None
    return (closes[-1] / closes[-1 - bars] - 1) * 100


def volatility(closes, bars=60):
    """Annualised stdev of daily returns, in percent."""
    tail = closes[-bars - 1:]
    if len(tail) < 20:
        return None
    rets = [b / a - 1 for a, b in zip(tail, tail[1:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def indicators(chart):
    meta, closes = chart["meta"], chart["closes"]
    price = meta.get("regularMarketPrice") or closes[-1]
    prev = closes[-2] if len(closes) > 1 else meta.get("chartPreviousClose")
    hi = meta.get("fiftyTwoWeekHigh") or max(closes)
    lo = meta.get("fiftyTwoWeekLow") or min(closes)
    ma50 = sma(closes, 50)
    ma50_prev = sma(closes[:-10], 50) if len(closes) >= 60 else None
    return {
        "price": round(price, 2),
        "currency": meta.get("currency") or "USD",
        "name": meta.get("longName") or meta.get("shortName") or meta.get("symbol"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "change_pct": round((closes[-1] / prev - 1) * 100, 2) if prev else None,
        "high_52w": round(hi, 2), "low_52w": round(lo, 2),
        "off_high_pct": round((price / hi - 1) * 100, 1) if hi else None,
        "ma20": _r(sma(closes, 20)), "ma50": _r(ma50), "ma200": _r(sma(closes, 200)),
        "ma50_rising": (ma50 > ma50_prev) if ma50 and ma50_prev else None,
        "rsi14": _r(rsi(closes), 1),
        "ret_1m": _r(ret(closes, 21), 1), "ret_3m": _r(ret(closes, 63), 1),
        "ret_1y": _r(ret(closes, 250), 1),
        "volatility": _r(volatility(closes), 1),
        "bars": len(closes),
        "as_of": chart["ts"][-1] if chart["ts"] else None,
    }


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def compact(chart, points=90):
    """The tail of the series for a sparkline or chart."""
    return {"ts": chart["ts"][-points:], "closes": chart["closes"][-points:]}

