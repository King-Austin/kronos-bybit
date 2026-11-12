import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from bot.bybit import BybitClient, round_tick
from bot.config import load_settings
from bot.forecast import forecast
from bot.strategy import build_signal

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def run_scan(horizon: int = 4, paths: int = 2) -> dict:
    base = replace(load_settings(), dry_run=True, horizon=horizon, sample_paths=paths)
    client = BybitClient(base)
    client.sync_time()

    results = []
    print(f"Starting Multi-Timeframe Confluence Scan (1h Bias + 15m Trigger) on {len(SYMBOLS)} pairs...")

    for sym in SYMBOLS:
        print(f"\nProcessing {sym}...")
        try:
            # 1. Higher Timeframe (60m = 1hr)
            s_60 = replace(base, symbol=sym, interval="60")
            df_60 = client.closed_klines(512, symbol=sym, interval="60")
            fc_60 = forecast(s_60, df_60)
            sig_60 = build_signal(fc_60, df_60, s_60)

            # Trend Bias definition
            if sig_60.prob >= 0.55 and sig_60.expected_bps > 0:
                trend_bias = "BULLISH"
            elif sig_60.prob <= 0.45 and sig_60.expected_bps < 0:
                trend_bias = "BEARISH"
            else:
                trend_bias = "NEUTRAL"

            # 2. Lower Timeframe (15m)
            s_15 = replace(base, symbol=sym, interval="15")
            df_15 = client.closed_klines(512, symbol=sym, interval="15")
            fc_15 = forecast(s_15, df_15)
            sig_15 = build_signal(fc_15, df_15, s_15)

            # 3. Confluence Logic
            confluence_signal = "NEUTRAL"
            confluence_reason = "No multi-timeframe alignment"

            if trend_bias == "BULLISH" and sig_15.side == "Buy":
                confluence_signal = "STRONG BUY"
                confluence_reason = "1h Bullish Trend + 15m Buy Trigger aligned"
            elif trend_bias == "BEARISH" and sig_15.side == "Sell":
                confluence_signal = "STRONG SELL"
                confluence_reason = "1h Bearish Trend + 15m Sell Trigger aligned"
            elif trend_bias == "BULLISH" and sig_15.side == "Sell":
                confluence_signal = "CONFLICT / WAIT"
                confluence_reason = "1h Bullish Trend against 15m Sell pullback"
            elif trend_bias == "BEARISH" and sig_15.side == "Buy":
                confluence_signal = "CONFLICT / WAIT"
                confluence_reason = "1h Bearish Trend against 15m Buy bounce"
            elif sig_15.side is None:
                confluence_reason = f"1h {trend_bias}, but 15m has no statistical edge"

            # Get Instrument tick size
            inst = client.instrument(symbol=sym)
            tick = inst["priceFilter"]["tickSize"]
            entry = fc_15.last_close
            stop = sig_15.stop
            tp = sig_15.take_profit

            res = {
                "symbol": sym,
                "last_price": entry,
                "tf_60": {
                    "last_close": fc_60.last_close,
                    "prob_up": round(sig_60.prob, 3),
                    "expected_bps": round(sig_60.expected_bps, 1),
                    "q05_bps": round(sig_60.q05_bps, 1),
                    "q95_bps": round(sig_60.q95_bps, 1),
                    "side": sig_60.side or "None",
                    "trend_bias": trend_bias,
                    "reason": sig_60.reason,
                },
                "tf_15": {
                    "last_close": fc_15.last_close,
                    "prob_up": round(sig_15.prob, 3),
                    "expected_bps": round(sig_15.expected_bps, 1),
                    "q05_bps": round(sig_15.q05_bps, 1),
                    "q95_bps": round(sig_15.q95_bps, 1),
                    "side": sig_15.side or "None",
                    "stop": float(round_tick(stop, tick)) if stop else None,
                    "take_profit": float(round_tick(tp, tick)) if tp else None,
                    "atr_pct": round(sig_15.atr / entry * 100, 2),
                    "reason": sig_15.reason,
                },
                "confluence_signal": confluence_signal,
                "confluence_reason": confluence_reason,
            }
            results.append(res)
            print(f"[{sym}] 1h: {trend_bias} ({sig_60.prob:.2f} p_up, {sig_60.expected_bps:+.0f}bps) | "
                  f"15m: {sig_15.side or 'None'} ({sig_15.prob:.2f} p_up, {sig_15.expected_bps:+.0f}bps) => {confluence_signal}")
        except Exception as e:
            print(f"[{sym}] ERROR: {e}")
            results.append({
                "symbol": sym,
                "error": str(e)
            })

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pairs_scanned": len(SYMBOLS),
        "horizon_bars": horizon,
        "sample_paths": paths,
        "results": results
    }

    with open("data/confluence_signals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("\nScan completed successfully and saved to data/confluence_signals.json")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Timeframe Confluence Scanner")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--paths", type=int, default=2)
    args = parser.parse_args()
    run_scan(horizon=args.horizon, paths=args.paths)

