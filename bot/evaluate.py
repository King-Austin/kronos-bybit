import argparse
import math
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from .bybit import BybitClient
from .config import INTERVAL_MS, load_settings
from .forecast import forecast


def fetch_history(client, settings, bars: int, symbol: str | None = None, interval: str | None = None) -> pd.DataFrame:
    frames = []
    end = None
    while sum(len(f) for f in frames) < bars:
        chunk = client.klines(limit=1000, end=end, symbol=symbol, interval=interval)
        if chunk.empty:
            break
        frames.append(chunk)
        end = int(chunk["start"].iloc[0]) - 1
        time.sleep(0.15)
    if not frames:
        raise RuntimeError("no history")
    df = pd.concat(frames).drop_duplicates("start").sort_values("start").reset_index(drop=True)
    iv = interval or settings.interval
    df = df[df["start"] + INTERVAL_MS[iv] <= int(time.time() * 1000)]
    return df.tail(bars).reset_index(drop=True)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward check: does Kronos beat a random walk here?")
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--horizon", type=int, default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--paths", type=int, default=1)
    args = parser.parse_args(argv)

    settings = load_settings()
    updates = {}
    if args.symbol:
        updates["symbol"] = args.symbol.upper()
    if args.interval:
        updates["interval"] = args.interval
    if args.horizon:
        updates["horizon"] = args.horizon
    if args.paths:
        updates["sample_paths"] = args.paths
    if updates:
        settings = replace(settings, **updates)
    settings = replace(settings, dry_run=True)

    client = BybitClient(settings)
    client.sync_time()
    needed = args.lookback + (settings.horizon + args.steps) + 10
    df = fetch_history(client, settings, needed)
    print(f"{settings.symbol} {settings.interval}: {len(df)} closed bars, horizon={settings.horizon}, "
          f"steps={args.steps}, lookback={args.lookback}")

    preds, realised, up_frac = [], [], []
    start = time.time()
    for i in range(args.steps):
        idx = len(df) - settings.horizon - args.steps + i
        if idx - args.lookback < 0:
            continue
        window = df.iloc[idx - args.lookback: idx].reset_index(drop=True)
        fc = forecast(settings, window, horizon=settings.horizon, sample_paths=1)
        realized = df["close"].iloc[idx + settings.horizon - 1] / df["close"].iloc[idx - 1] - 1
        preds.append(fc.expected_return)
        realised.append(float(realized))
        up_frac.append(1.0 if realized > 0 else 0.0)
        elapsed = time.time() - start
        print(f"\r{i + 1}/{args.steps}  {elapsed:6.1f}s  pred={preds[-1] * 1e4:+6.0f}bps "
              f"real={realised[-1] * 1e4:+6.0f}bps", end="", flush=True)
    print()

    if len(preds) < 5:
        print("not enough forecasts to score")
        return 1

    p = np.array(preds)
    r = np.array(realised)
    mask = (p != 0) & (r != 0)
    hit = float((np.sign(p[mask]) == np.sign(r[mask])).mean()) if mask.any() else float("nan")

    print("\nresults")
    print(f"forecasts            {len(p)}")
    print(f"direction hit rate   {hit:.3f}   (coin flip = 0.500)")
    print(f"share of up moves    {np.mean(up_frac):.3f}   (always-long hit rate)")
    print(f"spearman IC          {spearman(p, r):+.4f}")
    print(f"mean predicted      {p.mean() * 1e4:+.1f} bps")
    print(f"mean realised       {r.mean() * 1e4:+.1f} bps")
    print(f"MAE vs zero         {np.abs(r).mean() * 1e4:.1f} bps")
    print(f"MAE of forecast     {np.abs(r - p).mean() * 1e4:.1f} bps")
    print(f"costs per round trip {settings.round_trip_cost_bps:.1f} bps")
    edge = (np.sign(p) * r)[mask].mean() * 1e4 if mask.any() else float("nan")
    print(f"gross edge/trade    {edge:+.1f} bps   net {edge - settings.round_trip_cost_bps:+.1f} bps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
