# Portfolio Radar: notes for Claude Code

A local web tool for a stock investor, with three tabs:
- **התיק שלי (My portfolio):** pick holdings by name or symbol.
- **רעיונות (Ideas):** similar stocks, ranked by a potential score.
- **דוחות (Earnings):** upcoming reports, plus a quarterly analysis from SEC.

The UI is in Hebrew (RTL). The owner writes in Hebrew, so reply in Hebrew.

## Running it

```
python server.py            # live data, opens http://127.0.0.1:8765
python server.py --demo     # offline, real snapshots from demo_data/ (2026-10-20)
python -m unittest discover -s tests -v
```

On Windows, use `py` or `python`, not `python3`.

It uses the Python standard library only, with no pip installs and no build step. Keep it that way unless there is a clear reason to change it.

## Layout

| Path | What it does |
|---|---|
| `server.py` | HTTP server, `/api/*` routes, static files from `web/` (with a path-traversal check), and the flags `--demo --port --host --config --no-browser` |
| `radar/net.py` | JSON fetch with a disk cache (`data/cache`), TTL and per-host pacing |
| `radar/market.py` | Yahoo chart, search and peers; indicators (MA20/50/200, RSI14, returns, volatility) |
| `radar/earnings.py` | SEC EDGAR client; Hebrew summary; Nasdaq calendar; earnings date estimated from SEC filings |
| `radar/sec_facts.py` | XBRL quarterly analysis, vendored from the day-trader-skill pack (MIT, see `LICENSE.sec_facts`) |
| `radar/score.py` | Transparent 0–100 potential score. Technical parts: trend 25, momentum 20, range 15, RSI 10. Fundamental parts: revenue 15, EPS 10, margin 5. The score is normalized over the parts that have data. |
| `radar/sources.py` | `LiveSource` and `DemoSource` (the demo has SEC data for AAPL and KO only) |
| `radar/app.py` | `Radar`: portfolio storage (`data/portfolio.json`), stock payload, ideas, earnings |
| `web/` | `index.html`, `style.css` and `app.js`: vanilla JS, SVG charts, light and dark themes |

## Configuration

`config.json` is gitignored. It holds `{"sec_user_agent": "Name email@example.com"}`, which SEC requires. Without it, these are off:
- SEC analysis
- SEC-estimated earnings dates
- the fundamentals part of the score

The server reads the config only at startup, so restart it after a change. The `SEC_USER_AGENT` environment variable also works.

The owner's email is private. Never write it into files or commit it. The owner creates `config.json` themselves.

## Status

- **Works live:** Yahoo prices, a full year of history, search (including TASE symbols such as TEVA.TA), and ideas through Yahoo's "recommended symbols".
- **Nasdaq earnings calendar:** it returned 403 from the cloud container, and it has not been tested from a home connection yet. Do not work around it with spoofed headers. If it is blocked, look for another source. When it fails, holdings fall back to the SEC estimate.
- **Last fix:** idea cards overflowed when names were long. The card column now shrinks, and names truncate with an ellipsis.

## Ideas offered to the owner, not started

- A Yahoo-based earnings calendar as a fallback for Nasdaq.
- Did "by number" mean TASE security numbers (מספר נייר)? This has not been confirmed.
- CSV import of holdings.
- Comparison against the S&P 500.
- Sector breakdown chart.
- Pre-earnings alerts.

## Conventions

- Before committing, run the tests: `tests/test_radar.py` has 20 offline tests.
- Wrap numbers in Hebrew text in `<bdi class="num">`, and use `bidiText()` in `app.js`.
- Colors are tokens on `:root`, with a dark-mode block and a `[data-theme]` override.
- The footer must keep saying that this is not investment advice.
