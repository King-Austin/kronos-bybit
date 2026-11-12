import argparse
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from .bybit import BybitClient
from .config import load_settings
from .evaluate import fetch_history, spearman
from .forecast import forecast

COMBOS = [
    ("ETHUSDT", "5"),
    ("SOLUSDT", "5"),
    ("SOLUSDT", "15"),
    ("DOGEUSDT", "5"),
    ("DOGEUSDT", "15"),
    ("DOGEUSDT", "60"),
    ("XRPUSDT", "60"),
]


def run_combo(client, base, symbol: str, interval: str, steps: int, lookback: int) -> dict:
    s = replace(base, symbol=symbol, interval=interval)
    needed = lookback + s.horizon + steps + 10
    df = fetch_history(client, s, needed, symbol=symbol, interval=interval)
    print(f"  {symbol} {interval}: {len(df)} bars, close {df['close'].iloc[0]:.6g} -> {df['close'].iloc[-1]:.6g}")
    preds, realised = [], []
    for i in range(steps):
        idx = len(df) - s.horizon - steps + i
        if idx - lookback < 0:
            continue
        window = df.iloc[idx - lookback: idx].reset_index(drop=True)
        fc = forecast(s, window, horizon=s.horizon, sample_paths=s.sample_paths)
        realized = df["close"].iloc[idx + s.horizon - 1] / df["close"].iloc[idx - 1] - 1
        preds.append(fc.expected_return)
        realised.append(float(realized))
    p = np.array(preds)
    r = np.array(realised)
    mask = (p != 0) & (r != 0)
    hit = float((np.sign(p[mask]) == np.sign(r[mask])).mean()) if mask.any() else float("nan")
    edge = float((np.sign(p) * r)[mask].mean() * 1e4) if mask.any() else float("nan")
    return {
        "symbol": symbol,
        "tf": interval,
        "n": int(mask.sum()),
        "hit": hit,
        "ic": spearman(p, r),
        "pred_bps": float(p.mean() * 1e4),
        "real_bps": float(r.mean() * 1e4),
        "gross_bps": edge,
        "net_bps": edge - base.round_trip_cost_bps,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Quick edge check on the signaled combos")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--paths", type=int, default=2)
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--csv", default="data/edge_check.csv")
    parser.add_argument("--combos", default=None)
    args = parser.parse_args(argv)

    base = replace(load_settings(), dry_run=True, horizon=args.horizon,
                   sample_paths=args.paths)
    client = BybitClient(base)
    client.sync_time()

    combos = COMBOS
    if args.combos:
        combos = [tuple(x.split(":")) for x in args.combos.split(",")]
    rows = []
    for symbol, interval in combos:
        t0 = time.time()
        try:
            row = run_combo(client, base, symbol, interval, args.steps, args.lookback)
            rows.append(row)
            pd.DataFrame(rows).to_csv(args.csv, index=False)
            print(f"{symbol:9} {interval:>3}  n={row['n']:3}  hit {row['hit']:.3f}  "
                  f"IC {row['ic']:+.3f}  net {row['net_bps']:+6.0f}bps  ({time.time() - t0:.0f}s)")
        except Exception as exc:
            print(f"{symbol:9} {interval:>3}  ERROR {type(exc).__name__}: {exc}")
            rows.append({"symbol": symbol, "tf": interval, "n": 0})

    out = pd.DataFrame(rows)
    print()
    print(out.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print(f"\nsaved {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
