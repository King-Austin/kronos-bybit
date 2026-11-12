from dataclasses import dataclass

import numpy as np
import pandas as pd

from .forecast import Forecast


@dataclass
class Signal:
    side: str | None
    entry: float
    expected_bps: float
    prob: float
    q05_bps: float
    q95_bps: float
    stop: float | None
    take_profit: float | None
    atr: float
    reason: str


def atr(df: pd.DataFrame, period: int = 14) -> float:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    value = tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(value)


def build_signal(fc: Forecast, df: pd.DataFrame, settings) -> Signal:
    entry = fc.last_close
    expected_bps = fc.expected_return * 10_000
    prob_up = fc.prob_up
    stop_distance = settings.atr_k * atr(df)
    cost_bps = settings.round_trip_cost_bps
    edge_ok = expected_bps > cost_bps * 1.5
    edge_short_ok = expected_bps < -cost_bps * 1.5

    side = None
    reason = "no edge above costs"
    if prob_up >= settings.prob_threshold and edge_ok:
        side = "Buy"
        reason = f"prob_up={prob_up:.2f} expected={expected_bps:+.0f}bps > cost {cost_bps:.0f}bps"
    elif prob_up <= 1 - settings.prob_threshold and edge_short_ok:
        side = "Sell"
        reason = f"prob_down={1 - prob_up:.2f} expected={expected_bps:+.0f}bps > cost {cost_bps:.0f}bps"

    if side and stop_distance <= 0:
        side = None
        reason = "ATR is zero, refusing to trade"

    if side == "Buy":
        stop = entry - stop_distance
        take = entry + settings.rr * stop_distance
    elif side == "Sell":
        stop = entry + stop_distance
        take = entry - settings.rr * stop_distance
    else:
        stop = take = None

    return Signal(
        side=side,
        entry=float(entry),
        expected_bps=float(expected_bps),
        prob=float(prob_up),
        q05_bps=float(fc.q05 * 10_000),
        q95_bps=float(fc.q95 * 10_000),
        stop=None if stop is None else float(stop),
        take_profit=None if take is None else float(take),
        atr=stop_distance,
        reason=reason,
    )


def position_size(equity: float, entry: float, stop: float, settings,
                  qty_step: str, min_qty: float, max_qty: float | None = None) -> float:
    from .bybit import round_step

    risk_amount = equity * settings.risk_pct
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return 0.0
    raw = risk_amount / stop_distance
    max_notional = equity * settings.max_leverage
    raw = min(raw, max_notional / entry)
    if max_qty:
        raw = min(raw, max_qty)
    try:
        qty = float(round_step(raw, qty_step))
    except Exception:
        qty = 0.0
    if qty < min_qty:
        return 0.0
    return qty


def summarize_signal(sig: Signal) -> str:
    return (
        f"side={sig.side or '-'} prob={sig.prob:.2f} exp={sig.expected_bps:+.0f}bps "
        f"q05/q95={sig.q05_bps:+.0f}/{sig.q95_bps:+.0f}bps atr={sig.atr:.6g} :: {sig.reason}"
    )
