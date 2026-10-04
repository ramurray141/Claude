from datetime import date

import pandas as pd
import pytest

from stockwatch import monitor, notify
from stockwatch.config import Config, StockConfig, load_config, save_config
from stockwatch.data import period_for
from stockwatch.rules import compute_metrics, evaluate


def series(values, end="2026-10-02"):
    idx = pd.bdate_range(end=end, periods=len(values), tz="America/New_York")
    return pd.Series(values, index=idx, dtype=float)


# ------------------------------------------------------------------ rules

def test_metrics_basic():
    cfg = StockConfig("X", high_lookback_days=30)
    m = compute_metrics(cfg, series([100, 120, 110, 105]))
    assert m.price == 105
    assert m.prev_close == 110
    assert m.day_change_pct == pytest.approx(-4.5454, rel=1e-3)
    assert m.high == 120
    assert m.from_high_pct == pytest.approx(-12.5)
    assert m.as_of == date(2026, 10, 2)


def test_high_lookback_excludes_old_peak():
    vals = [200] + [100] * 60
    m = compute_metrics(StockConfig("X", high_lookback_days=30), series(vals))
    assert m.high == 100


def test_nan_rows_dropped():
    m = compute_metrics(StockConfig("X"), series([100, float("nan"), 90]))
    assert m.prev_close == 100


def test_empty_series_raises():
    with pytest.raises(ValueError):
        compute_metrics(StockConfig("X"), series([float("nan")]))


@pytest.mark.parametrize("price,threshold,fires", [(97, 3.0, True), (97.1, 3.0, False), (103, 3.0, False)])
def test_daily_drop_threshold_boundary(price, threshold, fires):
    cfg = StockConfig("X", daily_drop_pct=threshold)
    alerts = evaluate(cfg, compute_metrics(cfg, series([100, price])))
    assert any(a.rule == "daily_drop" for a in alerts) == fires


def test_all_rules_fire():
    cfg = StockConfig("X", daily_drop_pct=2, drop_from_high_pct=10, reference_price=150,
                      drop_from_reference_pct=20, below_price=90)
    alerts = evaluate(cfg, compute_metrics(cfg, series([150, 100, 85])))
    assert {a.rule for a in alerts} == {"daily_drop", "drop_from_high", "drop_from_reference", "below_price"}


def test_no_rules_no_alerts():
    cfg = StockConfig("X")
    assert evaluate(cfg, compute_metrics(cfg, series([100, 1]))) == []


def test_period_for():
    assert period_for(1) == "1mo"
    assert period_for(90) == "6mo"
    assert period_for(365) == "2y"
    assert period_for(5000) == "max"


# ------------------------------------------------------------------ config

def write(tmp_path, text):
    p = tmp_path / "w.toml"
    p.write_text(text)
    return p


def test_defaults_apply_and_override(tmp_path):
    cfg = load_config(write(tmp_path, """
[defaults]
daily_drop_pct = 3.0
[[stocks]]
ticker = "aapl"
[[stocks]]
ticker = "MSFT"
daily_drop_pct = 5.0
"""))
    assert [s.ticker for s in cfg.stocks] == ["AAPL", "MSFT"]
    assert cfg.stocks[0].daily_drop_pct == 3.0
    assert cfg.stocks[1].daily_drop_pct == 5.0


@pytest.mark.parametrize("body,msg", [
    ('[[stocks]]\nticker="A"\ndaily_drop_pct=150', "between 0 and 100"),
    ('[[stocks]]\nticker="A"\ndrop_from_reference_pct=10', "needs reference_price"),
    ('[[stocks]]\nticker="A"\n[[stocks]]\nticker="a"', "Duplicate"),
    ('[[stocks]]\nticker="A"\ntypo=1', "Unknown keys"),
    ('[settings]\nnotify=["sms"]', "Unknown notifiers"),
])
def test_config_validation(tmp_path, body, msg):
    with pytest.raises(ValueError, match=msg):
        load_config(write(tmp_path, body))


def test_save_round_trip_keeps_defaults_inherited(tmp_path):
    cfg = Config(
        stocks=[StockConfig("A", name='Quote "x"', daily_drop_pct=3.0),
                StockConfig("B", daily_drop_pct=5.0, reference_price=10.0, drop_from_reference_pct=20.0)],
        defaults={"daily_drop_pct": 3.0},
        notify=["console", "ntfy"],
    )
    p = tmp_path / "out.toml"
    save_config(cfg, p)
    assert "daily_drop_pct = 3.0" not in p.read_text().split("[[stocks]]")[1]
    loaded = load_config(p)
    assert loaded.stocks == cfg.stocks
    assert loaded.notify == cfg.notify


def test_shipped_watchlist_is_valid():
    assert load_config("watchlist.toml").stocks


# ------------------------------------------------------------------ monitor / dedupe

class FakeMarket:
    def __init__(self, prices):
        self.prices = prices  # ticker -> list of closes, or Exception

    def __call__(self, ticker, lookback):
        p = self.prices[ticker]
        if isinstance(p, Exception):
            raise p
        return series(p[0], end=p[1]), "USD"


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setitem(notify.CHANNELS, "console", lambda t, b: calls.append((t, b)))
    return calls


def test_notifies_once_then_suppresses(tmp_path, sent):
    cfg = Config(stocks=[StockConfig("A", daily_drop_pct=3)], notify=["console"])
    market = FakeMarket({"A": ([100, 95], "2026-10-01")})
    r1 = monitor.run_check(cfg, market, state_dir=tmp_path)
    r2 = monitor.run_check(cfg, market, state_dir=tmp_path)
    assert len(r1.new_alerts) == 1 and len(r2.new_alerts) == 0
    assert len(sent) == 1
    assert len(monitor.read_alert_log(tmp_path)) == 1


def test_second_daily_drop_on_new_day_notifies_again(tmp_path, sent):
    cfg = Config(stocks=[StockConfig("A", daily_drop_pct=3)], notify=["console"])
    monitor.run_check(cfg, FakeMarket({"A": ([100, 95], "2026-10-01")}), state_dir=tmp_path)
    monitor.run_check(cfg, FakeMarket({"A": ([100, 95, 90], "2026-10-02")}), state_dir=tmp_path)
    assert len(sent) == 2


def test_level_alert_rearms_after_recovery(tmp_path, sent):
    cfg = Config(stocks=[StockConfig("A", below_price=50)], notify=["console"])
    for p in ([60, 40], [40, 45], [45, 60], [60, 40]):
        monitor.run_check(cfg, FakeMarket({"A": (p, "2026-10-01")}), state_dir=tmp_path)
    assert len(sent) == 2  # first breach, then again after recovering above 50


def test_fetch_error_does_not_cause_repeat(tmp_path, sent):
    cfg = Config(stocks=[StockConfig("A", below_price=50), StockConfig("B")], notify=["console"])
    ok = FakeMarket({"A": ([60, 40], "2026-10-01"), "B": ([1, 1], "2026-10-01")})
    down = FakeMarket({"A": RuntimeError("timeout"), "B": ([1, 1], "2026-10-01")})
    monitor.run_check(cfg, ok, state_dir=tmp_path)
    r = monitor.run_check(cfg, down, state_dir=tmp_path)
    monitor.run_check(cfg, ok, state_dir=tmp_path)
    assert "A" in r.errors
    assert len(sent) == 1


def test_failing_channel_does_not_block_others(monkeypatch):
    got = []
    monkeypatch.setitem(notify.CHANNELS, "ntfy", lambda t, b: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setitem(notify.CHANNELS, "console", lambda t, b: got.append(t))
    res = notify.send(["ntfy", "console"], "t", "b")
    assert res == {"ntfy": "down", "console": None}
    assert got == ["t"]


def test_ntfy_requires_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    with pytest.raises(notify.NotifierError):
        notify.ntfy("t", "b")


def test_cli_once_exit_codes(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "w.toml").write_text('[settings]\nnotify=["console"]\n[[stocks]]\nticker="A"\nbelow_price=50\n')
    monkeypatch.setattr(monitor, "fetch_history", FakeMarket({"A": ([60, 40], "2026-10-01")}))
    monkeypatch.setattr(monitor.run_check, "__defaults__",
                        (FakeMarket({"A": ([60, 40], "2026-10-01")}), None, tmp_path / "state"))
    assert monitor.main(["--config", "w.toml", "--once"]) == 0
    assert len(sent) == 1
    monkeypatch.setattr(monitor.run_check, "__defaults__",
                        (FakeMarket({"A": RuntimeError("x")}), None, tmp_path / "state"))
    assert monitor.main(["--config", "w.toml", "--once"]) == 1


def test_zero_disables_a_defaulted_rule_and_round_trips(tmp_path):
    cfg = load_config(write(tmp_path, '[defaults]\ndaily_drop_pct=3.0\n[[stocks]]\nticker="A"\ndaily_drop_pct=0\n'))
    assert cfg.stocks[0].daily_drop_pct is None
    out = tmp_path / "o.toml"
    save_config(cfg, out)
    assert load_config(out).stocks[0].daily_drop_pct is None
