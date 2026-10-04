"""Load and save the watchlist (TOML)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

RULE_FIELDS = (
    "daily_drop_pct",
    "drop_from_high_pct",
    "high_lookback_days",
    "reference_price",
    "drop_from_reference_pct",
    "below_price",
)
VALID_NOTIFIERS = {"console", "desktop", "ntfy", "email"}


@dataclass
class StockConfig:
    ticker: str
    name: str = ""
    daily_drop_pct: float | None = None
    drop_from_high_pct: float | None = None
    high_lookback_days: int = 90
    reference_price: float | None = None
    drop_from_reference_pct: float | None = None
    below_price: float | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ticker})" if self.name else self.ticker


@dataclass
class Config:
    stocks: list[StockConfig] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    check_interval_minutes: int = 15
    notify: list[str] = field(default_factory=lambda: ["console"])


def _validate_stock(s: StockConfig) -> None:
    if not s.ticker:
        raise ValueError("Every stock needs a ticker")
    for name in ("daily_drop_pct", "drop_from_high_pct", "drop_from_reference_pct"):
        v = getattr(s, name)
        if v is not None and not 0 < v < 100:
            raise ValueError(f"{s.ticker}: {name} must be between 0 and 100, got {v}")
    if s.drop_from_reference_pct is not None and s.reference_price is None:
        raise ValueError(f"{s.ticker}: drop_from_reference_pct needs reference_price")
    if s.high_lookback_days < 1:
        raise ValueError(f"{s.ticker}: high_lookback_days must be >= 1")


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    settings = raw.get("settings", {})
    defaults = {k: v for k, v in raw.get("defaults", {}).items() if k in RULE_FIELDS}
    known = {f.name for f in fields(StockConfig)}

    stocks = []
    seen = set()
    for entry in raw.get("stocks", []):
        unknown = set(entry) - known
        if unknown:
            raise ValueError(f"Unknown keys for {entry.get('ticker')}: {sorted(unknown)}")
        merged = {**defaults, **entry}
        # An explicit 0 switches a rule off, overriding any default.
        for k in RULE_FIELDS:
            if k != "high_lookback_days" and merged.get(k) == 0:
                merged[k] = None
        merged["ticker"] = str(merged.get("ticker", "")).strip().upper()
        stock = StockConfig(**merged)
        _validate_stock(stock)
        if stock.ticker in seen:
            raise ValueError(f"Duplicate ticker: {stock.ticker}")
        seen.add(stock.ticker)
        stocks.append(stock)

    notify = list(settings.get("notify", ["console"]))
    bad = set(notify) - VALID_NOTIFIERS
    if bad:
        raise ValueError(f"Unknown notifiers {sorted(bad)}; valid: {sorted(VALID_NOTIFIERS)}")

    return Config(
        stocks=stocks,
        defaults=defaults,
        check_interval_minutes=int(settings.get("check_interval_minutes", 15)),
        notify=notify,
    )


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def save_config(cfg: Config, path: str | Path) -> None:
    """Write the config back out. Per-stock values equal to the defaults are omitted
    so that changing a default still affects those stocks. Comments are not preserved."""
    lines = [
        "# Stock watchlist. See README.md for the meaning of each rule.",
        "",
        "[settings]",
        f"check_interval_minutes = {cfg.check_interval_minutes}",
        f"notify = {_toml_value(cfg.notify)}",
        "",
        "[defaults]",
    ]
    lines += [f"{k} = {_toml_value(v)}" for k, v in cfg.defaults.items()]
    base = StockConfig(ticker="")
    for s in cfg.stocks:
        lines += ["", "[[stocks]]", f"ticker = {_toml_value(s.ticker)}"]
        if s.name:
            lines.append(f"name = {_toml_value(s.name)}")
        for k in RULE_FIELDS:
            v = getattr(s, k)
            inherited = cfg.defaults.get(k, getattr(base, k))
            if v is None and inherited is not None:
                lines.append(f"{k} = 0")  # explicitly off
            elif v is not None and v != inherited:
                lines.append(f"{k} = {_toml_value(v)}")
    Path(path).write_text("\n".join(lines) + "\n")


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)
