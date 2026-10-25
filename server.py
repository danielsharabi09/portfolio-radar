#!/usr/bin/env python3
"""Portfolio Radar: a local web page for your portfolio, similar-stock ideas,
upcoming earnings and SEC earnings analysis. Standard library only.

    python3 server.py            # live data (Yahoo, Nasdaq, SEC)
    python3 server.py --demo     # real snapshot data, no network needed
    python3 server.py --port 9000 --no-browser

Then open http://localhost:8765. Not financial advice.
"""

import argparse
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from radar import earnings
from radar.app import ERRORS, Radar
from radar.net import Cache
from radar.sources import DemoSource, LiveSource

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


def make_handler(radar):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            routes = {
                "/api/status": lambda: {"mode": radar.src.mode, "today": radar.src.today().isoformat(),
                                        "sec_ready": bool(radar.src.sec.ua)},
                "/api/portfolio": radar.portfolio,
                "/api/search": lambda: {"results": radar.src.search(q.get("q", ""))},
                "/api/stock": lambda: radar.stock(_symbol(q)),
                "/api/ideas": radar.ideas,
                "/api/earnings": lambda: radar.earnings(
                    [s for s in q.get("extra", "").split(",") if s.strip()][:20]),
                "/api/analysis": lambda: radar.analysis(_symbol(q)),
            }
            if url.path in routes:
                try:
                    return self._send(200, routes[url.path]())
                except ERRORS as exc:
                    return self._send(404, {"error": str(exc)})
            return self._static(url.path)

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != "/api/portfolio":
                return self._send(404, {"error": "not found"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
                doc = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            return self._send(200, radar.save_portfolio(doc))

        def _static(self, path):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.realpath(os.path.join(WEB, name))
            if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
                return self._send(404, {"error": "not found"})
            with open(full, "rb") as fh:
                self._send(200, fh.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    return Handler


def _symbol(q):
    sym = q.get("symbol", "").strip().upper()
    if not sym or len(sym) > 12 or not all(c.isalnum() or c in ".-^=" for c in sym):
        raise ValueError("bad symbol")
    return sym


def build(demo, config_path):
    data = os.path.join(ROOT, "data")
    if demo:
        return Radar(DemoSource(os.path.join(ROOT, "demo_data")),
                     os.path.join(data, "portfolio.demo.json"))
    ua = earnings.config_user_agent(config_path)
    return Radar(LiveSource(Cache(os.path.join(data, "cache")), ua),
                 os.path.join(data, "portfolio.json"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="snapshot data, no network")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    radar = build(a.demo, a.config)
    if radar.src.mode == "demo" and not os.path.isfile(radar.portfolio_path):
        radar.save_portfolio({"holdings": [
            {"symbol": "AAPL", "name": "Apple Inc.", "shares": 10, "cost": 210},
            {"symbol": "KO", "name": "The Coca-Cola Company", "shares": 25, "cost": 66},
            {"symbol": "NVDA", "name": "NVIDIA Corporation", "shares": 8, "cost": 150},
            {"symbol": "TSLA", "name": "Tesla, Inc.", "shares": 3, "cost": 330}]})
    if radar.src.mode == "live" and not radar.src.sec.ua:
        print("note: no sec_user_agent in config.json, so earnings analysis is off "
              "(copy config.example.json to config.json and add your name and email)")
    server = ThreadingHTTPServer((a.host, a.port), make_handler(radar))
    url = "http://%s:%d" % ("localhost" if a.host in ("127.0.0.1", "0.0.0.0") else a.host, a.port)
    print("Portfolio Radar (%s) on %s  (Ctrl+C to stop)" % (radar.src.mode, url))
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
