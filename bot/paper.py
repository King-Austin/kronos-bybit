import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .bybit import BybitClient, round_step
from .config import INTERVAL_MS, ROOT, load_settings
from .evaluate import fetch_history
from .forecast import forecast
from .strategy import build_signal, position_size

DEFAULT_STATE = ROOT / "data" / "paper_state.json"
DEFAULT_TRADES = ROOT / "data" / "paper_trades.jsonl"
BT_STATE = ROOT / "data" / "paper_bt_state.json"
BT_TRADES = ROOT / "data" / "paper_bt_trades.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


class Paper:
    def __init__(self, settings, client, state_path: Path, trades_path: Path,
                 equity: float, reset: bool = False):
        self.s = settings
        self.client = client
        self.state_path = state_path
        self.trades_path = trades_path
        self._inst = None
        if reset or not state_path.exists():
            self.state = {
                "equity": float(equity),
                "start_equity": float(equity),
                "peak_equity": float(equity),
                "symbol": settings.symbol,
                "interval": settings.interval,
                "position": None,
                "last_bar": None,
                "created": now_iso(),
            }
            self.save()
        else:
            self.state = json.loads(state_path.read_text())

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str))

    def instrument_lot(self) -> dict:
        if self._inst is None:
            self._inst = self.client.instrument(symbol=self.s.symbol)["lotSizeFilter"]
        return self._inst

    def fill(self, bar, sig) -> None:
        lot = self.instrument_lot()
        qty = position_size(
            equity=self.state["equity"], entry=sig.entry, stop=sig.stop, settings=self.s,
            qty_step=lot["qtyStep"], min_qty=float(lot["minOrderQty"]),
            max_qty=float(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or 0) or None,
        )
        if qty <= 0:
            print(f"  skip fill: size 0 (equity {self.state['equity']:.2f})")
            return
        slip = self.s.slippage_bps / 10_000
        raw = float(bar["open"])
        entry_px = raw * (1 + slip) if sig.side == "Buy" else raw * (1 - slip)
        pos = {
            "side": sig.side,
            "qty": qty,
            "entry": entry_px,
            "signal_entry": sig.entry,
            "sl": sig.stop,
            "tp": sig.take_profit,
            "risk_usdt": qty * abs(entry_px - sig.stop),
            "open_bar": int(bar["start"]),
            "open_ts": str(bar["timestamp"]),
            "bars_held": 0,
            "prob": sig.prob,
            "expected_bps": sig.expected_bps,
        }
        self.state["position"] = pos
        self.save()
        print(f"  FILL {sig.side} {qty} @ {entry_px:.6g}  SL {sig.stop:.6g}  TP {sig.take_profit:.6g}")

    def close(self, price: float, reason: str, ts: str) -> None:
        pos = self.state["position"]
        if not pos:
            return
        slip = self.s.slippage_bps / 10_000
        exit_px = price * (1 - slip) if pos["side"] == "Buy" else price * (1 + slip)
        qty = pos["qty"]
        pnl = qty * ((exit_px - pos["entry"]) if pos["side"] == "Buy" else (pos["entry"] - exit_px))
        fees = qty * (pos["entry"] + exit_px) * self.s.fee_bps / 10_000
        net = pnl - fees
        equity = self.state["equity"] + net
        notional = qty * pos["entry"]
        rec = {
            "event": "exit",
            "ts": now_iso(),
            "bar_ts": ts,
            "symbol": self.s.symbol,
            "interval": self.s.interval,
            "side": pos["side"],
            "qty": qty,
            "entry": pos["entry"],
            "exit": exit_px,
            "reason": reason,
            "bars_held": pos["bars_held"],
            "pnl": net,
            "fees": fees,
            "r_multiple": net / pos["risk_usdt"] if pos["risk_usdt"] else 0.0,
            "net_bps": net / notional * 10_000 if notional else 0.0,
            "equity_after": equity,
            "open_ts": pos["open_ts"],
            "prob": pos["prob"],
            "expected_bps": pos["expected_bps"],
        }
        _append(self.trades_path, rec)
        self.state["equity"] = equity
        self.state["peak_equity"] = max(self.state["peak_equity"], equity)
        self.state["position"] = None
        self.save()
        print(f"  EXIT {reason:8} @ {exit_px:.6g}  {net:+.2f} USDT  {rec['r_multiple']:+.2f}R  "
              f"{rec['net_bps']:+.0f}bps  equity {equity:.2f}")

    def on_bar(self, bar, window) -> None:
        pos = self.state["position"]
        if pos:
            pos["bars_held"] += 1
            high, low = float(bar["high"]), float(bar["low"])
            if pos["side"] == "Buy":
                if low <= pos["sl"]:
                    self.close(pos["sl"], "SL", str(bar["timestamp"]))
                elif high >= pos["tp"]:
                    self.close(pos["tp"], "TP", str(bar["timestamp"]))
                elif pos["bars_held"] >= self.s.horizon:
                    self.close(float(bar["close"]), "timeout", str(bar["timestamp"]))
            else:
                if high >= pos["sl"]:
                    self.close(pos["sl"], "SL", str(bar["timestamp"]))
                elif low <= pos["tp"]:
                    self.close(pos["tp"], "TP", str(bar["timestamp"]))
                elif pos["bars_held"] >= self.s.horizon:
                    self.close(float(bar["close"]), "timeout", str(bar["timestamp"]))
            self.save()
            if self.state["position"]:
                return
        fc = forecast(self.s, window, horizon=self.s.horizon, sample_paths=self.s.sample_paths)
        sig = build_signal(fc, window, self.s)
        print(f"  {bar['timestamp']}  close {fc.last_close:.6g}  exp {sig.expected_bps:+.0f}bps  "
              f"p_up {sig.prob:.2f}  -> {sig.side or '-'}")
        if sig.side:
            self.fill(bar, sig)

    def process(self, df: pd.DataFrame) -> int:
        last = self.state.get("last_bar")
        done = 0
        for idx in range(len(df)):
            bar = df.iloc[idx]
            if last is not None and int(bar["start"]) <= int(last):
                continue
            window = df.iloc[max(0, idx - self.s.lookback):idx].reset_index(drop=True)
            if len(window) < self.s.lookback:
                continue
            self.on_bar(bar, window)
            self.state["last_bar"] = int(bar["start"])
            self.save()
            done += 1
        return done

    def step(self) -> int:
        width = INTERVAL_MS[self.s.interval]
        last = self.state.get("last_bar")
        if last is None:
            pending = 6
        else:
            pending = max(1, int((time.time() * 1000 - int(last)) / width) + 2)
        if pending > 1000 - self.s.lookback:
            skipped = pending - (1000 - self.s.lookback)
            print(f"  downtime too long: skipping {skipped} old bars")
            pending = 1000 - self.s.lookback
        df = self.client.closed_klines(limit=self.s.lookback + pending,
                                       symbol=self.s.symbol, interval=self.s.interval)
        return self.process(df)

    def flatten(self, price: float, ts: str) -> None:
        if self.state["position"]:
            self.close(price, "end", ts)

    def show_open(self) -> None:
        pos = self.state["position"]
        if not pos:
            print("no open position")
            return
        df = self.client.closed_klines(limit=2, symbol=self.s.symbol, interval=self.s.interval)
        price = float(df["close"].iloc[-1])
        move = (price - pos["entry"]) if pos["side"] == "Buy" else (pos["entry"] - price)
        pnl = move * pos["qty"]
        risk = pos["risk_usdt"] or 1.0
        to_sl = abs(price - pos["sl"]) / price * 100
        to_tp = abs(price - pos["tp"]) / price * 100
        print(f"{pos['side']} {pos['qty']} @ {pos['entry']:.6g}  opened {pos['open_ts']}  "
              f"held {pos['bars_held']} bars")
        print(f"price {price:.6g}  unrealised {pnl:+.2f} USDT ({pnl / risk:+.2f}R)")
        print(f"to SL {to_sl:.2f}%   to TP {to_tp:.2f}%")


def stats(trades_path: Path) -> None:
    if not trades_path.exists():
        print("no trades yet")
        return
    rows = [json.loads(line) for line in trades_path.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("event") == "exit"]
    if not rows:
        print("no closed trades yet")
        return
    df = pd.DataFrame(rows)
    wins = (df["pnl"] > 0).sum()
    peak = df["equity_after"].cummax()
    dd = ((df["equity_after"] - peak) / peak).min() * 100
    print(df[["bar_ts", "side", "entry", "exit", "reason", "bars_held",
              "pnl", "r_multiple", "net_bps", "equity_after"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.4g}"))
    print()
    print(f"trades          {len(df)}")
    print(f"win rate        {wins / len(df):.1%}")
    print(f"avg R           {df['r_multiple'].mean():+.2f}")
    print(f"mean net        {df['net_bps'].mean():+.0f} bps")
    print(f"total PnL       {df['pnl'].sum():+.2f} USDT")
    print(f"final equity    {df['equity_after'].iloc[-1]:,.2f}")
    print(f"max drawdown    {dd:.2f}%")
    reasons = df["reason"].value_counts().to_dict()
    print(f"exits           {reasons}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Kronos paper trader (no exchange orders)")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--paths", type=int, default=4)
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--risk", type=float, default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--trades", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--step", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--backtest", type=int, default=0)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    backtest = args.backtest > 0
    state_path = Path(args.state) if args.state else (BT_STATE if backtest else DEFAULT_STATE)
    trades_path = Path(args.trades) if args.trades else (BT_TRADES if backtest else DEFAULT_TRADES)

    if args.report:
        stats(trades_path)
        return 0

    s = load_settings()
    updates = {"symbol": args.symbol.upper(), "interval": args.interval,
               "sample_paths": args.paths, "horizon": args.horizon, "dry_run": True}
    if args.risk is not None:
        updates["risk_pct"] = args.risk
    s = replace(s, **updates)

    client = BybitClient(s)
    client.sync_time()
    paper = Paper(s, client, state_path, trades_path, args.equity, reset=args.reset)

    if args.open:
        paper.show_open()
        return 0

    if backtest:
        bars = args.backtest
        df = fetch_history(client, s, s.lookback + bars + s.horizon + 5,
                           symbol=s.symbol, interval=s.interval)
        df = df.tail(s.lookback + bars).reset_index(drop=True)
        paper.state["last_bar"] = None
        paper.state["position"] = None
        paper.process(df)
        if paper.state["position"]:
            paper.flatten(float(df["close"].iloc[-1]), str(df["timestamp"].iloc[-1]))
        print()
        stats(trades_path)
        return 0

    if args.loop:
        width = INTERVAL_MS[s.interval] / 1000
        print(f"paper loop {s.symbol} {s.interval}  equity {paper.state['equity']:.2f}  "
              f"state {state_path}")
        while True:
            try:
                n = paper.step()
                if n:
                    print(f"processed {n} bar(s), equity {paper.state['equity']:.2f}")
            except Exception as exc:
                print(f"cycle failed: {type(exc).__name__}: {exc}")
            now = time.time()
            sleep_for = (now // width + 1) * width + 5 - now
            time.sleep(max(5.0, sleep_for))
    else:
        n = paper.step()
        if n == 0:
            print("no new closed bars")
        print(f"equity {paper.state['equity']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
