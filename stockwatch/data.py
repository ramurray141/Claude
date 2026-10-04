"""Price data from Yahoo Finance via yfinance (free, unofficial, may be ~15 min delayed)."""
from __future__ import annotations

import pandas as pd

_PERIODS = [(25, "1mo"), (85, "3mo"), (175, "6mo"), (360, "1y"), (725, "2y"), (1820, "5y")]


def period_for(lookback_days: int) -> str:
    # Pad so there is always a previous close, even after a long weekend.
    for days, period in _PERIODS:
        if lookback_days + 5 <= days:
            return period
    return "max"


def fetch_history(ticker: str, lookback_days: int = 90) -> tuple[pd.Series, str]:
    """Return (daily closes, currency). Raises on failure."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    df = t.history(period=period_for(lookback_days), interval="1d", auto_adjust=False)
    if df is None or df.empty or "Close" not in df:
        raise ValueError(f"No data returned for {ticker!r} (check the Yahoo Finance symbol)")
    try:
        currency = t.fast_info.get("currency") or ""
    except Exception:
        currency = ""
    return df["Close"], currency
