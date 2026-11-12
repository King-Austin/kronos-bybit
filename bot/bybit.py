import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from urllib.parse import urlencode, urlparse

import pandas as pd
import requests

from .config import INTERVAL_MS, Settings
from .doh import ensure_dns


class BybitError(RuntimeError):
    pass


class BybitClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.http = session or requests.Session()
        self._offset_ms = 0
        for base in {settings.data_url, settings.trade_url}:
            ensure_dns(urlparse(base).hostname)

    def _timestamp(self) -> str:
        return str(int(time.time() * 1000) + self._offset_ms)

    def sync_time(self) -> None:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                started = time.time() * 1000
                r = self.http.get(f"{self.s.data_url}/v5/market/time", timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("retCode") != 0:
                    raise BybitError(data)
                server_ms = int(data["result"]["timeNano"]) // 1_000_000
                self._offset_ms = int(server_ms - (started + time.time() * 1000) / 2)
                return
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _auth_headers(self, payload: str) -> dict:
        ts = self._timestamp()
        recv = "5000"
        msg = f"{ts}{self.s.api_key}{recv}{payload}"
        sign = hmac.new(self.s.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": self.s.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, params: dict | None = None, private: bool = False) -> dict:
        params = params or {}
        base = self.s.trade_url if private else self.s.data_url
        url = f"{base}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                if method == "GET":
                    query = urlencode(sorted(params.items()))
                    headers = self._auth_headers(query) if private else {}
                    resp = self.http.get(f"{url}?{query}" if query else url, headers=headers, timeout=20)
                else:
                    body = json.dumps(params, separators=(",", ":"))
                    headers = self._auth_headers(body) if private else {"Content-Type": "application/json"}
                    resp = self.http.post(url, data=body, headers=headers, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") != 0:
                    raise BybitError(f"{data.get('retCode')} {data.get('retMsg')} {data.get('retExtInfo')}")
                return data["result"]
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def klines(self, limit: int = 1000, end: int | None = None, category: str | None = None,
               symbol: str | None = None, interval: str | None = None) -> pd.DataFrame:
        params = {
            "category": category or self.s.category,
            "symbol": symbol or self.s.symbol,
            "interval": interval or self.s.interval,
            "limit": min(int(limit), 1000),
        }
        if end is not None:
            params["end"] = int(end)
        result = self._call("GET", "/v5/market/kline", params)
        rows = result.get("list", [])
        if not rows:
            return pd.DataFrame(columns=["start", "open", "high", "low", "close", "volume", "amount"])
        df = pd.DataFrame(rows, columns=["start", "open", "high", "low", "close", "volume", "amount"])
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["start"] = df["start"].astype("int64")
        df = df.sort_values("start").reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["start"], unit="ms", utc=True)
        return df

    def closed_klines(self, limit: int, category: str | None = None, symbol: str | None = None,
                      interval: str | None = None) -> pd.DataFrame:
        interval = interval or self.s.interval
        df = self.klines(limit=limit + 5, category=category, symbol=symbol, interval=interval)
        if df.empty:
            raise BybitError("no kline data returned")
        self.sync_time()
        now_ms = int(time.time() * 1000) + self._offset_ms
        width = INTERVAL_MS[interval]
        df = df[df["start"] + width <= now_ms]
        if df.empty:
            raise BybitError("no closed candles available")
        return df.tail(limit).reset_index(drop=True)

    def ticker(self, category: str | None = None, symbol: str | None = None) -> dict:
        result = self._call("GET", "/v5/market/tickers", {
            "category": category or self.s.category,
            "symbol": symbol or self.s.symbol,
        })
        return result["list"][0]

    def instrument(self, category: str | None = None, symbol: str | None = None) -> dict:
        result = self._call("GET", "/v5/market/instruments-info", {
            "category": category or self.s.category,
            "symbol": symbol or self.s.symbol,
        })
        return result["list"][0]

    def wallet_equity(self) -> float | None:
        if not (self.s.api_key and self.s.api_secret):
            return None
        result = self._call("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, private=True)
        accounts = result.get("list", [])
        if not accounts:
            return None
        for coin in accounts[0].get("coin", []):
            if coin.get("coin") == "USDT":
                return float(coin.get("equity") or coin.get("walletBalance") or 0.0)
        return float(accounts[0].get("totalEquity") or 0.0)

    def set_leverage(self, leverage: float) -> None:
        if self.s.category not in {"linear", "inverse"}:
            return
        self._call("POST", "/v5/position/set-leverage", {
            "category": self.s.category,
            "symbol": self.s.symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }, private=True)

    def positions(self) -> list[dict]:
        result = self._call("GET", "/v5/position/list", {
            "category": self.s.category,
            "symbol": self.s.symbol,
        }, private=True)
        return result.get("list", [])

    def place_market(self, side: str, qty: str, take_profit: str | None = None,
                     stop_loss: str | None = None, reduce_only: bool = False,
                     symbol: str | None = None) -> dict:
        params = {
            "category": self.s.category,
            "symbol": symbol or self.s.symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
            "positionIdx": 0,
            "orderLinkId": f"kronos-{int(time.time() * 1000)}",
        }
        if reduce_only:
            params["reduceOnly"] = True
        elif take_profit and stop_loss:
            params.update({
                "takeProfit": take_profit,
                "stopLoss": stop_loss,
                "tpslMode": "Full",
                "tpOrderType": "Market",
                "slOrderType": "Market",
                "tpTriggerBy": "LastPrice",
                "slTriggerBy": "LastPrice",
            })
        return self._call("POST", "/v5/order/create", params, private=True)

    def close_position(self, side: str, qty: str, symbol: str | None = None) -> dict:
        return self.place_market(side=side, qty=qty, reduce_only=True, symbol=symbol)


def round_step(value: float, step: str) -> str:
    step_d = Decimal(str(step))
    v = Decimal(str(value)).quantize(step_d, rounding=ROUND_DOWN)
    return format(v.normalize(), "f")


def round_tick(value: float, tick: str) -> str:
    tick_d = Decimal(str(tick))
    v = (Decimal(str(value)) / tick_d).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_d
    return format(v.normalize(), "f")
