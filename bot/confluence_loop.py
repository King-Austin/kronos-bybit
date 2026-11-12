import argparse
import json
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bot.bybit import BybitClient, BybitError, round_step, round_tick
from bot.config import INTERVAL_MS, load_settings
from bot.forecast import forecast
from bot.strategy import build_signal, position_size

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kronos-confluence")

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def get_seconds_until_next_candle(interval_minutes: int = 15, offset_sec: int = 3) -> float:
    now = time.time()
    interval_sec = interval_minutes * 60
    current_bucket = int(now // interval_sec) * interval_sec
    next_bucket = current_bucket + interval_sec
    wait_time = (next_bucket - now) + offset_sec
    return max(5.0, wait_time)


def run_cycle(client, base_settings, symbols: list[str], max_drawdown_equity: float = 950.0):
    equity = client.wallet_equity() or 1000.0
    logger.info("Starting confluence scan. Current account equity: %.2f USDT", equity)

    if equity < max_drawdown_equity:
        logger.error(
            "CIRCUIT BREAKER TRIGGERED! Equity (%.2f) below safety floor (%.2f). Halting orders.",
            equity,
            max_drawdown_equity,
        )
        return

    # Check open positions
    open_positions = {
        p["symbol"]: p
        for p in client.positions()
        if float(p.get("size") or 0) > 0
    }
    if open_positions:
        logger.info("Active positions: %s", list(open_positions.keys()))

    scan_records = []

    for sym in symbols:
        try:
            # 1. 1h Trend Bias
            s_60 = replace(base_settings, symbol=sym, interval="60")
            df_60 = client.closed_klines(512, symbol=sym, interval="60")
            fc_60 = forecast(s_60, df_60)
            sig_60 = build_signal(fc_60, df_60, s_60)

            if sig_60.prob >= 0.55 and sig_60.expected_bps > 0:
                trend_bias = "BULLISH"
            elif sig_60.prob <= 0.45 and sig_60.expected_bps < 0:
                trend_bias = "BEARISH"
            else:
                trend_bias = "NEUTRAL"

            # 2. 15m Entry Trigger
            s_15 = replace(base_settings, symbol=sym, interval="15")
            df_15 = client.closed_klines(512, symbol=sym, interval="15")
            fc_15 = forecast(s_15, df_15)
            sig_15 = build_signal(fc_15, df_15, s_15)

            confluence = "NEUTRAL"
            if trend_bias == "BULLISH" and sig_15.side == "Buy":
                confluence = "STRONG BUY"
            elif trend_bias == "BEARISH" and sig_15.side == "Sell":
                confluence = "STRONG SELL"

            logger.info(
                "[%s] 1h: %s (p=%.2f, exp=%+.0fbps) | 15m: %s (p=%.2f, exp=%+.0fbps) => %s",
                sym,
                trend_bias,
                sig_60.prob,
                sig_60.expected_bps,
                sig_15.side or "None",
                sig_15.prob,
                sig_15.expected_bps,
                confluence,
            )

            record = {
                "symbol": sym,
                "price": fc_15.last_close,
                "1h_bias": trend_bias,
                "15m_signal": sig_15.side,
                "confluence": confluence,
            }

            # 3. Trade Execution
            if confluence in {"STRONG BUY", "STRONG SELL"} and not base_settings.dry_run:
                if sym in open_positions:
                    logger.info("[%s] Already have an open position. Skipping duplicate entry.", sym)
                else:
                    inst = client.instrument(symbol=sym)
                    lot = inst["lotSizeFilter"]
                    pf = inst["priceFilter"]
                    qty = position_size(
                        equity=equity,
                        entry=sig_15.entry,
                        stop=sig_15.stop,
                        settings=s_15,
                        qty_step=lot["qtyStep"],
                        min_qty=float(lot["minOrderQty"]),
                        max_qty=float(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or 0) or None,
                    )
                    if qty > 0:
                        side = "Buy" if confluence == "STRONG BUY" else "Sell"
                        tp = round_tick(sig_15.take_profit, pf["tickSize"])
                        sl = round_tick(sig_15.stop, pf["tickSize"])
                        formatted_qty = round_step(qty, lot["qtyStep"])
                        logger.info(
                            "EXECUTING LIVE DEMO ORDER: %s %s %s | SL: %s | TP: %s",
                            side,
                            formatted_qty,
                            sym,
                            sl,
                            tp,
                        )
                        order_res = client.place_market(
                            side=side,
                            qty=formatted_qty,
                            take_profit=tp,
                            stop_loss=sl,
                            symbol=sym,
                        )
                        record["order"] = order_res
                        logger.info("[%s] Order placed successfully: %s", sym, order_res.get("orderId"))
            scan_records.append(record)
        except Exception as exc:
            logger.error("[%s] Evaluation error: %s", sym, exc)

    # Save summary
    out_path = Path("data/latest_confluence.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "scans": scan_records}, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="24/7 Multi-Timeframe Confluence Loop")
    parser.add_argument("--live", action="store_true", help="Send live demo orders (default is dry run)")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma separated symbols")
    parser.add_argument("--interval", type=int, default=15, help="Candle interval minutes (default: 15)")
    parser.add_argument("--circuit-breaker", type=float, default=950.0, help="Min equity before halting (default: 950)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    settings = load_settings()
    settings = replace(settings, dry_run=not args.live)

    client = BybitClient(settings)
    client.sync_time()

    logger.info("=== Kronos 24/7 Confluence Daemon Started ===")
    logger.info("Mode: %s | Watchlist: %s | Interval: %dm | Floor: %.2f USDT",
                "LIVE DEMO" if args.live else "DRY RUN", symbols, args.interval, args.circuit_breaker)

    while True:
        try:
            client.sync_time()
            run_cycle(client, settings, symbols, max_drawdown_equity=args.circuit_breaker)
        except KeyboardInterrupt:
            logger.info("Daemon interrupted by user. Exiting cleanly.")
            break
        except Exception as exc:
            logger.error("Unexpected error in main loop cycle: %s", exc)

        sleep_sec = get_seconds_until_next_candle(interval_minutes=args.interval, offset_sec=3)
        logger.info("Cycle complete. Sleeping %.1fs until next candle close (%s)...",
                    sleep_sec, datetime.now(timezone.utc).strftime("%H:%M:%S"))
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()

