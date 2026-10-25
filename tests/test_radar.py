"""Offline tests on the real snapshots in demo_data/.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radar import earnings, market, score  # noqa: E402
from radar.app import Radar  # noqa: E402
from radar.sources import DemoSource, sec_matches  # noqa: E402

DEMO = os.path.join(ROOT, "demo_data")


class Indicators(unittest.TestCase):
    def test_sma_and_returns(self):
        closes = [float(i) for i in range(1, 101)]
        self.assertEqual(market.sma(closes, 10), 95.5)
        self.assertIsNone(market.sma(closes, 200))
        self.assertAlmostEqual(market.ret(closes, 21), (100 / 79 - 1) * 100)

    def test_rsi_bounds(self):
        self.assertEqual(market.rsi([float(i) for i in range(30)]), 100.0)
        self.assertLess(market.rsi([float(30 - i) for i in range(30)]), 1)
        self.assertIsNone(market.rsi([1.0, 2.0]))

    def test_yahoo_symbol(self):
        self.assertEqual(market.yahoo_symbol("brk.b"), "BRK-B")
        self.assertEqual(market.yahoo_symbol("TEVA.TA"), "TEVA.TA")
        self.assertEqual(market.yahoo_symbol("AAPL"), "AAPL")

    def test_real_snapshot(self):
        src = DemoSource(DEMO)
        ind = market.indicators(src.chart("AAPL"))
        self.assertEqual(ind["price"], 342.94)
        self.assertEqual(ind["high_52w"], 348.15)
        self.assertIsNone(ind["ma200"])          # 65 bars only
        self.assertIsNotNone(ind["ma50"])


class Score(unittest.TestCase):
    def ind(self, **kw):
        base = {"price": 110, "ma50": 100, "ma200": 90, "ma50_rising": True, "ret_3m": 30,
                "off_high_pct": -2, "rsi14": 55}
        base.update(kw)
        return base

    def test_strong_chart_scores_high(self):
        s = score.score(self.ind())
        self.assertEqual(s["value"], 96)   # 67 of 70 points
        self.assertIn("מגמה עולה", s["tags"])

    def test_weak_chart_scores_low(self):
        s = score.score(self.ind(price=60, ma50_rising=False, ret_3m=-30, off_high_pct=-50, rsi14=80))
        self.assertLess(s["value"], 20)
        self.assertEqual(s["label"], "חלש")

    def test_fundamentals_count_when_present(self):
        s = score.score(self.ind(), {"revenue_yoy": 30, "eps_yoy": 40, "op_margin_change": 2})
        self.assertTrue(s["with_fundamentals"])
        self.assertEqual(sum(p["max"] for p in s["parts"]), 100)

    def test_missing_data_normalises(self):
        s = score.score({"price": 10})
        self.assertIsNone(s["value"])


class Earnings(unittest.TestCase):
    def setUp(self):
        self.src = DemoSource(DEMO)

    def test_apple_analysis_and_hebrew_summary(self):
        a = earnings.analyse(self.src.sec, "AAPL")
        self.assertEqual(a["rows"][0]["revenue"], 109417000000)
        self.assertEqual(a["fundamentals"]["revenue_yoy"], 16.4)
        self.assertTrue(a["summary"][0].startswith("רבעון Q3 2026"))
        self.assertIn("+16.4%", a["summary"][0])

    def test_unknown_company(self):
        with self.assertRaises(earnings.EarningsError):
            earnings.analyse(self.src.sec, "TEVA.TA")

    def test_sec_name_search(self):
        ciks = {"KO": ("0000021344", "COCA COLA CO"), "AAPL": ("0000320193", "Apple Inc.")}
        self.assertEqual(sec_matches(ciks, "coca", set())[0]["symbol"], "KO")
        self.assertEqual(sec_matches(ciks, "aapl", set())[0]["symbol"], "AAPL")


class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.radar = Radar(DemoSource(DEMO), os.path.join(self.tmp, "p.json"))

    def test_portfolio_roundtrip_dedupes_and_cleans(self):
        out = self.radar.save_portfolio({"holdings": [
            {"symbol": " aapl ", "shares": "10", "cost": -3}, {"symbol": "AAPL"}, {"symbol": ""}]})
        self.assertEqual(out["holdings"], [{"symbol": "AAPL", "name": "AAPL", "shares": 10.0, "cost": None}])
        self.assertEqual(self.radar.portfolio(), out)

    def test_ideas_exclude_holdings_and_rank_by_votes(self):
        self.radar.save_portfolio({"holdings": [{"symbol": "AAPL"}, {"symbol": "NVDA"}]})
        ideas = self.radar.ideas()["ideas"]
        syms = [i["symbol"] for i in ideas]
        self.assertNotIn("AAPL", syms)
        self.assertEqual(ideas[0], {"symbol": "MSFT", "because": ["AAPL", "NVDA"]})

    def test_earnings_calendar(self):
        self.radar.save_portfolio({"holdings": [{"symbol": "KO"}, {"symbol": "NVDA"}]})
        e = self.radar.earnings(["TSLA"])
        self.assertEqual([(r["symbol"], r["days"], r["held"]) for r in e["upcoming"]],
                         [("KO", 1, True), ("TSLA", 1, False)])   # NVDA is 29 days out

    def test_stock_payload(self):
        s = self.radar.stock("ko")
        self.assertEqual(s["symbol"], "KO")
        self.assertTrue(s["score"]["with_fundamentals"])
        s = self.radar.stock("TSLA")
        self.assertFalse(s["score"]["with_fundamentals"])
        self.assertIn("AAPL, KO", s["fundamentals_error"])


class LiveParsing(unittest.TestCase):
    """The live endpoints' response shapes, with the network replaced."""

    def patch(self, module, doc):
        orig = module.get_json
        module.get_json = lambda *a, **k: doc
        self.addCleanup(setattr, module, "get_json", orig)

    def test_yahoo_search(self):
        self.patch(market, {"quotes": [
            {"symbol": "TEVA.TA", "shortname": "TEVA", "longname": "Teva Pharmaceutical",
             "exchDisp": "Tel Aviv", "quoteType": "EQUITY"},
            {"symbol": "TEVA250117C", "quoteType": "OPTION"}]})
        self.assertEqual(market.search("teva", None),
                         [{"symbol": "TEVA.TA", "name": "Teva Pharmaceutical",
                           "exchange": "Tel Aviv", "type": "EQUITY"}])

    def test_yahoo_peers(self):
        self.patch(market, {"finance": {"result": [{"symbol": "AAPL", "recommendedSymbols": [
            {"symbol": "MSFT", "score": 0.3}, {"symbol": "GOOG", "score": 0.2}]}]}})
        self.assertEqual(market.peers("AAPL", None), ["MSFT", "GOOG"])

    def test_yahoo_chart_error(self):
        self.patch(market, {"chart": {"result": None, "error": {"description": "No data found"}}})
        with self.assertRaises(LookupError):
            market.fetch_chart("ZZZZ", None)

    def test_nasdaq_calendar(self):
        import datetime as dt
        self.patch(earnings, {"data": {"rows": [
            {"symbol": "KO", "time": "time-pre-market", "epsForecast": "$0.78", "noOfEsts": "11",
             "fiscalQuarterEnding": "Sep/2026", "lastYearEPS": "$0.77"},
            {"symbol": "BRK-B", "time": "time-not-supplied"}]}})
        found, errors = earnings.calendar(["KO", "BRK.B", "AAPL"], None, dt.date(2026, 10, 20), days=0)
        self.assertEqual(errors, [])
        self.assertEqual(found["KO"]["time"], "לפני הפתיחה")
        self.assertEqual(found["KO"]["eps_forecast"], "$0.78")
        self.assertIn("BRK.B", found)
        self.assertNotIn("AAPL", found)


class Server(unittest.TestCase):
    def test_routes(self):
        import server
        from http.server import ThreadingHTTPServer
        radar = Radar(DemoSource(DEMO), os.path.join(tempfile.mkdtemp(), "p.json"))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(radar))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        try:
            get = lambda p: json.load(urllib.request.urlopen(base + p))
            self.assertEqual(get("/api/status")["mode"], "demo")
            self.assertEqual(get("/api/search?q=coca")["results"][0]["symbol"], "KO")
            self.assertEqual(get("/api/stock?symbol=AAPL")["quote"]["price"], 342.94)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/stock?symbol=../../etc")
            self.assertEqual(cm.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/../server.py")
            self.assertIn(b"Portfolio Radar", urllib.request.urlopen(base + "/").read())
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
