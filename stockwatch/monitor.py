"""Check the watchlist and send notifications for new alerts.

    python -m stockwatch.monitor              # loop forever, every check_interval_minutes
    python -m stockwatch.monitor --once       # single check (for cron / scheduled jobs)
    python -m stockwatch.monitor --test-notify
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from . import notify
from .config import Config, load_config, load_dotenv
from .data import fetch_history
from .rules import Alert, Metrics, compute_metrics, evaluate

log = logging.getLogger("stockwatch")

STATE_DIR = Path("state")
Fetcher = Callable[[str, int], "tuple[pd.Series, str]"]


@dataclass
class CheckResult:
    metrics: dict[str, Metrics] = field(default_factory=dict)
    currency: dict[str, str] = field(default_factory=dict)
    closes: dict[str, pd.Series] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)       # everything currently triggered
    new_alerts: list[Alert] = field(default_factory=list)   # not yet notified
    errors: dict[str, str] = field(default_factory=dict)


def evaluate_all(cfg: Config, fetch: Fetcher = fetch_history) -> CheckResult:
    result = CheckResult()
    for stock in cfg.stocks:
        try:
            closes, currency = fetch(stock.ticker, stock.high_lookback_days)
            m = compute_metrics(stock, closes)
        except Exception as e:  # noqa: BLE001 - one bad ticker must not stop the rest
            result.errors[stock.ticker] = str(e)
            continue
        result.metrics[stock.ticker] = m
        result.currency[stock.ticker] = currency
        result.closes[stock.ticker] = closes
        result.alerts.extend(evaluate(stock, m))
    return result


def _load_active(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text()).get("active", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def run_check(
    cfg: Config,
    fetch: Fetcher = fetch_history,
    channels: list[str] | None = None,
    state_dir: Path = STATE_DIR,
) -> CheckResult:
    """Fetch, evaluate, notify about alerts not already sent, and persist state.

    State is the set of alert keys currently triggered. An alert is "new" if its key
    wasn't triggered last time. Keys of tickers that failed to fetch are carried over
    so a temporary data outage doesn't cause a repeat notification afterwards.
    """
    result = evaluate_all(cfg, fetch)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    previous = _load_active(state_path)
    current = {a.key for a in result.alerts}
    carried = {k for k in previous if k.split(":", 1)[0] in result.errors}
    result.new_alerts = [a for a in result.alerts if a.key not in previous]

    if result.new_alerts:
        n = len(result.new_alerts)
        tickers = ", ".join(sorted({a.ticker for a in result.new_alerts}))
        title = f"Stock alert: {tickers}" if n == 1 else f"{n} stock alerts: {tickers}"
        body = "\n".join(f"• {a.message}" for a in result.new_alerts)
        delivery = notify.send(channels if channels is not None else cfg.notify, title, body)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(state_dir / "alerts_log.jsonl", "a") as f:
            for a in result.new_alerts:
                f.write(json.dumps({"time": now, **asdict(a), "delivery": delivery}) + "\n")

    state_path.write_text(json.dumps({"active": sorted(current | carried)}, indent=2))
    for ticker, err in result.errors.items():
        log.warning("%s: %s", ticker, err)
    return result


def read_alert_log(state_dir: Path = STATE_DIR, limit: int = 100) -> list[dict]:
    path = state_dir / "alerts_log.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--config", default="watchlist.toml")
    p.add_argument("--once", action="store_true", help="run a single check and exit")
    p.add_argument("--notify", help="comma-separated channels, overrides the config")
    p.add_argument("--test-notify", action="store_true", help="send a test message and exit")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    cfg = load_config(args.config)
    channels = args.notify.split(",") if args.notify else cfg.notify
    unknown = set(channels) - set(notify.CHANNELS)
    if unknown:
        p.error(f"unknown channel(s): {', '.join(sorted(unknown))}")

    if args.test_notify:
        results = notify.send(channels, "Stockwatch test", "Notifications are working.")
        for ch, err in results.items():
            print(f"{ch}: {'OK' if err is None else 'FAILED - ' + err}")
        return 0 if all(e is None for e in results.values()) else 1

    while True:
        result = run_check(cfg, channels=channels)
        log.info("Checked %d stock(s): %d triggered, %d new, %d error(s)",
                 len(result.metrics), len(result.alerts), len(result.new_alerts), len(result.errors))
        if args.once:
            # Fail the job only if nothing could be fetched at all.
            return 1 if cfg.stocks and not result.metrics else 0
        time.sleep(cfg.check_interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
