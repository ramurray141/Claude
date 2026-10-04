# Stock Watch

A dashboard for your watchlist, plus a monitor that notifies you when a stock drops
past a threshold you set.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run dashboard.py          # the dashboard, opens in your browser
python -m stockwatch.monitor        # background monitor: checks every 15 min and notifies
```

Edit `watchlist.toml` (or use the dashboard sidebar) to set your stocks and thresholds.

## Alert rules

Each stock can use any combination. Values in `[defaults]` apply to every stock
unless the stock overrides them; set a rule to `0` to switch it off for one stock.

| Rule | Fires when | Re-alerts |
|---|---|---|
| `daily_drop_pct` | price is this % below the previous close | once per trading day |
| `drop_from_high_pct` | price is this % below the highest close in `high_lookback_days` | after it recovers above the threshold and falls again |
| `drop_from_reference_pct` | price is this % below `reference_price` (e.g. what you paid) | same |
| `below_price` | price is below this absolute value | same |

So you get **one** notification per event, not one every 15 minutes. Already-sent alerts
are remembered in `state/state.json`; the history is in `state/alerts_log.jsonl` and on
the dashboard.

## Notifications

Set `notify = [...]` in `watchlist.toml`. Put credentials in `.env` (copy
`.env.example`; it is git-ignored). Test with `python -m stockwatch.monitor --test-notify`.

| Channel | Setup | Notes |
|---|---|---|
| `console` | none | prints to the terminal |
| `desktop` | none | macOS, Linux (`notify-send`), Windows (balloon tip) |
| `ntfy` | install the free [ntfy](https://ntfy.sh) app, subscribe to a topic, set `NTFY_TOPIC` | **easiest way to get alerts on your phone**. Use a long random topic name: anyone who guesses it can read your alerts |
| `email` | `SMTP_*` and `ALERT_EMAIL_TO` | Gmail needs an [app password](https://myaccount.google.com/apppasswords). Outlook/Hotmail personal accounts may block basic SMTP login |

## Running it when your computer is off

The monitor only works while something runs it. Options:

1. **GitHub Actions (included):** `.github/workflows/stock-alerts.yml` checks every
   30 minutes on weekdays. Add `NTFY_TOPIC` (and/or the email settings) as repository
   secrets under *Settings → Secrets and variables → Actions*. It only runs once the
   workflow is on the default branch. On a private repo this uses roughly 600-700 of the
   2,000 free minutes a month.
2. **cron** on an always-on machine (e.g. a Raspberry Pi):
   `*/15 7-21 * * 1-5 cd /path/to/repo && .venv/bin/python -m stockwatch.monitor --once`

## Limitations

- Data comes from Yahoo Finance via `yfinance`: free and needs no API key, but unofficial.
  It can be delayed (often ~15 min), rate-limited, or break when Yahoo changes things.
  Don't rely on it for trading decisions that need to be timely.
- Tickers are Yahoo symbols: `AAPL`, `VOD.L` (London, priced in **pence**), `VUSA.L`, etc.
  Set `reference_price`/`below_price` in the same units the dashboard shows.
- "Daily drop" compares with the previous close, so it can fire on a stock that opened
  down on news overnight, before you could have acted.
- `drop_from_high_pct` uses daily closes, not intraday highs.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```
