"""
💩 Kalshi WebSocket client for real-time orderbook 💩

The orderbook shows all open orders at different price levels:

    NO SIDE (what we want to BUY)
    ─────────────────────────────
    SELL @ 15¢  [500 contracts]  ← Best ask (we buy here)
    SELL @ 14¢  [200 contracts]
    SELL @ 13¢  [50 contracts]
    ───────────────────────────
    BUY @ 12¢   [300 contracts]  ← Best bid
    BUY @ 11¢   [400 contracts]

When we see NO ask ≤ our target (10-15¢), we buy!
"""

import asyncio
import json
import time
from typing import Callable, Optional
from dataclasses import dataclass, field

import websockets

from .auth import KalshiAuth
from .config import KalshiConfig


@dataclass
class OrderbookLevel:
    """Single price level in orderbook."""
    price: int  # cents
    quantity: int  # contracts available


@dataclass
class Orderbook:
    """
    💩 Orderbook snapshot 💩

    For stink theory, we care about NO asks (prices to buy NO contracts).
    We want the lowest NO ask price.
    """
    ticker: str
    yes_bids: list[OrderbookLevel] = field(default_factory=list)
    yes_asks: list[OrderbookLevel] = field(default_factory=list)
    no_bids: list[OrderbookLevel] = field(default_factory=list)
    no_asks: list[OrderbookLevel] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def best_no_ask(self) -> Optional[int]:
        """💩 Best (lowest) price to buy NO contracts 💩"""
        if not self.no_asks:
            return None
        return min(level.price for level in self.no_asks)

    @property
    def best_no_ask_quantity(self) -> int:
        """Quantity available at best NO ask."""
        if not self.no_asks:
            return 0
        best_price = self.best_no_ask
        return sum(l.quantity for l in self.no_asks if l.price == best_price)

    @property
    def best_yes_ask(self) -> Optional[int]:
        """Best (lowest) price to buy YES contracts."""
        if not self.yes_asks:
            return None
        return min(level.price for level in self.yes_asks)

    def is_stinky_opportunity(self, max_price: int = 15) -> bool:
        """💩 Check if there's a stinky opportunity (NO ask ≤ target) 💩"""
        best = self.best_no_ask
        return best is not None and best <= max_price


class KalshiWebSocket:
    """
    💩 WebSocket client for real-time Kalshi data 💩

    Subscribes to orderbook updates for BTC 15-min markets.
    Calls your callback when orderbook changes.
    """

    WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

    def __init__(
        self,
        config: KalshiConfig,
        on_orderbook_update: Callable[[Orderbook], None] = None,
        on_market_update: Callable[[dict], None] = None,
    ):
        self.config = config
        self.auth = KalshiAuth(
            api_key_id=config.api_key_id,
            private_key_path=config.private_key_path,
            private_key_base64=config.private_key_base64,
        )
        self.on_orderbook_update = on_orderbook_update
        self.on_market_update = on_market_update

        self.ws = None
        self.orderbooks: dict[str, Orderbook] = {}
        self.subscribed_tickers: set[str] = set()
        self._running = False
        self._reconnect_delay = 1

    def _get_auth_message(self) -> dict:
        """Generate authentication message for websocket."""
        timestamp = str(int(time.time() * 1000))
        # For websocket, sign: timestamp + "GET" + "/trade-api/ws/v2"
        headers = self.auth.get_auth_headers("GET", "/trade-api/ws/v2")
        return {
            "id": 1,
            "cmd": "login",
            "params": {
                "api_key": self.config.api_key_id,
                "signature": headers["KALSHI-ACCESS-SIGNATURE"],
                "timestamp": headers["KALSHI-ACCESS-TIMESTAMP"],
            }
        }

    async def connect(self):
        """Connect to websocket and authenticate."""
        print("💩 Connecting to Kalshi WebSocket...")

        try:
            self.ws = await websockets.connect(self.WS_URL)
            print("💩 Connected! Authenticating...")

            # Authenticate
            auth_msg = self._get_auth_message()
            await self.ws.send(json.dumps(auth_msg))

            # Wait for auth response
            response = await self.ws.recv()
            data = json.loads(response)

            if data.get("type") == "error":
                raise Exception(f"Auth failed: {data}")

            print("💩 Authenticated successfully!")
            self._reconnect_delay = 1
            return True

        except Exception as e:
            print(f"💩 Connection failed: {e}")
            return False

    async def subscribe_orderbook(self, ticker: str):
        """Subscribe to orderbook updates for a ticker."""
        if not self.ws:
            return

        if ticker in self.subscribed_tickers:
            return

        msg = {
            "id": len(self.subscribed_tickers) + 10,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": [ticker]
            }
        }

        await self.ws.send(json.dumps(msg))
        self.subscribed_tickers.add(ticker)
        print(f"💩 Subscribed to orderbook: {ticker}")

    async def subscribe_markets(self, tickers: list[str]):
        """Subscribe to multiple market orderbooks."""
        for ticker in tickers:
            await self.subscribe_orderbook(ticker)
            await asyncio.sleep(0.1)  # Rate limit

    def _parse_orderbook(self, data: dict) -> Optional[Orderbook]:
        """Parse orderbook message into Orderbook object."""
        try:
            ticker = data.get("market_ticker") or data.get("ticker", "")
            if not ticker:
                return None

            ob = self.orderbooks.get(ticker, Orderbook(ticker=ticker))

            # Parse levels
            if "yes" in data:
                yes_data = data["yes"]
                if "bids" in yes_data:
                    ob.yes_bids = [
                        OrderbookLevel(price=int(l[0]), quantity=int(l[1]))
                        for l in yes_data["bids"]
                    ]
                if "asks" in yes_data:
                    ob.yes_asks = [
                        OrderbookLevel(price=int(l[0]), quantity=int(l[1]))
                        for l in yes_data["asks"]
                    ]

            if "no" in data:
                no_data = data["no"]
                if "bids" in no_data:
                    ob.no_bids = [
                        OrderbookLevel(price=int(l[0]), quantity=int(l[1]))
                        for l in no_data["bids"]
                    ]
                if "asks" in no_data:
                    ob.no_asks = [
                        OrderbookLevel(price=int(l[0]), quantity=int(l[1]))
                        for l in no_data["asks"]
                    ]

            ob.timestamp = time.time()
            self.orderbooks[ticker] = ob
            return ob

        except Exception as e:
            print(f"💩 Error parsing orderbook: {e}")
            return None

    async def _handle_message(self, message: str):
        """Handle incoming websocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "orderbook_snapshot" or msg_type == "orderbook_delta":
                ob = self._parse_orderbook(data.get("msg", data))
                if ob and self.on_orderbook_update:
                    self.on_orderbook_update(ob)

            elif msg_type == "market":
                if self.on_market_update:
                    self.on_market_update(data.get("msg", data))

            elif msg_type == "error":
                print(f"💩 WebSocket error: {data}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"💩 Error handling message: {e}")

    async def listen(self):
        """Listen for websocket messages."""
        self._running = True

        while self._running:
            try:
                if not self.ws:
                    if not await self.connect():
                        await asyncio.sleep(self._reconnect_delay)
                        self._reconnect_delay = min(self._reconnect_delay * 2, 30)
                        continue

                    # Resubscribe to tickers
                    for ticker in list(self.subscribed_tickers):
                        self.subscribed_tickers.discard(ticker)
                        await self.subscribe_orderbook(ticker)

                message = await self.ws.recv()
                await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed:
                print("💩 WebSocket disconnected, reconnecting...")
                self.ws = None
                await asyncio.sleep(self._reconnect_delay)

            except Exception as e:
                print(f"💩 WebSocket error: {e}")
                await asyncio.sleep(1)

    async def close(self):
        """Close websocket connection."""
        self._running = False
        if self.ws:
            await self.ws.close()
            self.ws = None

    def get_orderbook(self, ticker: str) -> Optional[Orderbook]:
        """Get cached orderbook for a ticker."""
        return self.orderbooks.get(ticker)

    def get_stinky_opportunities(self, max_price: int = 15) -> list[Orderbook]:
        """💩 Get all tickers with NO ask ≤ target price 💩"""
        opportunities = []
        for ob in self.orderbooks.values():
            if ob.is_stinky_opportunity(max_price):
                opportunities.append(ob)
        return opportunities
