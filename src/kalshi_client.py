"""
💩 Kalshi API client for REST operations 💩
"""

import json
import time
from typing import Optional
from dataclasses import dataclass

import requests

from .auth import KalshiAuth
from .config import KalshiConfig


@dataclass
class OrderResponse:
    """Response from order placement."""
    order_id: str
    ticker: str
    side: str
    action: str
    price: int
    count: int
    status: str
    filled_count: int = 0
    remaining_count: int = 0

    @classmethod
    def from_api(cls, data: dict) -> "OrderResponse":
        order = data.get("order", data)
        fill_count = order.get("fill_count", order.get("filled_count", 0))
        return cls(
            order_id=order.get("order_id", ""),
            ticker=order.get("ticker", ""),
            side=order.get("side", ""),
            action=order.get("action", ""),
            price=order.get("yes_price", order.get("no_price", 0)),
            count=order.get("count", order.get("initial_count", 0)),
            status=order.get("status", ""),
            filled_count=fill_count,
            remaining_count=order.get("remaining_count", 0),
        )


@dataclass
class MarketData:
    """Market data snapshot."""
    ticker: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    volume: int
    status: str
    close_time: str
    floor_strike: float = 0.0

    @classmethod
    def from_api(cls, data: dict) -> "MarketData":
        market = data.get("market", data)

        def to_cents(val) -> int:
            if val is None:
                return 0
            if isinstance(val, (int, float)):
                return int(val * 100) if val < 10 else int(val)
            try:
                return int(float(val) * 100)
            except:
                return 0

        return cls(
            ticker=market.get("ticker", ""),
            yes_bid=to_cents(market.get("yes_bid_dollars") or market.get("yes_bid", 0)),
            yes_ask=to_cents(market.get("yes_ask_dollars") or market.get("yes_ask", 0)),
            no_bid=to_cents(market.get("no_bid_dollars") or market.get("no_bid", 0)),
            no_ask=to_cents(market.get("no_ask_dollars") or market.get("no_ask", 0)),
            volume=int(float(market.get("volume_24h_fp", market.get("volume_24h", "0")) or "0")),
            status=market.get("status", ""),
            close_time=market.get("close_time", ""),
            floor_strike=float(market.get("floor_strike", 0) or 0),
        )


class KalshiClient:
    """
    💩 REST API client for Kalshi 💩
    """

    def __init__(self, config: KalshiConfig):
        self.config = config
        self.auth = KalshiAuth(
            api_key_id=config.api_key_id,
            private_key_path=config.private_key_path,
            private_key_base64=config.private_key_base64,
        )
        self.session = requests.Session()

    def _request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        """Make authenticated request to Kalshi API."""
        url = f"{self.config.api_url}{path}"
        body = json.dumps(data) if data else ""

        full_path = f"/trade-api/v2{path}"
        headers = self.auth.get_auth_headers(method, full_path, body)

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=body if data else None,
        )

        if response.status_code == 429:
            time.sleep(1)
            return self._request(method, path, params, data)

        response.raise_for_status()
        return response.json() if response.text else {}

    # ========== Account ==========

    def get_balance(self) -> dict:
        """Get account balance."""
        return self._request("GET", "/portfolio/balance")

    def get_balance_cents(self) -> int:
        """Get available balance in cents."""
        return self.get_balance().get("balance", 0)

    def get_balance_dollars(self) -> float:
        """Get available balance in dollars."""
        return self.get_balance_cents() / 100

    # ========== Markets ==========

    def get_markets(self, status: str = "open", limit: int = 200, series_ticker: str = None) -> dict:
        """Get list of markets."""
        params = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> MarketData:
        """Get single market data."""
        data = self._request("GET", f"/markets/{ticker}")
        return MarketData.from_api(data)

    def get_btc_15min_markets(self) -> list[MarketData]:
        """Get BTC 15-minute markets (all statuses for debugging)."""
        # Fetch without status filter to see all markets
        params = {"series_ticker": "KXBTC15M", "limit": 100}
        data = self._request("GET", "/markets", params=params)
        markets = []
        for m in data.get("markets", []):
            markets.append(MarketData.from_api(m))
        return markets

    # ========== Orders ==========

    def place_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        action: str,  # "buy" or "sell"
        count: int,
        price: int = None,  # cents, None for market order
        order_type: str = "market",
    ) -> OrderResponse:
        """Place an order."""
        data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }

        if order_type == "limit" and price:
            if side == "yes":
                data["yes_price"] = price
            else:
                data["no_price"] = price

        response = self._request("POST", "/portfolio/orders", data=data)
        return OrderResponse.from_api(response)

    def buy_no(self, ticker: str, count: int, price: int = None) -> OrderResponse:
        """💩 Buy NO contracts (our stinky longshot bet) 💩"""
        order_type = "limit" if price else "market"
        return self.place_order(ticker, "no", "buy", count, price, order_type)

    def get_orders(self, status: str = None, ticker: str = None) -> list[OrderResponse]:
        """Get list of orders."""
        params = {"limit": 100}
        if status:
            params["status"] = status
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/orders", params=params)
        return [OrderResponse.from_api(o) for o in data.get("orders", [])]

    def get_positions(self) -> list[dict]:
        """Get current positions."""
        data = self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    def get_fills(self, limit: int = 100) -> list[dict]:
        """Get fill history."""
        data = self._request("GET", "/portfolio/fills", params={"limit": limit})
        return data.get("fills", [])

    # ========== Orderbook ==========

    def get_orderbook(self, ticker: str) -> dict:
        """Get orderbook for a market."""
        return self._request("GET", f"/orderbook/{ticker}")
