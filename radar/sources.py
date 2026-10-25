"""Where the data comes from. LiveSource reads the free public endpoints;
DemoSource reads the real snapshots in demo_data/ (prices as of 2026-10-20,
Apple's and Coca-Cola's SEC filings) so the app runs with no network."""

import datetime as dt
import json
import os

from . import earnings, market
from .net import FetchError


class LiveSource(object):
    mode = "live"

    def __init__(self, cache, user_agent):
        self.cache = cache
        self.sec = earnings.Sec(cache, user_agent)

    def today(self):
        return dt.date.today()

    def search(self, q):
        out, seen = [], set()
        try:
            for it in market.search(q, self.cache):
                out.append(it)
                seen.add(it["symbol"].upper())
        except FetchError:
            pass
        # SEC's list covers every US filer, and matches company names well.
        try:
            out += sec_matches(self.sec.ciks(), q, seen)
        except earnings.EarningsError:
            pass
        return out[:12]

    def chart(self, sym):
        return market.fetch_chart(sym, self.cache)

    def peers(self, sym):
        return market.peers(sym, self.cache)

    def upcoming(self, symbols):
        return earnings.calendar(symbols, self.cache, self.today())


def sec_matches(ciks, q, seen):
    q = q.strip().upper()
    if not q:
        return []
    exact, starts, contains = [], [], []
    for sym, (_, name) in ciks.items():
        if sym in seen:
            continue
        uname = (name or "").upper()
        row = {"symbol": sym, "name": name or sym, "exchange": "SEC", "type": "EQUITY"}
        if sym == q:
            exact.append(row)
        elif uname.startswith(q) or sym.startswith(q):
            starts.append(row)
        elif len(q) >= 3 and q in uname:
            contains.append(row)
    return exact + sorted(starts, key=lambda r: len(r["symbol"]))[:6] + contains[:4]


class _DemoSec(earnings.Sec):
    """SEC reads served from demo_data/."""

    def __init__(self, folder):
        earnings.Sec.__init__(self, None, "demo")
        self.folder = folder

    def _file(self, name):
        path = os.path.join(self.folder, name)
        if not os.path.isfile(path):
            raise earnings.EarningsError("אין נתוני SEC לדמו עבור החברה הזו (בדמו: AAPL, KO)")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def ciks(self):
        if self._ciks is None:
            self._ciks = {}
            for v in self._file("company_tickers.json").values():
                self._ciks[v["ticker"]] = (str(v["cik_str"]).zfill(10), v["title"])
        return self._ciks

    def cik(self, sym):
        hit = self.ciks().get(sym.upper())
        if not hit or not os.path.isfile(os.path.join(self.folder, "companyfacts-CIK%s.json" % hit[0])):
            raise earnings.EarningsError("אין נתוני SEC לדמו עבור החברה הזו (בדמו: AAPL, KO)")
        return hit

    def companyfacts(self, cik):
        return self._file("companyfacts-CIK%s.json" % cik)

    def submissions(self, cik):
        return self._file("submissions-CIK%s.json" % cik)


# Peers the way Yahoo's "people also watch" would list them, for the demo.
DEMO_PEERS = {
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META"], "MSFT": ["AAPL", "GOOGL", "AMZN", "NVDA"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL"], "META": ["GOOGL", "AMZN", "MSFT", "AAPL"],
    "AMZN": ["MSFT", "GOOGL", "AAPL", "META"], "NVDA": ["AMD", "INTC", "MSFT", "PLTR"],
    "AMD": ["NVDA", "INTC", "MSFT", "PLTR"], "INTC": ["AMD", "NVDA", "MSFT", "AAPL"],
    "TSLA": ["RIVN", "LCID", "F", "NVDA"], "RIVN": ["LCID", "TSLA", "F", "PLUG"],
    "LCID": ["RIVN", "TSLA", "PLUG", "F"], "F": ["TSLA", "RIVN", "LCID", "BAC"],
    "KO": ["BRK-B", "BAC", "AAPL", "F"], "BAC": ["BRK-B", "SOFI", "KO", "F"],
    "BRK-B": ["BAC", "KO", "AAPL", "MSFT"], "SOFI": ["BAC", "PLTR", "AMC", "LCID"],
    "PLTR": ["NVDA", "SOFI", "AMD", "MSFT"], "PLUG": ["LCID", "RIVN", "AMC", "SOFI"],
    "AMC": ["SOFI", "PLUG", "LCID", "F"],
}

# Days after the demo date; marked as demo dates in the page.
DEMO_EARNINGS = {
    "TSLA": (1, "אחרי הסגירה"), "KO": (1, "לפני הפתיחה"), "INTC": (2, "אחרי הסגירה"),
    "SOFI": (6, "לפני הפתיחה"), "F": (7, "אחרי הסגירה"), "GOOGL": (8, "אחרי הסגירה"),
    "MSFT": (8, "אחרי הסגירה"), "META": (8, "אחרי הסגירה"), "AAPL": (9, "אחרי הסגירה"),
    "AMZN": (9, "אחרי הסגירה"), "BRK-B": (12, "לפני הפתיחה"), "PLTR": (13, "אחרי הסגירה"),
    "AMD": (14, "אחרי הסגירה"), "RIVN": (15, "אחרי הסגירה"), "AMC": (15, "אחרי הסגירה"),
    "LCID": (16, "אחרי הסגירה"), "PLUG": (21, "לפני הפתיחה"), "NVDA": (29, "אחרי הסגירה"),
}


class DemoSource(object):
    mode = "demo"
    DAY = dt.date(2026, 10, 20)

    def __init__(self, folder):
        with open(os.path.join(folder, "charts.json"), encoding="utf-8") as fh:
            spark = json.load(fh)["spark"]["result"]
        self.charts = {}
        for r in spark:
            self.charts[r["symbol"]] = market.parse_chart({"spark": {"result": [r]}})
        self.sec = _DemoSec(folder)

    def today(self):
        return self.DAY

    def _key(self, sym):
        return market.yahoo_symbol(sym)

    def search(self, q):
        q = q.strip().upper()
        out = []
        for sym, c in self.charts.items():
            name = c["meta"].get("longName") or c["meta"].get("shortName") or sym
            if sym.startswith(q) or q in name.upper():
                out.append({"symbol": sym, "name": name,
                            "exchange": c["meta"].get("fullExchangeName", ""), "type": "EQUITY"})
        return sorted(out, key=lambda r: (not r["symbol"].startswith(q), r["symbol"]))[:10]

    def chart(self, sym):
        c = self.charts.get(self._key(sym))
        if not c:
            raise LookupError("בדמו יש רק: " + ", ".join(sorted(self.charts)))
        return c

    def peers(self, sym):
        return DEMO_PEERS.get(self._key(sym), [])

    def upcoming(self, symbols):
        out = {}
        for s in symbols:
            hit = DEMO_EARNINGS.get(self._key(s))
            if hit and hit[0] <= 28:
                out[s.upper()] = {"date": (self.DAY + dt.timedelta(days=hit[0])).isoformat(),
                                  "time": hit[1], "source": "דמו", "estimated": False,
                                  "eps_forecast": None, "estimates": None,
                                  "last_year_eps": None, "quarter": None}
        return out, []
