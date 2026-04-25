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
    open_time: str = ""
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
            open_time=market.get("open_time", ""),
            floor_strike=float(market.get("floor_strike", 0) or 0),
        )

    def is_currently_active(self) -> bool:
        """Check if market is currently tradeable based on time."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        try:
            # Parse ISO format times
            if self.open_time:
                open_dt = datetime.fromisoformat(self.open_time.replace('Z', '+00:00'))
                if now < open_dt:
                    return False  # Not open yet

            if self.close_time:
                close_dt = datetime.fromisoformat(self.close_time.replace('Z', '+00:00'))
                if now > close_dt:
                    return False  # Already closed

            return True  # Between open and close
        except Exception:
            # Fall back to status check
            return self.status in ("active", "open", "trading")


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

    def _request(self, method: str, path: str, params: dict = None, data: dict = None, debug: bool = False) -> dict:
        """Make authenticated request to Kalshi API."""
        url = f"{self.config.api_url}{path}"
        body = json.dumps(data) if data else ""

        full_path = f"/trade-api/v2{path}"
        headers = self.auth.get_auth_headers(method, full_path, body)

        if debug:
            print(f"💩 API REQUEST DEBUG:")
            print(f"💩   Method: {method}")
            print(f"💩   URL: {url}")
            print(f"💩   Path (for auth): {full_path}")
            print(f"💩   Params: {params}")
            print(f"💩   Body: {body}")
            print(f"💩   Headers (partial): Content-Type={headers.get('Content-Type')}")

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=body if data else None,
        )

        if debug:
            print(f"💩 API RESPONSE DEBUG:")
            print(f"💩   Status Code: {response.status_code}")
            print(f"💩   Response Text: {response.text[:500] if response.text else 'EMPTY'}")

        if response.status_code == 429:
            print(f"💩 RATE LIMITED - waiting 1s and retrying...")
            time.sleep(1)
            return self._request(method, path, params, data, debug)

        if response.status_code >= 400:
            print(f"💩 API ERROR: {response.status_code}")
            print(f"💩 Response: {response.text}")

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
        """Get BTC 15-minute markets - both open and upcoming."""
        markets = []

        # First get OPEN markets (currently tradeable)
        open_params = {"series_ticker": "KXBTC15M", "status": "open", "limit": 50}
        open_data = self._request("GET", "/markets", params=open_params)
        for m in open_data.get("markets", []):
            markets.append(MarketData.from_api(m))

        # Also get upcoming (initialized) markets for visibility
        upcoming_params = {"series_ticker": "KXBTC15M", "status": "unopened", "limit": 20}
        try:
            upcoming_data = self._request("GET", "/markets", params=upcoming_params)
            for m in upcoming_data.get("markets", []):
                markets.append(MarketData.from_api(m))
        except:
            pass

        print(f"💩 API returned: {len(open_data.get('markets', []))} open, {len(markets)} total")
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
        print(f"💩 ========== PLACE_ORDER CALLED ==========")
        print(f"💩 PARAMS: ticker={ticker}, side={side}, action={action}")
        print(f"💩 PARAMS: count={count}, price={price}, order_type={order_type}")

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
            print(f"💩 LIMIT ORDER: Adding {side}_price = {price}")
        elif order_type == "market":
            print(f"💩 MARKET ORDER: No price needed")
        else:
            print(f"💩 WARNING: order_type={order_type} but price={price}")

        print(f"💩 FINAL ORDER DATA: {json.dumps(data, indent=2)}")

        try:
            response = self._request("POST", "/portfolio/orders", data=data, debug=True)
            print(f"💩 ORDER RESPONSE RAW: {json.dumps(response, indent=2)}")

            order_response = OrderResponse.from_api(response)
            print(f"💩 ORDER PARSED:")
            print(f"💩   order_id: {order_response.order_id}")
            print(f"💩   status: {order_response.status}")
            print(f"💩   filled_count: {order_response.filled_count}")
            print(f"💩   remaining_count: {order_response.remaining_count}")

            return order_response

        except Exception as e:
            print(f"💩 ORDER EXCEPTION: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise

    def buy_no(self, ticker: str, count: int, price: int = None) -> OrderResponse:
        """💩 Buy NO contracts (our stinky longshot bet) 💩"""
        order_type = "limit" if price else "market"
        print(f"💩 ========== BUY_NO CALLED ==========")
        print(f"💩 ticker={ticker}, count={count}, price={price}")
        print(f"💩 order_type determined: {order_type}")
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

    def get_order(self, order_id: str) -> OrderResponse:
        """Get single order by ID."""
        print(f"💩 GET_ORDER: Fetching order {order_id}")
        data = self._request("GET", f"/portfolio/orders/{order_id}")
        print(f"💩 GET_ORDER RESPONSE: {json.dumps(data, indent=2)}")
        return OrderResponse.from_api(data)

    def verify_order_filled(self, order_id: str, max_wait: float = 5.0) -> tuple[bool, OrderResponse]:
        """
        Verify an order has been filled. Returns (filled, order_response).
        Waits up to max_wait seconds for fill confirmation.
        """
        print(f"💩 VERIFYING ORDER FILL: {order_id}")
        start = time.time()

        while time.time() - start < max_wait:
            try:
                order = self.get_order(order_id)
                print(f"💩 ORDER STATUS: {order.status}, filled={order.filled_count}/{order.count}")

                if order.status == "executed":
                    print(f"💩 ORDER FULLY EXECUTED!")
                    return True, order
                elif order.status == "canceled":
                    print(f"💩 ORDER WAS CANCELED!")
                    return False, order
                elif order.status == "resting":
                    print(f"💩 ORDER IS RESTING (limit order waiting)")
                elif order.filled_count > 0:
                    print(f"💩 PARTIAL FILL: {order.filled_count}/{order.count}")

                time.sleep(0.5)
            except Exception as e:
                print(f"💩 ERROR CHECKING ORDER: {e}")
                time.sleep(0.5)

        # Final check
        try:
            order = self.get_order(order_id)
            filled = order.status == "executed" or order.filled_count == order.count
            print(f"💩 FINAL CHECK: status={order.status}, filled={filled}")
            return filled, order
        except Exception as e:
            print(f"💩 FINAL CHECK ERROR: {e}")
            return False, None

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
