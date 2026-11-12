import argparse
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from .bybit import BybitClient
from .config import load_settings
from .forecast import forecast
from .strategy import build_signal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-shot Kronos forecast table")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--horizon", type=int, default=None)
    parser.add_argument("--paths", type=int, default=None)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    updates = {"dry_run": True}
    if args.symbol:
        updates["symbol"] = args.symbol.upper()
    if args.interval:
        updates["interval"] = args.interval
    if args.horizon:
        updates["horizon"] = args.horizon
    if args.paths:
        updates["sample_paths"] = args.paths
    settings = replace(settings, **updates)

    client = BybitClient(settings)
    client.sync_time()
    df = client.closed_klines(512, interval=settings.interval)
    print(f"context: {len(df)} x {settings.interval}  {df.timestamp.iloc[0]} -> "
          f"{df.timestamp.iloc[-1]}  last close {df.close.iloc[-1]:,.1f}")

    fc = forecast(settings, df)
    sig = build_signal(fc, df, settings)

    rows = []
    for i in range(fc.horizon):
        col = fc.paths[:, i]
        rows.append({
            "bar": i + 1,
            "time": str(fc.mean.index[i]),
            "close": float(fc.mean["close"].iloc[i]),
            "p05": float(np.quantile(col, 0.05)),
            "p95": float(np.quantile(col, 0.95)),
            "p_up": float((col > fc.last_close).mean()),
        })
    out = pd.DataFrame(rows)
    print(out.to_string(index=False, float_format=lambda v: f"{v:,.1f}"))
    if args.csv:
        out.to_csv(args.csv, index=False)

    print()
    print(f"terminal mean {fc.terminal.mean():,.0f} ({fc.expected_return * 1e4:+.0f} bps), "
          f"prob_up {fc.prob_up:.2f}, q05 {fc.q05 * 1e4:+.0f} bps, q95 {fc.q95 * 1e4:+.0f} bps")
    side = sig.side or "none"
    stop = "-" if sig.stop is None else f"{sig.stop:,.0f}"
    take = "-" if sig.take_profit is None else f"{sig.take_profit:,.0f}"
    print(f"signal {side} | {sig.reason} | atr {sig.atr:,.0f} | stop {stop} | tp {take}")
    print(f"inference {fc.elapsed_s:.1f}s for {settings.sample_paths} paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
