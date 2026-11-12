import argparse
import json
import sys
import time
from datetime import datetime, timezone

from .bybit import BybitClient, BybitError, round_step, round_tick
from .config import INTERVAL_MS, load_settings
from .forecast import forecast
from .strategy import build_signal, position_size, summarize_signal


def _log(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")


def _current_position(client) -> dict | None:
    try:
        for pos in client.positions():
            if float(pos.get("size") or 0) > 0:
                return pos
    except BybitError as exc:
        print(f"positions unavailable: {exc}")
    return None


def _equity(client, fallback: float = 10_000.0) -> float:
    try:
        value = client.wallet_equity()
        if value and value > 0:
            return value
    except BybitError as exc:
        print(f"wallet unavailable, using fallback equity: {exc}")
    return fallback


def _place(client, settings, inst, sig, equity: float) -> dict:
    lot = inst["lotSizeFilter"]
    price_filter = inst["priceFilter"]
    qty = position_size(
        equity=equity,
        entry=sig.entry,
        stop=sig.stop,
        settings=settings,
        qty_step=lot["qtyStep"],
        min_qty=float(lot["minOrderQty"]),
        max_qty=float(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or 0) or None,
    )
    if qty <= 0:
        return {"skipped": "size below minimum or zero risk budget"}

    try:
        client.set_leverage(settings.max_leverage)
    except BybitError as exc:
        print(f"set leverage skipped: {exc}")

    tp = round_tick(sig.take_profit, price_filter["tickSize"])
    sl = round_tick(sig.stop, price_filter["tickSize"])
    return client.place_market(
        side=sig.side,
        qty=round_step(qty, lot["qtyStep"]),
        take_profit=tp,
        stop_loss=sl,
    )


def run_once(settings, client, close_existing: bool = False) -> dict:
    df = client.closed_klines(limit=settings.lookback)
    print(f"candles: {len(df)} closed {settings.symbol} {settings.interval} "
          f"last={df['timestamp'].iloc[-1]} close={df['close'].iloc[-1]}")

    fc = forecast(settings, df)
    sig = build_signal(fc, df, settings)
    print(f"forecast: {fc.elapsed_s:.1f}s for {fc.horizon} bars x {settings.sample_paths} paths")
    print(f"last close {fc.last_close:.6f} -> mean {float(fc.mean['close'].iloc[-1]):.6f} "
          f"prob_up {fc.prob_up:.2f} q05 {fc.q05 * 100:+.2f}% q95 {fc.q95 * 100:+.2f}%")
    print(summarize_signal(sig))

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "env": settings.env,
        "symbol": settings.symbol,
        "interval": settings.interval,
        "last_close": fc.last_close,
        "last_candle": str(fc.last_timestamp),
        "predicted_close": float(fc.mean["close"].iloc[-1]),
        "expected_bps": sig.expected_bps,
        "prob_up": sig.prob,
        "q05_bps": sig.q05_bps,
        "q95_bps": sig.q95_bps,
        "signal": sig.side,
        "reason": sig.reason,
        "dry_run": settings.dry_run,
    }

    position = _current_position(client) if settings.api_key else None
    if position:
        print(f"open position exists: {position.get('side')} size={position.get('size')}")
        if close_existing:
            close_side = "Sell" if position.get("side") == "Buy" else "Buy"
            result = client.close_position(close_side, position["size"])
            record["closed"] = result
            print(f"closed: {result}")

    if sig.side and not position and not settings.dry_run:
        inst = client.instrument()
        equity = _equity(client)
        result = _place(client, settings, inst, sig, equity)
        record["order"] = result
        print(f"order: {result}")

    _log(settings.log_file, record)
    return record


def loop(settings, client, close_existing: bool = False) -> None:
    width = INTERVAL_MS[settings.interval] / 1000
    while True:
        started = time.time()
        try:
            run_once(settings, client, close_existing)
        except Exception as exc:
            print(f"cycle failed: {type(exc).__name__}: {exc}")
        elapsed = time.time() - started
        sleep_for = max(5.0, width - elapsed + 5.0)
        print(f"sleeping {sleep_for:.0f}s until next close\n")
        time.sleep(sleep_for)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Kronos x Bybit demo harness")
    parser.add_argument("--loop", action="store_true", help="run every candle close")
    parser.add_argument("--live", action="store_true", help="place demo orders (default is dry run)")
    parser.add_argument("--close", action="store_true", help="close an existing position first")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--paths", type=int, default=None)
    parser.add_argument("--horizon", type=int, default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.symbol or args.interval or args.model:
        from dataclasses import replace
        settings = replace(
            settings,
            symbol=(args.symbol or settings.symbol).upper(),
            interval=args.interval or settings.interval,
            model=args.model or settings.model,
        )
    if args.paths or args.horizon:
        from dataclasses import replace
        settings = replace(settings, sample_paths=args.paths or settings.sample_paths,
                           horizon=args.horizon or settings.horizon)
    if args.live:
        from dataclasses import replace
        settings = replace(settings, dry_run=False)
        if not (settings.api_key and settings.api_secret):
            print("missing BYBIT_API_KEY / BYBIT_API_SECRET in .env")
            return 2
    else:
        from dataclasses import replace
        settings = replace(settings, dry_run=True)

    print(f"env={settings.env} data={settings.data_url} trade={settings.trade_url} "
          f"symbol={settings.symbol} "
          f"interval={settings.interval} model={settings.model} dry_run={settings.dry_run}")

    client = BybitClient(settings)
    client.sync_time()

    if settings.dry_run and not settings.api_key:
        print("no keys: market data only")

    try:
        run_once(settings, client, close_existing=args.close)
        if args.loop:
            loop(settings, client, close_existing=args.close)
    except KeyboardInterrupt:
        return 130
    except (BybitError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
