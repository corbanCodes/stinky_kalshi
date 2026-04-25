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

    def _parse_orderbook_data(self, data: dict, ticker: str, is_delta: bool = False):
        """
        Parse orderbook snapshot or delta.

        Snapshot format: {"yes_dollars_fp": [[price, qty], ...], "no_dollars_fp": [[price, qty], ...]}
        Delta format: {"price_dollars": "0.45", "delta_fp": "10", "side": "yes"}
        """
        ob = self.orderbooks.get(ticker, Orderbook(ticker=ticker))

        def parse_levels(levels_data, side_name: str = "") -> list[OrderbookLevel]:
            """Parse price levels from [[price, qty], ...] format."""
            result = []
            if not isinstance(levels_data, list):
                return result

            for i, level in enumerate(levels_data):
                if len(level) >= 2:
                    price_raw = level[0]
                    qty_raw = level[1]

                    # Convert price to cents
                    # Prices < 1 are in dollars (0.45 = 45 cents)
                    # Prices >= 1 are already in cents (45 = 45 cents)
                    price_float = float(price_raw) if isinstance(price_raw, str) else price_raw
                    if price_float < 1:
                        price = int(price_float * 100)
                    else:
                        price = int(price_float)

                    # Convert quantity
                    qty = int(float(qty_raw)) if isinstance(qty_raw, str) else int(qty_raw)

                    # Debug first few
                    if i < 2 and ob.update_count < 2 and side_name:
                        print(f"💩 PARSE {side_name}[{i}]: raw={price_raw} -> {price}¢, qty={qty}")

                    if qty > 0 and 1 <= price <= 99:
                        result.append(OrderbookLevel(price=price, quantity=qty))

            return result

        if is_delta:
            # Delta format: single price level change
            side = data.get("side", "")
            price_raw = data.get("price_dollars", 0)
            delta_raw = data.get("delta_fp", 0)

            # Convert price - same logic as snapshots
            price_float = float(price_raw) if price_raw else 0
            if price_float < 1:
                price = int(price_float * 100)
            else:
                price = int(price_float)
            delta = int(float(delta_raw)) if delta_raw else 0

            # Debug first few deltas
            if ob.update_count < 10:
                print(f"💩 DELTA {side}: price_raw={price_raw} -> {price}¢, delta={delta}")

            if side == "yes" and 1 <= price <= 99:
                # Delta is the NEW quantity at this price level (not a change)
                # Remove existing level at this price
                ob.yes_bids = [l for l in ob.yes_bids if l.price != price]
                # Add new level if quantity > 0
                if delta > 0:
                    ob.yes_bids.append(OrderbookLevel(price=price, quantity=delta))

            elif side == "no" and 1 <= price <= 99:
                # Delta is the NEW quantity at this price level (not a change)
                ob.no_bids = [l for l in ob.no_bids if l.price != price]
                if delta > 0:
                    ob.no_bids.append(OrderbookLevel(price=price, quantity=delta))

        else:
            # Snapshot format: full orderbook replacement
            # Debug: show all keys on first snapshot
            if ob.update_count < 2:
                print(f"💩 PARSE SNAPSHOT keys: {list(data.keys())}")

            # Check for yes_dollars_fp / no_dollars_fp keys
            yes_found = False
            for key in ["yes_dollars_fp", "yes_dollars", "yes"]:
                if key in data:
                    raw_data = data[key]
                    ob.yes_bids = parse_levels(raw_data, "YES")
                    yes_found = True
                    if ob.update_count < 2:
                        print(f"💩 PARSE YES using key='{key}' len={len(raw_data) if raw_data else 0}")
                        if raw_data and len(raw_data) > 0:
                            print(f"💩 PARSE YES first 3: {raw_data[:3]}")
                        print(f"💩 PARSE YES best_bid={ob.best_yes_bid}¢ -> NO_ask={ob.no_ask}¢")
                    break

            if not yes_found and ob.update_count < 2:
                print(f"💩 PARSE WARNING: No YES key found!")

            no_found = False
            for key in ["no_dollars_fp", "no_dollars", "no"]:
                if key in data:
                    raw_data = data[key]
                    ob.no_bids = parse_levels(raw_data, "NO")
                    no_found = True
                    if ob.update_count < 2:
                        print(f"💩 PARSE NO using key='{key}' len={len(raw_data) if raw_data else 0}")
                        if raw_data and len(raw_data) > 0:
                            print(f"💩 PARSE NO first 3: {raw_data[:3]}")
                        print(f"💩 PARSE NO best_bid={ob.best_no_bid}¢ -> YES_ask={ob.yes_ask}¢")
                    break

            if not no_found and ob.update_count < 2:
                print(f"💩 PARSE WARNING: No NO key found!")

            if ob.update_count < 2:
                print(f"💩 PARSE FINAL: YES_ask={ob.yes_ask}¢ + NO_ask={ob.no_ask}¢ = {(ob.yes_ask or 0) + (ob.no_ask or 0)}¢")

        ob.last_update = time.time()
        ob.update_count += 1
        self.orderbooks[ticker] = ob

        # Periodic state logging (every 100 updates)
        if ob.update_count % 100 == 0:
            print(f"💩 STATE [{ticker}] update #{ob.update_count}: YES_ask={ob.yes_ask}¢ NO_ask={ob.no_ask}¢ sum={(ob.yes_ask or 0)+(ob.no_ask or 0)}¢ (yes_bids={len(ob.yes_bids)} no_bids={len(ob.no_bids)})")

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
                is_delta = (msg_type == "orderbook_delta")

                # Debug: show full message structure for first few messages
                if self._message_count <= 5:
                    print(f"💩 {msg_type.upper()} MSG KEYS: {list(msg.keys())}")
                    for k, v in msg.items():
                        if k != "market_ticker":
                            if isinstance(v, list) and v:
                                print(f"💩 {msg_type.upper()} {k} (list len={len(v)}): first={v[0]}")
                            elif isinstance(v, dict):
                                print(f"💩 {msg_type.upper()} {k} (dict): keys={list(v.keys())}")
                            else:
                                print(f"💩 {msg_type.upper()} {k}: {v}")

                if ticker:
                    # For snapshots, orderbook data might be nested under 'orderbook' key
                    # For deltas, the data is at the msg level
                    if is_delta:
                        orderbook_data = msg
                    else:
                        orderbook_data = msg.get("orderbook", msg)

                    ob = self._parse_orderbook_data(orderbook_data, ticker, is_delta=is_delta)
                    if self._message_count <= 10:
                        print(f"💩 Orderbook {msg_type}: {ticker} NO_ask={ob.no_ask}¢ YES_ask={ob.yes_ask}¢ yes_bids={len(ob.yes_bids)} no_bids={len(ob.no_bids)}")

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
