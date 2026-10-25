"""Earnings: when the next report is (Nasdaq's public calendar, else an
estimate from SEC filing history) and what the last ones said (SEC XBRL
company facts, read by sec_facts.py from the Day Trader skill pack)."""

import datetime as dt
import os

from . import sec_facts
from .net import FetchError, get_json

NASDAQ_CAL = "https://api.nasdaq.com/api/calendar/earnings?date=%s"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
TIME_HE = {"time-pre-market": "לפני הפתיחה", "time-after-hours": "אחרי הסגירה",
           "time-not-supplied": "שעה לא ידועה"}


class EarningsError(Exception):
    pass


# ---------------------------------------------------------------- SEC

class Sec(object):
    """SEC EDGAR with the on-disk cache. SEC asks for a name and email."""

    def __init__(self, cache, user_agent):
        self.cache, self.ua = cache, user_agent
        self._ciks = None

    def _get(self, url, ttl):
        if not self.ua:
            raise EarningsError("חסר sec_user_agent ב-config.json (שם ואימייל), SEC דורש אותו")
        try:
            return get_json(url, self.cache, ttl, headers={"User-Agent": self.ua}, gap=0.15)
        except FetchError as exc:
            raise EarningsError(str(exc))

    def ciks(self):
        if self._ciks is None:
            data = self._get(sec_facts.TICKERS_URL, 7 * 86400)
            self._ciks = {}
            for v in data.values():
                self._ciks.setdefault(v["ticker"].upper().replace("-", "."),
                                      (str(v["cik_str"]).zfill(10), sec_facts.clean_title(v.get("title"))))
        return self._ciks

    def cik(self, sym):
        hit = self.ciks().get(sym.upper().replace("-", "."))
        if not hit:
            raise EarningsError("%s אינה מדווחת ל-SEC (מניה זרה או ETF?)" % sym)
        return hit

    def companyfacts(self, cik):
        return self._get(sec_facts.FACTS_URL % cik, 12 * 3600)

    def submissions(self, cik):
        return self._get(sec_facts.SUBMISSIONS_URL % cik, 6 * 3600)


# ---------------------------------------------------------------- analysis

def _year_ago(rows, end):
    target = dt.date.fromisoformat(end) - dt.timedelta(days=364)
    for r in rows:
        if abs((dt.date.fromisoformat(r["end"]) - target).days) <= 12:
            return r
    return None


def money(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return "$%.2fB" % (v / 1e9)
    if a >= 1e6:
        return "$%.1fM" % (v / 1e6)
    return "$%.0f" % v


def summary_he(result):
    """Plain Hebrew bullets: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    out = []
    head = "רבעון %s (הסתיים %s%s): הכנסות %s" % (
        cur["fiscal"].strip() or "?", cur["end"], ", מחושב מהדוח השנתי" if cur["derived"] else "",
        money(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        head += " (%+.1f%% לעומת שנה קודמת)" % cur["revenue_yoy"]
    out.append(head)
    line = "רווח נקי %s" % money(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", רווח למניה (EPS) $%.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line)
    ago = _year_ago(rows, cur["end"])
    for key, name in (("gross_margin", "שולי רווח גולמי"), ("operating_margin", "שולי רווח תפעולי"),
                      ("net_margin", "שולי רווח נקי")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if ago and ago.get(key) is not None:
            s += " (%+.1f נק' לעומת אותו רבעון אשתקד)" % (cur[key] - ago[key])
        out.append(s)
    if len(rows) > 1 and cur["revenue_yoy"] is not None and rows[1]["revenue_yoy"] is not None:
        a, b = cur["revenue_yoy"], rows[1]["revenue_yoy"]
        trend = "מאיצה" if a > b + 1 else "מאטה" if a < b - 1 else "יציבה"
        out.append("צמיחת ההכנסות %s: %+.1f%% עכשיו מול %+.1f%% ברבעון הקודם" % (trend, a, b))
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("שים לב: הרווח הנקי גבוה מהרווח התפעולי, כלומר יש בו רווחים חד-פעמיים "
                   "(השקעות, מס). כדאי לבדוק בדוח לפני שמסיקים שזו צמיחה")
    return out


def analyse(sec, sym, quarters=8):
    cik, title = sec.cik(sym)
    facts = sec.companyfacts(cik)
    try:
        result = sec_facts.analyse(facts, quarters=quarters, name=title)
    except sec_facts.FactsError as exc:
        raise EarningsError(str(exc))
    try:
        filings = sec_facts.latest_filings(sec.submissions(cik), cik)
    except EarningsError:
        filings = {"report": None, "earnings_8k": None}
    return {"symbol": sym.upper(), "entity": result["entity"], "cik": cik,
            "rows": result["rows"], "ttm": result["ttm"],
            "summary": summary_he(result), "filings": filings,
            "stale": sec_facts.stale_note(result, filings),
            "fundamentals": fundamentals(result), "source": "SEC XBRL company facts"}


def fundamentals(result):
    """The few numbers the potential score uses."""
    rows = result["rows"]
    if not rows:
        return None
    cur = rows[0]
    ago = _year_ago(rows, cur["end"])
    om_change = None
    if ago and cur["operating_margin"] is not None and ago.get("operating_margin") is not None:
        om_change = round(cur["operating_margin"] - ago["operating_margin"], 1)
    return {"revenue_yoy": cur["revenue_yoy"], "eps_yoy": cur["eps_yoy"],
            "operating_margin": cur["operating_margin"], "op_margin_change": om_change,
            "period_end": cur["end"]}


# ---------------------------------------------------------------- calendar

def nasdaq_day(day, cache):
    """Every company Nasdaq lists as reporting on `day`."""
    doc = get_json(NASDAQ_CAL % day.isoformat(), cache, ttl=12 * 3600,
                   headers={"User-Agent": BROWSER_UA}, gap=0.3)
    rows = ((doc.get("data") or {}).get("rows")) or []
    out = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        if sym:
            out[sym] = {"date": day.isoformat(), "time": TIME_HE.get(r.get("time"), ""),
                        "eps_forecast": r.get("epsForecast") or None,
                        "estimates": r.get("noOfEsts") or None,
                        "last_year_eps": r.get("lastYearEPS") or None,
                        "quarter": r.get("fiscalQuarterEnding") or None,
                        "source": "Nasdaq", "estimated": False}
    return out


def calendar(symbols, cache, today, days=28):
    """{symbol: next report} for the symbols found in Nasdaq's next `days` days."""
    want = {s.upper().replace("-", ".") for s in symbols}
    found, errors = {}, []
    for i in range(days + 1):
        day = today + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            for sym, row in nasdaq_day(day, cache).items():
                key = sym.replace("-", ".")
                if key in want and key not in found:
                    found[key] = row
        except FetchError as exc:
            errors.append(str(exc))
            if len(errors) >= 3:
                break
    return found, errors


def estimate_from_sec(sec, sym, today):
    """No calendar row: last period end + a quarter + the company's usual filing lag."""
    cik, _ = sec.cik(sym)
    filings = sec_facts.latest_filings(sec.submissions(cik), cik)
    rep = filings.get("report")
    if not rep or not rep.get("period"):
        return None
    period = dt.date.fromisoformat(rep["period"])
    lag = (dt.date.fromisoformat(rep["filed"]) - period).days
    lag = min(max(lag - 5, 20), 60)   # the press release comes a few days before the filing
    nxt = period + dt.timedelta(days=91) + dt.timedelta(days=lag)
    while nxt < today:
        nxt += dt.timedelta(days=91)
    return {"date": nxt.isoformat(), "time": "", "source": "הערכה לפי SEC",
            "estimated": True, "last_report": rep}


def recent_reports(sec, sym, today, days=45):
    """The last 10-Q/10-K if it was filed within `days` days."""
    cik, _ = sec.cik(sym)
    filings = sec_facts.latest_filings(sec.submissions(cik), cik)
    rep = filings.get("report")
    if rep and (today - dt.date.fromisoformat(rep["filed"])).days <= days:
        return rep
    return None


def config_user_agent(path):
    return sec_facts.user_agent(config=path if os.path.isfile(path) else None)
