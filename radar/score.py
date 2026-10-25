"""The potential score: a transparent 0-100 sum of simple rules on trend,
momentum, position in the 52-week range, RSI and (for SEC filers) growth.
Every point is explained. It ranks charts and numbers; it predicts nothing."""


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _scale(v, lo, hi, pts):
    """Linear: lo -> 0 points, hi -> pts points."""
    return round(_clamp((v - lo) / (hi - lo), 0, 1) * pts, 1)


def score(ind, fund=None):
    parts = []

    def add(key, label, pts, mx, detail):
        parts.append({"key": key, "label": label, "points": pts, "max": mx, "detail": detail})

    price, ma50, ma200 = ind["price"], ind.get("ma50"), ind.get("ma200")
    # Trend: 25
    if ma50:
        above = price > ma50
        pts = 10 if above else 0
        if ind.get("ma50_rising"):
            pts += 8
        add("trend50", "מגמה (ממוצע 50 יום)", pts, 18,
            "המחיר %sממוצע 50 יום (%.2f)%s" % ("מעל " if above else "מתחת ל", ma50,
                                                ", והממוצע עולה" if ind.get("ma50_rising") else ""))
    if ma200:
        above = price > ma200
        add("trend200", "מגמה ארוכה (ממוצע 200 יום)", 7 if above else 0, 7,
            "המחיר %sממוצע 200 יום (%.2f)" % ("מעל " if above else "מתחת ל", ma200))
    # Momentum: 20
    if ind.get("ret_3m") is not None:
        add("momentum", "מומנטום (3 חודשים)", _scale(ind["ret_3m"], -15, 30, 20), 20,
            "תשואה של %+.1f%% ב-3 החודשים האחרונים" % ind["ret_3m"])
    # Position in the 52-week range: 15
    off = ind.get("off_high_pct")
    if off is not None:
        trend_ok = ma50 is not None and price > ma50
        if off >= -5:
            pts, why = 12, "קרוב לשיא 52 שבועות (%.1f%%): חוזק" % off
        elif off >= -25:
            pts = 15 if trend_ok else 8
            why = "%.1f%% מתחת לשיא%s" % (off, ", בתיקון בתוך מגמה עולה" if trend_ok else "")
        elif off >= -40:
            pts, why = 5, "%.1f%% מתחת לשיא: ירידה משמעותית" % off
        else:
            pts, why = 2, "%.1f%% מתחת לשיא: ירידה חדה" % off
        add("range", "מיקום בטווח 52 שבועות", pts, 15, why)
    # RSI: 10
    r = ind.get("rsi14")
    if r is not None:
        if 45 <= r <= 65:
            pts, why = 10, "RSI %.0f: מאוזן" % r
        elif 30 <= r < 45:
            pts, why = 8, "RSI %.0f: חלש, יש מקום לריבאונד" % r
        elif 65 < r <= 75:
            pts, why = 5, "RSI %.0f: מתחמם" % r
        elif r > 75:
            pts, why = 2, "RSI %.0f: קנוי-יתר (overbought)" % r
        else:
            pts, why = 6, "RSI %.0f: מכור-יתר (oversold)" % r
        add("rsi", "RSI (14)", pts, 10, why)
    # Fundamentals: 30, only when SEC numbers exist
    if fund:
        if fund.get("revenue_yoy") is not None:
            add("revenue", "צמיחת הכנסות", _scale(fund["revenue_yoy"], -5, 30, 15), 15,
                "%+.1f%% לעומת אותו רבעון אשתקד" % fund["revenue_yoy"])
        if fund.get("eps_yoy") is not None:
            add("eps", "צמיחת רווח למניה", _scale(fund["eps_yoy"], -10, 40, 10), 10,
                "%+.1f%% לעומת אותו רבעון אשתקד" % fund["eps_yoy"])
        if fund.get("op_margin_change") is not None:
            c = fund["op_margin_change"]
            add("margin", "שיפור בשולי הרווח", 5 if c > 0.5 else 2 if c > -0.5 else 0, 5,
                "שולי רווח תפעולי %+.1f נק' לעומת שנה קודמת" % c)

    got = sum(p["points"] for p in parts)
    mx = sum(p["max"] for p in parts)
    value = round(got / mx * 100) if mx else None
    return {"value": value, "label": label(value), "parts": parts,
            "with_fundamentals": any(p["key"] in ("revenue", "eps") for p in parts),
            "tags": tags(ind, fund)}


def label(v):
    if v is None:
        return "אין מספיק נתונים"
    if v >= 70:
        return "פוטנציאל גבוה"
    if v >= 50:
        return "בינוני"
    return "חלש"


def tags(ind, fund):
    out = []
    if ind.get("off_high_pct") is not None and ind["off_high_pct"] >= -3:
        out.append("ליד שיא שנתי")
    if ind.get("rsi14") is not None and ind["rsi14"] < 30:
        out.append("מכור-יתר")
    if ind.get("rsi14") is not None and ind["rsi14"] > 75:
        out.append("קנוי-יתר")
    if ind.get("ma50") and ind.get("ma200") and ind["ma50"] > ind["ma200"] and ind["price"] > ind["ma50"]:
        out.append("מגמה עולה")
    if fund and (fund.get("revenue_yoy") or 0) >= 10:
        out.append("צמיחה דו-ספרתית")
    if ind.get("ret_3m") is not None and ind["ret_3m"] <= -20:
        out.append("ירידה חדה")
    return out
