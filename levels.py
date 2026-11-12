import pandas as pd

from bot.bybit import BybitClient, round_tick
from bot.config import load_settings

WANT = [
    ("ETHUSDT", "5"),
    ("SOLUSDT", "5"),
    ("DOGEUSDT", "5"),
    ("SOLUSDT", "15"),
    ("DOGEUSDT", "15"),
    ("DOGEUSDT", "60"),
    ("XRPUSDT", "60"),
]

s = load_settings()
c = BybitClient(s)
c.sync_time()
df = pd.read_csv("data/sweep_all.csv")

rows = []
for sym in {w[0] for w in WANT}:
    inst = c.instrument(symbol=sym)
    lot, pf = inst["lotSizeFilter"], inst["priceFilter"]
    for sym2, tf in WANT:
        if sym2 != sym:
            continue
        r = df[(df.symbol == sym) & (df.tf == tf)].iloc[0]
        entry = float(r["last"])
        atr = float(r["atr_pct"]) / 100
        dist = 1.5 * atr
        if r.signal == "Buy":
            stop = entry * (1 - dist)
            take = entry * (1 + 1.5 * dist)
        else:
            stop = entry * (1 + dist)
            take = entry * (1 - 1.5 * dist)
        tick = pf["tickSize"]
        rows.append({
            "symbol": sym, "tf": tf, "signal": r.signal,
            "entry": float(round_tick(entry, tick)),
            "stop": float(round_tick(stop, tick)),
            "tp": float(round_tick(take, tick)),
            "stop_pct": abs(stop - entry) / entry * 100,
            "tp_pct": abs(take - entry) / entry * 100,
            "prob": float(r["prob_up"]),
            "qty_10k": 100 / abs(entry - stop),
            "step": lot["qtyStep"],
        })

out = pd.DataFrame(rows)
out["qty_10k"] = [round(q, 6) for q in out["qty_10k"]]
print(out.to_string(index=False, float_format=lambda v: f"{v:,.6g}"))
out.to_csv("data/levels.csv", index=False)
print("\nsaved data/levels.csv")
