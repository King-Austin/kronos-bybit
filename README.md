# Kronos x Bybit demo harness

Local test rig for **Kronos** (AAAI 2026 candlestick foundation model) plugged into **Bybit demo trading**. Everything lives in this folder: venv, pinned Kronos source, model weights, logs.

## Verdict first

Kronos is a legitimate model, but the hype does not survive out-of-sample testing:

- The "+93% RankIC" headline is a relative gain from **0.014 to 0.027** — and it belongs to `Kronos-large`, which is **not released**. `Kronos-small` (what runs here) sits at 0.025.
- 749 post-cutoff forecasts on 25 FX/CFD markets: **50.2% direction accuracy**.
- 5-minute BTC vs geometric Brownian motion: no edge, overconfident tails.
- US equities, 53 weeks after release: +0.026 RankIC next day, **negative at 5 days**.
- 5,000 A-shares x 3 years for volatility: **loses to HAR-RV by 2.4-2.6x MAE**.
- Open bugs: fine-tune look-ahead leakage (#228, acknowledged), tokenizer layer count (#219), ~220 open issues, no tagged releases, last commit Apr 2026, live demo dead.

Treat this as a learning sandbox and a scenario generator, not an alpha source. Demo keys only.

## Measured on this machine

| Metric | Value |
|---|---|
| Fresh download (packages + repo + small model) | ~315 MB |
| Disk after setup | 0.88 GB |
| `Kronos-small` weights | 94.4 MB |
| Tokenizer | 15.1 MB |
| Inference, 8 bars x 1 path (CPU) | ~2.4 s |
| Inference, 8 bars x 4 paths (CPU) | ~9.7 s |
| Klines, 512 bars | ~41 KB |
| Bandwidth per 15m cycle | ~50 KB (~5 MB/day) |
| Peak RAM | under 2 GB |

## Quickstart

```powershell
cd C:\Users\kingaustin\Downloads\kronos-bybit
.\.venv\Scripts\Activate.ps1
```

Dry run (market data + forecast + signal, no orders):

```powershell
python -m bot.main
python -m bot.main --paths 4 --horizon 8 --interval 15
```

Test whether there is an edge on your symbol/timeframe **before** touching keys:

```powershell
python -m bot.evaluate --steps 60 --horizon 4 --interval 15
python -m bot.evaluate --steps 40 --horizon 8 --interval 60 --symbol ETHUSDT
```

Live demo orders: put demo keys in `.env`, set `DRY_RUN=false`, then:

```powershell
python -m bot.main --live
python -m bot.main --live --loop
python -m bot.main --close          # flatten the open position
```

Multi-timeframe confluence scanner (1h bias + 15m entry):

```powershell
python -m bot.confluence_scan
python -m bot.confluence_loop --live --interval 15 --circuit-breaker 950
```

## 24/7 VPS Deployment

A low-cost Linux VPS ($4-$6/mo, 1-2 vCPU, 2 GB RAM) runs this comfortably 24/7. In Europe or Asia, Bybit endpoints are unblocked.

1. Clone to VPS `/opt/kronos-bybit`.
2. Configure `.env` with your demo keys.
3. Run automated setup:
   ```bash
   chmod +x deploy/setup_vps.sh
   ./deploy/setup_vps.sh
   ```
4. Control with systemd:
   ```bash
   sudo systemctl status kronos-bybit
   sudo journalctl -u kronos-bybit -f
   ```

## Bybit demo wiring

1. Log in to the **mainnet** account, switch to **Demo Trading** (it has its own UID).
2. Avatar -> API -> create a key on the demo account. Testnet demo keys are useless.
3. Keys go in `.env` as `BYBIT_API_KEY` / `BYBIT_API_SECRET`. Never commit them.

| Purpose | Host |
|---|---|
| Market data | `https://api.bytick.com` (official alternate; `api.bybit.com` is blocked on this network) |
| Demo orders | `https://api-demo.bybit.com` |
| Public WS | `wss://stream.bybit.com` |
| Private WS | `wss://stream-demo.bybit.com` |

**Known blocker:** `api.bybit.com` and `api-demo.bybit.com` are connection-reset from this network, and `api-demo.bytick.com` does not exist. Market data works through `api.bytick.com`; **order execution needs a VPN/proxy** or a different network. Override hosts with `BYBIT_DATA_URL` / `BYBIT_TRADE_URL` in `.env`.

Demo facts: all market endpoints available, create/amend/cancel/history available, orders persist 7 days, default rate limits, no WebSocket trading. Klines are newest-first and include the unclosed bar — `bot/bybit.py` drops it.

## How the signal works

1. Pull the last 512 **closed** candles.
2. Kronos samples `SAMPLE_PATHS` autoregressive paths, `HORIZON` bars each.
3. `prob_up` = share of paths ending above the last close; `expected_bps` = mean terminal return.
4. Trade only if `prob_up >= PROB_THRESHOLD` (or the mirrored short condition) **and** `|expected| > 1.5x` round-trip cost (`2 * (FEE_BPS + SLIPPAGE_BPS)`).
5. Stop = `ATR_K * ATR(14)`, take profit = `RR * stop distance`, size = 1% equity risk capped by `MAX_LEVERAGE`, rounded to the instrument's `qtyStep`.
6. One position at a time, one-way mode (`positionIdx=0`). Market order with TP/SL attached.

Every cycle appends a JSON record to `data/trades.jsonl` with the forecast distribution, signal, and order response.

## Files

```
bot/config.py      env + settings + interval tables
bot/bybit.py       V5 REST client, HMAC signing, closed-candle filtering
bot/forecast.py    Kronos loader, multi-path forecast, prob_up/quantiles
bot/strategy.py    signal thresholds, ATR stops, position sizing
bot/main.py        dry run / loop / order placement
bot/evaluate.py    walk-forward accuracy, RankIC, edge net of costs
vendor/Kronos      pinned at 67b630e (MIT)
models/hf          HF_HOME, weights cached here
data/              trade log + state
```

## Before you trust any of it

- Run `evaluate.py` on the exact symbol/timeframe with at least 60 steps. If hit rate is near 0.50 and net edge is negative, that is the answer.
- The model has a persistent long bias in this setup; a rising market can fake a profit.
- Fees and slippage decide everything at these horizons. The default 15 bps round trip is optimistic for 15m scalps.
- Demo only. Do not point this at live keys.
