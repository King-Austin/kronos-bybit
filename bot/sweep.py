import argparse
import sys
from dataclasses import replace

import pandas as pd

from .bybit import BybitClient
from .config import load_settings
from .forecast import forecast
from .strategy import build_signal

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
DEFAULT_INTERVALS = ["5", "15", "60", "240", "D"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sweep Kronos signals across symbols and timeframes")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--paths", type=int, default=4)
    parser.add_argument("--csv", default="data/sweep.csv")
    args = parser.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    base = replace(load_settings(), dry_run=True, horizon=args.horizon, sample_paths=args.paths)
    client = BybitClient(base)
    client.sync_time()

    rows = []
    total = len(symbols) * len(intervals)
    done = 0
    for sym in symbols:
        for iv in intervals:
            done += 1
            try:
                s = replace(base, symbol=sym, interval=iv)
                df = client.closed_klines(512, symbol=sym, interval=iv)
                fc = forecast(s, df)
                sig = build_signal(fc, df, s)
                rows.append({
                    "symbol": sym,
                    "tf": iv,
                    "last": fc.last_close,
                    "exp_bps": sig.expected_bps,
                    "prob_up": sig.prob,
                    "q05_bps": sig.q05_bps,
                    "q95_bps": sig.q95_bps,
                    "atr_pct": sig.atr / fc.last_close * 100,
                    "signal": sig.side or "-",
                })
                pd.DataFrame(rows).to_csv(args.csv, index=False)
                print(f"\r{done}/{total} {sym:9} {iv:>3}  exp {sig.expected_bps:+6.0f}bps  "
                      f"p_up {sig.prob:.2f}  {sig.side or '-':4}", end="", flush=True)
            except Exception as exc:
                rows.append({"symbol": sym, "tf": iv, "signal": "error", "prob_up": None,
                             "exp_bps": None, "q05_bps": None, "q95_bps": None,
                             "atr_pct": None, "last": None})
                print(f"\r{done}/{total} {sym:9} {iv:>3}  ERROR {type(exc).__name__}", flush=True)
    print()

    out = pd.DataFrame(rows)
    out.to_csv(args.csv, index=False)
    view = out[out["signal"] != "error"].copy()
    view = view.sort_values("exp_bps", ascending=False)
    print(view.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print(f"\nsaved {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
