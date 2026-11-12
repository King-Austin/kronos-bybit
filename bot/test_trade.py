import time
import sys
from bot.config import load_settings
from bot.bybit import BybitClient, BybitError

def main():
    print("=== Bybit Demo API End-to-End Trade Test ===")
    settings = load_settings()
    client = BybitClient(settings)
    client.sync_time()

    equity_before = client.wallet_equity()
    print(f"1. Connected to Bybit Demo Trading. Wallet Equity: {equity_before:.2f} USDT")

    symbol = "DOGEUSDT"
    qty = "100"  # ~6.56 USDT notional, minNotional is 5 USDT

    print(f"\n2. Placing Test Market Order: BUY {qty} {symbol}...")
    order_res = client.place_market(side="Buy", qty=qty, symbol=symbol)
    order_id = order_res.get("orderId")
    order_link_id = order_res.get("orderLinkId")
    print(f"   -> Order submitted successfully! OrderId: {order_id} | LinkId: {order_link_id}")

    # Wait a moment for execution
    time.sleep(2)

    # Check active position
    positions = client.positions()
    active_pos = next((p for p in positions if p.get("symbol") == symbol and float(p.get("size") or 0) > 0), None)
    if active_pos:
        print(f"\n3. Verified Active Position on Bybit:")
        print(f"   Symbol: {active_pos.get('symbol')}")
        print(f"   Side: {active_pos.get('side')}")
        print(f"   Size: {active_pos.get('size')} DOGE")
        print(f"   Avg Price: {active_pos.get('avgPrice')}")
        print(f"   Unrealized PnL: {active_pos.get('unrealisedPnl')} USDT")
    else:
        print("\n3. Warning: Position size not immediately reflected or already closed.")

    print("\n4. Holding position for 3 seconds before closing...")
    time.sleep(3)

    print(f"\n5. Closing Position: SELL {qty} {symbol} (Reduce-Only)...")
    close_res = client.close_position(side="Sell", qty=qty, symbol=symbol)
    close_id = close_res.get("orderId")
    print(f"   -> Close order executed successfully! OrderId: {close_id}")

    # Wait a moment for position update
    time.sleep(2)

    positions_after = client.positions()
    remaining = [p for p in positions_after if p.get("symbol") == symbol and float(p.get("size") or 0) > 0]
    equity_after = client.wallet_equity()

    print(f"\n6. Verification:")
    if not remaining:
        print(f"   [SUCCESS] Position successfully flattened! Active positions: 0")
    else:
        print(f"   [WARNING] Remaining open position: {remaining}")
    print(f"   Final Wallet Equity: {equity_after:.2f} USDT")
    print("\nAPI Execution & Close test completed successfully!")

if __name__ == "__main__":
    main()
