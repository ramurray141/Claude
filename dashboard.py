"""Stock watchlist dashboard.  Run with:  streamlit run dashboard.py"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stockwatch.config import StockConfig, load_config, load_dotenv, save_config
from stockwatch.data import fetch_history
from stockwatch.monitor import evaluate_all, read_alert_log, run_check

CONFIG_PATH = Path("watchlist.toml")
PRICE_BLUE = "#2a78d6"
MUTED = "#8a8a85"

st.set_page_config(page_title="Stock Watch", page_icon="📉", layout="wide")
load_dotenv()


@st.cache_data(ttl=300, show_spinner=False)
def cached_fetch(ticker: str, lookback_days: int):
    return fetch_history(ticker, lookback_days)


def fmt_pct(v):
    return "–" if v is None else f"{v:+.2f}"


cfg = load_config(CONFIG_PATH)

# ---------------------------------------------------------------- sidebar: edit watchlist
with st.sidebar:
    st.header("Watchlist")
    with st.form("add", clear_on_submit=True):
        st.caption("Add or update a stock (Yahoo symbol, e.g. AAPL, VOD.L). "
                   "0 switches a rule off.")
        ticker = st.text_input("Ticker").strip().upper()
        name = st.text_input("Name (optional)")
        d = cfg.defaults
        daily = st.number_input("Daily drop alert %", 0.0, 99.0, float(d.get("daily_drop_pct", 0) or 0), 0.5)
        high = st.number_input("Drop from recent high %", 0.0, 99.0, float(d.get("drop_from_high_pct", 0) or 0), 1.0)
        lookback = st.number_input("High lookback (days)", 1, 1825, int(d.get("high_lookback_days", 90)))
        ref = st.number_input("Reference price (e.g. what you paid)", 0.0, value=0.0)
        ref_pct = st.number_input("Drop from reference %", 0.0, 99.0, 0.0, 1.0)
        floor = st.number_input("Alert below price", 0.0, value=0.0)
        if st.form_submit_button("Save stock") and ticker:
            new = StockConfig(
                ticker=ticker, name=name,
                daily_drop_pct=daily or None, drop_from_high_pct=high or None,
                high_lookback_days=int(lookback),
                reference_price=ref or None,
                drop_from_reference_pct=(ref_pct or None) if ref else None,
                below_price=floor or None,
            )
            cfg.stocks = [s for s in cfg.stocks if s.ticker != ticker] + [new]
            save_config(cfg, CONFIG_PATH)
            st.rerun()

    if cfg.stocks:
        remove = st.selectbox("Remove a stock", [""] + [s.ticker for s in cfg.stocks])
        if remove and st.button(f"Remove {remove}"):
            cfg.stocks = [s for s in cfg.stocks if s.ticker != remove]
            save_config(cfg, CONFIG_PATH)
            st.rerun()

    st.divider()
    st.caption(f"Notification channels: {', '.join(cfg.notify)}")
    if st.button("Check now & send notifications", type="primary"):
        with st.spinner("Checking…"):
            res = run_check(cfg)
        st.success(f"{len(res.new_alerts)} new alert(s) sent" if res.new_alerts
                   else "No new alerts (already-notified alerts are not resent)")
    if st.button("Refresh prices"):
        cached_fetch.clear()
        st.rerun()

# ---------------------------------------------------------------- main
st.title("📉 Stock Watch")
if not cfg.stocks:
    st.info("Add a stock in the sidebar to get started.")
    st.stop()

with st.spinner("Fetching prices…"):
    result = evaluate_all(cfg, fetch=cached_fetch)

alerts_by_ticker: dict[str, list] = {}
for a in result.alerts:
    alerts_by_ticker.setdefault(a.ticker, []).append(a)

c1, c2, c3 = st.columns(3)
c1.metric("Stocks watched", len(cfg.stocks))
c2.metric("Currently alerting", len(alerts_by_ticker))
c3.metric("Data errors", len(result.errors))

for a in result.alerts:
    st.error(f"🔻 {a.message}")
for t, err in result.errors.items():
    st.warning(f"⚠️ {t}: {err}")

rows = []
for s in cfg.stocks:
    m = result.metrics.get(s.ticker)
    if not m:
        continue
    triggered = alerts_by_ticker.get(s.ticker, [])
    rows.append({
        "Status": "🔻 Alert" if triggered else "✅ OK",
        "Ticker": s.ticker,
        "Name": s.name,
        "Price": round(m.price, 2),
        "Ccy": result.currency.get(s.ticker, ""),
        "Day %": fmt_pct(m.day_change_pct),
        "From high %": fmt_pct(m.from_high_pct),
        "From ref %": fmt_pct(m.from_reference_pct),
        "As of": m.as_of,
        "Triggered rules": ", ".join(a.rule for a in triggered),
    })

if rows:
    df = pd.DataFrame(rows)

    def colour_change(v: str):
        return {"-": "color: #d03b3b", "+": "color: #0ca30c"}.get(v[:1], "")

    st.dataframe(
        df.style.map(colour_change, subset=["Day %", "From high %", "From ref %"])
        .format({"Price": "{:,.2f}"}),
        hide_index=True, use_container_width=True,
    )
    st.caption("Prices from Yahoo Finance, typically delayed ~15 min. "
               "London (.L) prices are usually in pence (GBp).")

# ---------------------------------------------------------------- charts
st.subheader("Price history")
for s in cfg.stocks:
    closes = result.closes.get(s.ticker)
    m = result.metrics.get(s.ticker)
    if closes is None or m is None:
        continue
    flag = " 🔻" if s.ticker in alerts_by_ticker else ""
    with st.expander(f"{s.label}{flag}", expanded=s.ticker in alerts_by_ticker):
        fig = go.Figure(go.Scatter(
            x=closes.index, y=closes.values, mode="lines", name="Close",
            line=dict(color=PRICE_BLUE, width=2),
            hovertemplate="%{x|%d %b %Y}<br>%{y:,.2f}<extra></extra>",
        ))
        lines = [(m.high, f"{s.high_lookback_days}d high")]
        if s.drop_from_high_pct:
            lines.append((m.high * (1 - s.drop_from_high_pct / 100), f"−{s.drop_from_high_pct:g}% from high"))
        if s.reference_price:
            lines.append((s.reference_price, "Reference"))
        if s.below_price:
            lines.append((s.below_price, "Floor"))
        for y, label in lines:
            fig.add_hline(y=y, line=dict(color=MUTED, width=1, dash="dash"),
                          annotation_text=label, annotation_position="top left",
                          annotation_font_color=MUTED)
        fig.update_layout(
            height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
            hovermode="x unified", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor="rgba(128,128,128,0.15)", title=result.currency.get(s.ticker, "")),
            xaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- history
st.subheader("Notification history")
log = read_alert_log()
if log:
    st.dataframe(pd.DataFrame(log)[["time", "ticker", "rule", "message"]],
                 hide_index=True, use_container_width=True)
else:
    st.caption("No notifications sent yet. They are sent by the monitor "
               "(`python -m stockwatch.monitor`) or the 'Check now' button.")
