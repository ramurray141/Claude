"""Turn a price history into metrics, and metrics into alerts. Pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .config import StockConfig


@dataclass(frozen=True)
class Metrics:
    ticker: str
    as_of: date
    price: float
    prev_close: float | None
    day_change_pct: float | None
    high: float
    high_date: date
    from_high_pct: float
    from_reference_pct: float | None


@dataclass(frozen=True)
class Alert:
    ticker: str
    rule: str
    # Identifies one alert episode. Used to avoid repeating the same notification.
    key: str
    message: str


def _pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0


def compute_metrics(cfg: StockConfig, closes: pd.Series) -> Metrics:
    """`closes` is a daily close series indexed by date, oldest first. During market
    hours the last value is the live (possibly delayed) price for today."""
    closes = closes.dropna()
    if closes.empty:
        raise ValueError(f"{cfg.ticker}: no price data")
    idx = pd.DatetimeIndex(closes.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    closes = pd.Series(closes.to_numpy(dtype=float), index=idx.normalize())

    as_of = closes.index[-1].date()
    price = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None

    window = closes[closes.index >= pd.Timestamp(as_of - timedelta(days=cfg.high_lookback_days))]
    high = float(window.max())
    high_date = window.idxmax().date()

    return Metrics(
        ticker=cfg.ticker,
        as_of=as_of,
        price=price,
        prev_close=prev_close,
        day_change_pct=_pct(price, prev_close) if prev_close else None,
        high=high,
        high_date=high_date,
        from_high_pct=_pct(price, high),
        from_reference_pct=_pct(price, cfg.reference_price) if cfg.reference_price else None,
    )


def evaluate(cfg: StockConfig, m: Metrics) -> list[Alert]:
    alerts: list[Alert] = []
    t = cfg.label

    # Daily drops are keyed by trading date, so a second bad day alerts again.
    if cfg.daily_drop_pct is not None and m.day_change_pct is not None:
        if m.day_change_pct <= -cfg.daily_drop_pct:
            alerts.append(Alert(
                m.ticker, "daily_drop", f"{m.ticker}:daily_drop:{m.as_of}",
                f"{t} is down {-m.day_change_pct:.1f}% today "
                f"({m.price:,.2f} vs previous close {m.prev_close:,.2f}; "
                f"threshold {cfg.daily_drop_pct:g}%)",
            ))

    # The other rules alert once when the condition starts and re-arm when it clears.
    if cfg.drop_from_high_pct is not None and m.from_high_pct <= -cfg.drop_from_high_pct:
        alerts.append(Alert(
            m.ticker, "drop_from_high", f"{m.ticker}:drop_from_high",
            f"{t} is {-m.from_high_pct:.1f}% below its {cfg.high_lookback_days}-day high "
            f"({m.price:,.2f} vs {m.high:,.2f} on {m.high_date}; "
            f"threshold {cfg.drop_from_high_pct:g}%)",
        ))

    if (cfg.drop_from_reference_pct is not None and m.from_reference_pct is not None
            and m.from_reference_pct <= -cfg.drop_from_reference_pct):
        alerts.append(Alert(
            m.ticker, "drop_from_reference", f"{m.ticker}:drop_from_reference",
            f"{t} is {-m.from_reference_pct:.1f}% below your reference price "
            f"({m.price:,.2f} vs {cfg.reference_price:,.2f}; "
            f"threshold {cfg.drop_from_reference_pct:g}%)",
        ))

    if cfg.below_price is not None and m.price < cfg.below_price:
        alerts.append(Alert(
            m.ticker, "below_price", f"{m.ticker}:below_price",
            f"{t} is at {m.price:,.2f}, below your floor of {cfg.below_price:,.2f}",
        ))

    return alerts
