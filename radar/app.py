"""What the page asks for: the portfolio, one stock's card, ideas, the
earnings calendar and an earnings analysis. Each function returns plain
JSON-ready dicts; server.py only routes."""

import datetime as dt
import json
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from . import earnings, market, score
from .net import FetchError

ERRORS = (FetchError, LookupError, earnings.EarningsError, KeyError, ValueError)


class Radar(object):
    def __init__(self, source, portfolio_path):
        self.src = source
        self.portfolio_path = portfolio_path
        self._lock = threading.Lock()
        self._analyses = {}

    # ------------------------------------------------------------ portfolio

    def portfolio(self):
        try:
            with open(self.portfolio_path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = {"holdings": []}
        return {"holdings": doc.get("holdings", [])}

    def save_portfolio(self, doc):
        holdings, seen = [], set()
        for h in doc.get("holdings", []):
            sym = str(h.get("symbol", "")).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            holdings.append({"symbol": sym, "name": str(h.get("name") or sym)[:120],
                             "shares": _num(h.get("shares")), "cost": _num(h.get("cost"))})
        os.makedirs(os.path.dirname(self.portfolio_path) or ".", exist_ok=True)
        tmp = self.portfolio_path + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"holdings": holdings}, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.portfolio_path)
        return {"holdings": holdings}

    def symbols(self):
        return [h["symbol"] for h in self.portfolio()["holdings"]]

    # ------------------------------------------------------------ one stock

    def analysis(self, sym):
        sym = sym.upper()
        if sym not in self._analyses:
            self._analyses[sym] = earnings.analyse(self.src.sec, sym)
        return self._analyses[sym]

    def stock(self, sym):
        sym = sym.strip().upper()
        chart = self.src.chart(sym)
        ind = market.indicators(chart)
        fund, fund_error = None, None
        try:
            fund = self.analysis(sym)["fundamentals"]
        except ERRORS as exc:
            fund_error = str(exc)
        return {"symbol": sym, "quote": ind, "score": score.score(ind, fund),
                "fundamentals": fund, "fundamentals_error": fund_error,
                "series": market.compact(chart, 260)}

    # ------------------------------------------------------------ ideas

    def ideas(self, limit=12):
        held = self.symbols()
        votes = OrderedDict()
        with ThreadPoolExecutor(4) as pool:
            lists = list(pool.map(self._peers_safe, held))
        for owner, peers in zip(held, lists):
            for p in peers:
                p = p.upper()
                if p in held or market.yahoo_symbol(p) in [market.yahoo_symbol(h) for h in held]:
                    continue
                votes.setdefault(p, []).append(owner)
        ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))[:limit]
        return {"ideas": [{"symbol": s, "because": by} for s, by in ranked]}

    def _peers_safe(self, sym):
        try:
            return self.src.peers(sym)
        except ERRORS:
            return []

    # ------------------------------------------------------------ earnings

    def earnings(self, extra=()):
        held = self.symbols()
        want = list(OrderedDict.fromkeys(held + [s.upper() for s in extra]))
        today = self.src.today()
        found, errors = self.src.upcoming(want)
        upcoming = []
        for sym in want:
            row = found.get(sym) or found.get(sym.replace("-", "."))
            if row is None and sym in held and self.src.mode == "live":
                try:
                    row = earnings.estimate_from_sec(self.src.sec, sym, today)
                except ERRORS:
                    row = None
            if row:
                d = dt.date.fromisoformat(row["date"])
                upcoming.append(dict(row, symbol=sym, held=sym in held,
                                     days=(d - today).days))
        upcoming.sort(key=lambda r: (r["date"], not r["held"]))
        recent = []
        for sym in held:
            try:
                rep = earnings.recent_reports(self.src.sec, sym, today)
            except ERRORS:
                rep = None
            if rep:
                recent.append(dict(rep, symbol=sym))
        return {"today": today.isoformat(), "upcoming": upcoming, "recent": recent,
                "errors": errors[:3]}


def _num(v):
    try:
        v = float(v)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None
