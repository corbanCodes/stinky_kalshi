"""
2-Stage Recovery Martingale Trader

Uses WebSocket orderbook for fast entries at 80-92c anytime.
Two modes: Aggressive (50% S2) and Conservative (20% S2).

Unlike Stink bets, this is a "safe" high-probability system:
- Enters at 80-92c (high probability side) - backtested max 2 consecutive losses
- Uses BTC direction to pick YES or NO
- 2-stage recovery if base bet loses
- Auto-compounds on wins
- No time restriction ("anytime" has fewer max losses than "last 5 min")
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from .config import AppConfig, load_config
from .kalshi_client import KalshiClient, MarketData
from .recovery_stages import RecoveryState, RecoveryCalculator


@dataclass
class RecoveryTradeRecord:
    """Record of a single recovery trade."""
    timestamp: str
    ticker: str
    side: str
    stage: int  # 0=base, 1=stage1, 2=stage2
    contracts: int
    entry_price: int
    fill_price: int
    cost: float
    result: str = "pending"  # "win", "loss", "pending"
    profit: float = 0.0
    round_number: int = 1


class RecoveryTrader:
    """
    2-Stage Recovery Martingale Trader.

    Uses WebSocket orderbook for fast entries.
    Entry: 80-92c ask, anytime during the market.
    BTC > Strike = YES, BTC < Strike = NO.

    Backtested parameters (959k rows):
    - 80-92c @ anytime: max 2 consecutive losses
    - 80-87c @ anytime: max 3 consecutive losses
    - Waiting for last 5 min: max 4 consecutive losses (WORSE!)
    """

    def __init__(self, config: AppConfig = None):
        self.config = config or load_config()
        self.client = KalshiClient(self.config.kalshi)
        self.calc = RecoveryCalculator()

        # State
        self.state_path = self.config.data_dir / "recovery_state.json"
        self.state = RecoveryState.load(self.state_path)

        # Trade history
        self.history_path = self.config.data_dir / "recovery_history.json"
        self.trade_history: list[RecoveryTradeRecord] = []
        self._load_history()

        # Track traded tickers (prevent double-betting same window)
        self._traded_tickers: set[str] = set()

        # Mode: "aggressive" or "conservative"
        self.mode = "conservative"

        # Slippage (3c)
        self.slippage_cents = 3

        # Balance
        self.balance = 0.0
        self.refresh_balance()

        print("=" * 60)
        print("2-STAGE RECOVERY TRADER INITIALIZED")
        print("=" * 60)
        print(f"  Mode: {self.mode.upper()}")
        print(f"  Balance: ${self.balance:.2f}")
        print(f"  Current Stage: {self.state.current_stage}")
        print(f"  Round: #{self.state.round_number}")
        print(f"  Entry Range: 80-92c (95c with slippage)")
        print(f"  Time Window: Anytime (backtested optimal)")
        print("=" * 60)

    def _load_history(self):
        """Load trade history from disk."""
        if self.history_path.exists():
            with open(self.history_path) as f:
                data = json.load(f)
                self.trade_history = [RecoveryTradeRecord(**r) for r in data]

    def _save_history(self):
        """Save trade history to disk."""
        with open(self.history_path, "w") as f:
            json.dump([asdict(r) for r in self.trade_history], f, indent=2)

    def log(self, message: str, level: str = "INFO"):
        """Log with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = {
            "INFO": "[INFO ]",
            "TRADE": "[TRADE]",
            "WIN": "[ WIN ]",
            "LOSS": "[LOSS ]",
            "ERROR": "[ERROR]",
            "WARN": "[WARN ]",
        }.get(level, "[INFO ]")
        print(f"{timestamp} {prefix} {message}")

    def refresh_balance(self):
        """Refresh balance from Kalshi."""
        try:
            self.balance = self.client.get_balance_dollars()
            self.log(f"Balance: ${self.balance:.2f}")
        except Exception as e:
            self.log(f"Could not refresh balance: {e}", "ERROR")

    def set_mode(self, mode: str):
        """Set trading mode (aggressive or conservative)."""
        if mode in ("aggressive", "conservative"):
            self.mode = mode
            self.log(f"Mode set to: {mode.upper()}")

    def get_btc_price(self) -> Optional[float]:
        """Get current BTC price from Kraken."""
        import requests
        try:
            resp = requests.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": "XBTUSD"},
                timeout=5
            )
            data = resp.json()
            price = float(data["result"]["XXBTZUSD"]["c"][0])
            return price
        except Exception as e:
            self.log(f"Error getting BTC price: {e}", "ERROR")
            return None

    def get_btc_direction(self, floor_strike: float) -> Optional[tuple[str, float]]:
        """
        Get BTC direction relative to strike.

        Returns:
            (direction, btc_price) where direction is "above" or "below"
        """
        btc_price = self.get_btc_price()
        if btc_price is None:
            return None

        if btc_price > floor_strike:
            return "above", btc_price
        else:
            return "below", btc_price

    def find_opportunity_from_orderbook(self, orderbooks: dict) -> Optional[dict]:
        """
        Find a recovery opportunity using REST for market data + WebSocket for live prices.

        The WebSocket orderbooks have live prices but not close_time/floor_strike,
        so we use REST to get market metadata and WebSocket for price confirmation.

        Args:
            orderbooks: Dict of ticker -> Orderbook from WebSocket client

        Returns:
            dict with ticker, side, entry_price, minutes_remaining, floor_strike
        """
        # First, get market data from REST (has close_time and floor_strike)
        opportunity = self.find_opportunity_rest()

        if opportunity is None:
            return None

        ticker = opportunity["ticker"]

        # If we have a WebSocket orderbook for this ticker, use its live price
        if ticker in orderbooks:
            ob = orderbooks[ticker]
            side = opportunity["side"]

            # Get live price from WebSocket
            if side == "yes":
                live_price = ob.yes_ask
            else:
                live_price = ob.no_ask

            if live_price is not None:
                # Use live WebSocket price (more up-to-date)
                opportunity["entry_price"] = live_price

                # Verify still in valid range (80-87c)
                if live_price < self.calc.MIN_ENTRY_PRICE or live_price > self.calc.MAX_ENTRY_PRICE:
                    return None

        return opportunity

    def find_opportunity_rest(self) -> Optional[dict]:
        """
        Find a recovery opportunity using REST API (fallback).

        Returns:
            dict with ticker, side, entry_price, minutes_remaining, floor_strike
        """
        now = datetime.now(timezone.utc)

        try:
            markets = self.client.get_btc_15min_markets()

            for market in markets:
                ticker = market.ticker

                # Skip if already traded
                if ticker in self._traded_tickers:
                    continue

                # Check status
                if market.status not in ("open", "active"):
                    continue

                # Parse close time
                try:
                    close_time = datetime.fromisoformat(market.close_time.replace('Z', '+00:00'))
                except:
                    continue

                minutes_remaining = (close_time - now).total_seconds() / 60

                # Anytime is fine - backtesting shows this is optimal
                # Just need at least 30 seconds to get filled
                if minutes_remaining < 0.5 or minutes_remaining > 15.0:
                    continue

                # Get floor strike
                floor_strike = market.floor_strike
                if floor_strike == 0:
                    continue

                # Get BTC direction
                direction_result = self.get_btc_direction(floor_strike)
                if direction_result is None:
                    continue

                direction, btc_price = direction_result

                # Determine side and price
                if direction == "above":
                    side = "yes"
                    entry_price = market.yes_ask
                else:
                    side = "no"
                    entry_price = market.no_ask

                # Check price range (80-87c)
                if entry_price == 0:
                    continue
                if entry_price < self.calc.MIN_ENTRY_PRICE:
                    continue
                if entry_price > self.calc.MAX_ENTRY_PRICE:
                    continue

                return {
                    "ticker": ticker,
                    "side": side,
                    "entry_price": entry_price,
                    "minutes_remaining": minutes_remaining,
                    "floor_strike": floor_strike,
                    "btc_price": btc_price,
                    "btc_direction": direction,
                    "close_time": close_time,
                }

        except Exception as e:
            self.log(f"Error scanning REST: {e}", "ERROR")

        return None

    def get_contracts_for_stage(self, stage: int, entry_price: int) -> Optional[int]:
        """
        Calculate contracts for the given stage.

        Args:
            stage: 0=base, 1=stage1, 2=stage2
            entry_price: Entry price in cents

        Returns:
            Number of contracts or None if can't calculate
        """
        if stage == 0:
            # Base bet
            sizing = self.calc.calculate_bet_sizing(self.balance, self.mode)
            if not sizing.get("valid"):
                self.log(f"Cannot calculate base: {sizing.get('error')}", "ERROR")
                return None
            contracts = sizing["base_contracts"]
            self.state.base_contracts = contracts
            return contracts

        else:
            # Recovery bet
            loss_to_recover = self.state.total_loss_cents / 100
            recovery = self.calc.calculate_recovery_bet(loss_to_recover, entry_price)
            if not recovery:
                self.log(f"Cannot calculate Stage {stage}: invalid recovery", "ERROR")
                return None

            contracts = recovery["contracts"]

            # Verify Stage 2 doesn't exceed cap
            if stage == 2:
                if self.mode == "aggressive":
                    max_cost = self.balance * 0.50
                else:
                    max_cost = self.balance * 0.20

                fill_price = entry_price + self.slippage_cents
                estimated_cost = contracts * (fill_price / 100)

                if estimated_cost > max_cost:
                    # Cap contracts
                    contracts = int(max_cost / (fill_price / 100))
                    self.log(f"Stage 2 capped at {contracts} contracts (${max_cost:.2f} max)", "WARN")
                    if contracts < 1:
                        return None

            return contracts

    def execute_recovery_bet(
        self,
        opportunity: dict,
        orderbooks: dict = None
    ) -> Optional[RecoveryTradeRecord]:
        """
        Execute a recovery bet.

        Args:
            opportunity: Dict with ticker, side, entry_price, etc.
            orderbooks: WebSocket orderbooks for live price check

        Returns:
            RecoveryTradeRecord or None
        """
        ticker = opportunity["ticker"]
        side = opportunity["side"]
        entry_price = opportunity["entry_price"]
        stage = self.state.current_stage
        stage_name = ["BASE", "STAGE 1", "STAGE 2"][stage]

        # Check if already traded
        if ticker in self._traded_tickers:
            self.log(f"Already traded {ticker}", "WARN")
            return None

        # Get contracts for this stage
        contracts = self.get_contracts_for_stage(stage, entry_price)
        if contracts is None or contracts < 1:
            return None

        # Calculate limit price (add slippage)
        limit_price = entry_price + self.slippage_cents
        if limit_price > 95:
            limit_price = 95  # Cap at 95c (92c max entry + 3c slippage)

        # Estimate cost
        cost = contracts * (limit_price / 100)

        # Verify we can afford
        if cost > self.balance:
            self.log(f"Cannot afford: ${cost:.2f} > ${self.balance:.2f}", "ERROR")
            return None

        # Log the trade
        self.log("=" * 60, "TRADE")
        self.log(f"RECOVERY {stage_name} BET", "TRADE")
        self.log(f"  Mode: {self.mode.upper()}", "TRADE")
        self.log(f"  Ticker: {ticker}", "TRADE")
        self.log(f"  Side: {side.upper()}", "TRADE")
        self.log(f"  BTC: ${opportunity['btc_price']:,.0f} {opportunity['btc_direction']} strike ${opportunity['floor_strike']:,.0f}", "TRADE")
        self.log(f"  Entry: {entry_price}c -> Limit: {limit_price}c", "TRADE")
        self.log(f"  Contracts: {contracts}", "TRADE")
        self.log(f"  Est. Cost: ${cost:.2f}", "TRADE")
        if stage > 0:
            self.log(f"  Recovering: ${self.state.total_loss_cents / 100:.2f}", "TRADE")

        try:
            # Place the order
            order = self.client.buy_stinky(
                ticker=ticker,
                side=side,
                count=contracts,
                price=limit_price  # Limit order
            )

            self.log(f"Order placed: {order.order_id}", "TRADE")
            self.log(f"Status: {order.status}, Filled: {order.filled_count}", "TRADE")

            # Verify fill
            if order.order_id:
                filled, verified_order = self.client.verify_order_filled(order.order_id, max_wait=5.0)
                if verified_order:
                    order = verified_order
                    self.log(f"Verified: {order.status}, filled={order.filled_count}", "TRADE")

                if not filled:
                    self.log(f"Order may not be fully filled!", "WARN")

            # Mark as traded
            self._traded_tickers.add(ticker)

            # Record trade
            actual_fill = order.price if order.price > 0 else limit_price
            actual_cost = contracts * (actual_fill / 100)

            record = RecoveryTradeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                ticker=ticker,
                side=side,
                stage=stage,
                contracts=contracts,
                entry_price=entry_price,
                fill_price=actual_fill,
                cost=actual_cost,
                result="pending",
                round_number=self.state.round_number,
            )

            self.trade_history.append(record)
            self._save_history()

            # Save state
            self.state.save(self.state_path)

            # Refresh balance
            self.refresh_balance()

            self.log(f"Recovery bet placed successfully!", "TRADE")
            return record

        except Exception as e:
            import traceback
            self.log(f"Order failed: {e}", "ERROR")
            traceback.print_exc()
            return None

    def check_settlements(self):
        """
        Check for settled markets and update state.
        """
        print(f"[RECOVERY] Checking settlements for {len(self.trade_history)} trades...")

        pending = [t for t in self.trade_history if t.result == "pending"]
        print(f"[RECOVERY] Found {len(pending)} pending trades")

        if not pending:
            return

        for trade in pending:
            ticker = trade.ticker
            print(f"[RECOVERY] Checking: {ticker} (stage={trade.stage})")

            try:
                is_settled, result = self.client.check_market_settlement(ticker)

                if is_settled:
                    print(f"[RECOVERY] SETTLED! {ticker} result={result}, our side={trade.side}")

                    # Determine win/loss
                    if result:
                        won = (trade.side == result)
                    else:
                        # Fallback: check settlements endpoint
                        settlements = self.client.get_settlements(limit=50)
                        won = False
                        for s in settlements:
                            if s.get("ticker") == ticker:
                                revenue = s.get("revenue", 0)
                                if revenue > 0:
                                    won = True
                                break

                    # Calculate profit/loss
                    fee = self.calc.calc_fee(trade.fill_price) * trade.contracts
                    loss_cents = int(trade.cost * 100) + int(fee * 100)

                    if won:
                        # WIN!
                        net_profit_per = self.calc.calc_net_profit(trade.fill_price)
                        profit = trade.contracts * net_profit_per
                        trade.result = "win"
                        trade.profit = profit

                        self.log("=" * 60, "WIN")
                        stage_name = ["BASE", "STAGE 1", "STAGE 2"][trade.stage]
                        self.log(f"RECOVERY {stage_name} WON!", "WIN")
                        self.log(f"  Profit: +${profit:.2f}", "WIN")

                        if trade.stage > 0:
                            recovered = self.state.total_loss_cents / 100
                            net_gain = profit - recovered
                            self.log(f"  Recovered: ${recovered:.2f}", "WIN")
                            self.log(f"  Net gain: +${net_gain:.2f}", "WIN")

                        # Update state
                        self.state.on_win(profit)
                        self.state.reset_to_base()

                    else:
                        # LOSS
                        loss_dollars = loss_cents / 100
                        trade.result = "loss"
                        trade.profit = -loss_dollars

                        self.log("=" * 60, "LOSS")
                        stage_name = ["BASE", "STAGE 1", "STAGE 2"][trade.stage]
                        self.log(f"RECOVERY {stage_name} LOST", "LOSS")
                        self.log(f"  Loss: -${loss_dollars:.2f}", "LOSS")

                        if trade.stage == 0:
                            # Base lost -> Stage 1
                            self.state.advance_to_stage1(loss_cents)
                            self.log(f"  -> Advancing to STAGE 1", "LOSS")
                        elif trade.stage == 1:
                            # Stage 1 lost -> Stage 2
                            self.state.advance_to_stage2(loss_cents)
                            total_loss = self.state.total_loss_cents / 100
                            self.log(f"  -> Advancing to STAGE 2 (total: ${total_loss:.2f})", "LOSS")
                        else:
                            # Stage 2 lost -> Give up
                            total_loss = (self.state.total_loss_cents + loss_cents) / 100
                            self.log(f"  -> STAGE 2 FAILED", "LOSS")
                            self.log(f"  -> Total loss: -${total_loss:.2f}", "LOSS")
                            self.state.on_loss(total_loss)
                            self.state.reset_to_base()

                    # Save state and history
                    self._save_history()
                    self.state.save(self.state_path)

                    # Remove from traded tickers
                    self._traded_tickers.discard(ticker)

                    # Refresh balance
                    self.refresh_balance()

            except Exception as e:
                import traceback
                print(f"[RECOVERY] Error checking settlement: {e}")
                traceback.print_exc()

    def get_status(self) -> dict:
        """Get current trader status."""
        sizing = self.calc.calculate_bet_sizing(self.balance, self.mode)

        return {
            "balance": self.balance,
            "mode": self.mode,
            "current_stage": self.state.current_stage,
            "round_number": self.state.round_number,
            "total_loss_cents": self.state.total_loss_cents,
            "total_wins": self.state.total_wins,
            "total_losses": self.state.total_losses,
            "total_profit": self.state.total_profit,
            "sizing": sizing if sizing.get("valid") else None,
            "recent_trades": [asdict(t) for t in self.trade_history[-10:]],
        }

    def run_once(self, orderbooks: dict = None) -> Optional[RecoveryTradeRecord]:
        """
        Run one iteration of the recovery loop.

        Args:
            orderbooks: WebSocket orderbooks dict

        Returns:
            RecoveryTradeRecord if trade executed
        """
        # Check settlements first
        self.check_settlements()

        # Find opportunity
        if orderbooks:
            opportunity = self.find_opportunity_from_orderbook(orderbooks)
        else:
            opportunity = self.find_opportunity_rest()

        if not opportunity:
            return None

        # Execute bet
        return self.execute_recovery_bet(opportunity, orderbooks)
