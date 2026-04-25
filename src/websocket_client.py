"""
💩 Kalshi WebSocket Client - REAL-TIME ORDERBOOK 💩

Proper implementation based on Kalshi docs:
- Auth via headers during WebSocket handshake
- Subscribe to orderbook_delta channel per market
- Orderbook format: yes/no arrays of [price, quantity]
- NO ask = 100 - YES bid (binary market relationship)

https://docs.kalshi.com/getting_started/quick_start_websockets
https://docs.kalshi.com/getting_started/orderbook_responses
"""

import asyncio
import json
import time
from typing import Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime

import websockets

from .auth import KalshiAuth
from .config import KalshiConfig


@dataclass
class OrderbookLevel:
    """Single price level: price in cents, quantity in contracts."""
    price: int
    quantity: int


@dataclass
class Orderbook:
    """
    💩 Live Orderbook State 💩

    Kalshi only provides BIDS. In binary markets:
    - YES bid @ 85¢ = NO ask @ 15¢ (you can BUY NO at 15¢)
    - NO bid @ 15¢ = YES ask @ 85¢ (you can BUY YES at 85¢)

    For stink theory: We want lowest NO ask = 100 - highest YES bid
    """
    ticker: str
    yes_bids: list[OrderbookLevel] = field(default_factory=list)  # Sorted ascending
    no_bids: list[OrderbookLevel] = field(default_factory=list)   # Sorted ascending
    last_update: float = 0.0
    update_count: int = 0

    @property
    def best_yes_bid(self) -> Optional[int]:
        """Highest YES bid price (last in sorted list)."""
        if not self.yes_bids:
            return None
        return max(l.price for l in self.yes_bids)

    @property
    def best_no_bid(self) -> Optional[int]:
        """Highest NO bid price."""
        if not self.no_bids:
            return None
        return max(l.price for l in self.no_bids)

    @property
    def no_ask(self) -> Optional[int]:
        """
        💩 THE KEY METRIC: Price to BUY NO contracts 💩
        NO ask = 100 - best YES bid
        """
        best_yes = self.best_yes_bid
        if best_yes is None:
            return None
        return 100 - best_yes

    @property
    def yes_ask(self) -> Optional[int]:
        """Price to BUY YES contracts = 100 - best NO bid."""
        best_no = self.best_no_bid
        if best_no is None:
            return None
        return 100 - best_no

    @property
    def no_ask_quantity(self) -> int:
        """Quantity available at best NO ask (= quantity at best YES bid)."""
        if not self.yes_bids:
            return 0
        best_price = self.best_yes_bid
        return sum(l.quantity for l in self.yes_bids if l.price == best_price)

    def is_stinky(self, min_price: int = 5, max_price: int = 20) -> bool:
        """💩 Is this a stinky opportunity? NO ask in target range."""
        ask = self.no_ask
        return ask is not None and min_price <= ask <= max_price

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "ticker": self.ticker,
            "no_ask": self.no_ask,
            "yes_ask": self.yes_ask,
            "best_yes_bid": self.best_yes_bid,
            "best_no_bid": self.best_no_bid,
            "no_ask_quantity": self.no_ask_quantity,
            "yes_bids": [[l.price, l.quantity] for l in self.yes_bids],
            "no_bids": [[l.price, l.quantity] for l in self.no_bids],
            "last_update": self.last_update,
            "update_count": self.update_count,
        }


class KalshiWebSocket:
    """
    💩 Real-time WebSocket Client 💩

    - Connects with header-based auth
    - Subscribes to orderbook_delta per market
    - Maintains live orderbook state
    - Calls callback on every update
    """

    PROD_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    DEMO_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"

    def __init__(
        self,
        config: KalshiConfig,
        on_orderbook_update: Callable[[Orderbook], None] = None,
        on_connection_change: Callable[[bool, str], None] = None,
    ):
        self.config = config
        self.auth = KalshiAuth(
            api_key_id=config.api_key_id,
            private_key_path=config.private_key_path,
            private_key_base64=config.private_key_base64,
        )
        self.on_orderbook_update = on_orderbook_update
        self.on_connection_change = on_connection_change

        self.ws = None
        self.orderbooks: dict[str, Orderbook] = {}
        self.subscribed_tickers: set[str] = set()
        self._running = False
        self._connected = False
        self._last_message_time = 0.0
        self._message_count = 0
        self._subscription_id = 1

        # Connection state
        self.connected_at: Optional[float] = None
        self.last_error: Optional[str] = None

        self.ws_url = self.DEMO_URL if config.environment == "demo" else self.PROD_URL

    def _get_auth_headers(self) -> dict:
        """Generate WebSocket auth headers."""
        headers = self.auth.get_auth_headers("GET", "/trade-api/ws/v2")
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": headers["KALSHI-ACCESS-SIGNATURE"],
            "KALSHI-ACCESS-TIMESTAMP": headers["KALSHI-ACCESS-TIMESTAMP"],
        }

    async def connect(self) -> bool:
        """Connect to WebSocket with header auth."""
        try:
            print(f"💩 Connecting to {self.ws_url}...")

            headers = self._get_auth_headers()
            self.ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
            )

            self._connected = True
            self.connected_at = time.time()
            self.last_error = None
            print("💩 WebSocket connected and authenticated!")

            if self.on_connection_change:
                self.on_connection_change(True, "Connected")

            return True

        except Exception as e:
            self._connected = False
            self.last_error = str(e)
            print(f"💩 Connection failed: {e}")

            if self.on_connection_change:
                self.on_connection_change(False, str(e))

            return False

    async def subscribe(self, ticker: str) -> bool:
        """Subscribe to orderbook updates for a market."""
        if not self.ws:
            print(f"💩 Cannot subscribe to {ticker}: no websocket")
            return False

        if not self._connected:
            print(f"💩 Cannot subscribe to {ticker}: not connected")
            return False

        if ticker in self.subscribed_tickers:
            print(f"💩 Already subscribed to {ticker}")
            return True

        try:
            self._subscription_id += 1
            msg = {
                "id": self._subscription_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_ticker": ticker
                }
            }

            print(f"💩 Sending subscribe for {ticker}: {json.dumps(msg)}")
            await self.ws.send(json.dumps(msg))
            self.subscribed_tickers.add(ticker)

            # Initialize empty orderbook
            if ticker not in self.orderbooks:
                self.orderbooks[ticker] = Orderbook(ticker=ticker)

            print(f"💩 Subscribed: {ticker}")
            return True

        except Exception as e:
            print(f"💩 Subscribe failed for {ticker}: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def unsubscribe(self, ticker: str) -> bool:
        """Unsubscribe from a market."""
        if not self.ws or ticker not in self.subscribed_tickers:
            return False

        try:
            self._subscription_id += 1
            msg = {
                "id": self._subscription_id,
                "cmd": "unsubscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_ticker": ticker
                }
            }

            await self.ws.send(json.dumps(msg))
            self.subscribed_tickers.discard(ticker)
            self.orderbooks.pop(ticker, None)

            print(f"💩 Unsubscribed: {ticker}")
            return True

        except Exception as e:
            print(f"💩 Unsubscribe failed: {e}")
            return False

    def _parse_orderbook_data(self, data: dict, ticker: str):
        """
        Parse orderbook snapshot or delta.

        Format: {"yes": [[price, qty], ...], "no": [[price, qty], ...]}
        Prices are in cents (integers), quantities are integers.
        """
        ob = self.orderbooks.get(ticker, Orderbook(ticker=ticker))

        # Parse YES bids
        if "yes" in data:
            yes_data = data["yes"]
            if isinstance(yes_data, list):
                # Full snapshot: replace all
                ob.yes_bids = []
                for level in yes_data:
                    if len(level) >= 2:
                        price = int(float(level[0]) * 100) if isinstance(level[0], str) and '.' in level[0] else int(level[0])
                        qty = int(float(level[1])) if isinstance(level[1], str) else int(level[1])
                        if qty > 0:
                            ob.yes_bids.append(OrderbookLevel(price=price, quantity=qty))

        # Parse NO bids
        if "no" in data:
            no_data = data["no"]
            if isinstance(no_data, list):
                ob.no_bids = []
                for level in no_data:
                    if len(level) >= 2:
                        price = int(float(level[0]) * 100) if isinstance(level[0], str) and '.' in level[0] else int(level[0])
                        qty = int(float(level[1])) if isinstance(level[1], str) else int(level[1])
                        if qty > 0:
                            ob.no_bids.append(OrderbookLevel(price=price, quantity=qty))

        ob.last_update = time.time()
        ob.update_count += 1
        self.orderbooks[ticker] = ob

        return ob

    async def _handle_message(self, raw: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(raw)
            self._last_message_time = time.time()
            self._message_count += 1

            msg_type = data.get("type", "")

            # Log first few messages for debugging
            if self._message_count <= 5:
                print(f"💩 WS msg #{self._message_count}: type={msg_type}, keys={list(data.keys())}")

            if msg_type in ("orderbook_snapshot", "orderbook_delta"):
                msg = data.get("msg", {})
                ticker = msg.get("market_ticker", "")

                if ticker:
                    ob = self._parse_orderbook_data(msg, ticker)
                    if self._message_count <= 10:
                        print(f"💩 Orderbook update: {ticker} NO_ask={ob.no_ask}¢")

                    if self.on_orderbook_update:
                        self.on_orderbook_update(ob)

            elif msg_type == "error":
                error_msg = data.get("msg", {}).get("msg", str(data))
                print(f"💩 WebSocket error: {error_msg}")
                self.last_error = error_msg

            elif msg_type == "subscribed":
                print(f"💩 Subscription confirmed: {data}")

            elif msg_type == "":
                # Could be a response to our command
                if "id" in data:
                    print(f"💩 Response to cmd {data.get('id')}: {data}")

        except json.JSONDecodeError:
            print(f"💩 Invalid JSON: {raw[:100]}")
        except Exception as e:
            print(f"💩 Message parse error: {e}")
            import traceback
            traceback.print_exc()

    async def run(self):
        """Main WebSocket loop with auto-reconnect."""
        self._running = True
        reconnect_delay = 1

        while self._running:
            try:
                if not self._connected:
                    if not await self.connect():
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30)
                        continue

                    reconnect_delay = 1

                    # Resubscribe to all tickers
                    for ticker in list(self.subscribed_tickers):
                        self.subscribed_tickers.discard(ticker)
                        await self.subscribe(ticker)
                        await asyncio.sleep(0.05)

                # Receive message
                message = await asyncio.wait_for(self.ws.recv(), timeout=30)
                await self._handle_message(message)

            except asyncio.TimeoutError:
                # No message in 30s, connection might be stale
                pass

            except websockets.exceptions.ConnectionClosed as e:
                print(f"💩 WebSocket closed: {e}")
                self._connected = False
                self.ws = None

                if self.on_connection_change:
                    self.on_connection_change(False, f"Disconnected: {e}")

                await asyncio.sleep(reconnect_delay)

            except Exception as e:
                print(f"💩 WebSocket error: {e}")
                self._connected = False
                await asyncio.sleep(1)

    async def stop(self):
        """Stop the WebSocket client."""
        self._running = False
        if self.ws:
            await self.ws.close()
            self.ws = None
        self._connected = False

        if self.on_connection_change:
            self.on_connection_change(False, "Stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def last_message_age(self) -> float:
        """Seconds since last message."""
        if self._last_message_time == 0:
            return float('inf')
        return time.time() - self._last_message_time

    def get_stinky_opportunities(self, min_price: int = 5, max_price: int = 20) -> list[Orderbook]:
        """💩 Get all orderbooks with NO ask in target range 💩"""
        return [
            ob for ob in self.orderbooks.values()
            if ob.is_stinky(min_price, max_price)
        ]

    def get_status(self) -> dict:
        """Get WebSocket status for UI."""
        return {
            "connected": self._connected,
            "url": self.ws_url,
            "connected_at": datetime.fromtimestamp(self.connected_at).isoformat() if self.connected_at else None,
            "uptime_seconds": time.time() - self.connected_at if self.connected_at else 0,
            "subscribed_count": len(self.subscribed_tickers),
            "subscribed_tickers": list(self.subscribed_tickers),
            "message_count": self._message_count,
            "last_message_age": self.last_message_age,
            "last_error": self.last_error,
        }
