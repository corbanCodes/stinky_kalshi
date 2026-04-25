"""
💩💩💩 STINKY KALSHI TRADER 💩💩💩

The Stink Theory:
- Bet on NO side (longshots) at 5-20 cents
- Quadratic progression: $1, $2, $3, $4... (not exponential!)
- When we win, payout = contracts * $1 = (bet * 100 / entry_price)
- At 10¢ entry: $1 bet = 10 contracts = $10 payout = $9 profit
- At 15¢ entry: $1 bet = 6.67 contracts = $6.67 payout = $5.67 profit

Break-even math:
- After N losses, cumulative cost = N(N+1)/2 * base_bet
- Win on bet N+1: payout = (N+1) * base * 100 / entry_price
- Profitable if payout > cumulative_cost + current_bet
- Max losses to profit = (200/entry_price) - 2

With 10¢ entry: Can survive 18 losses and still profit
With 15¢ entry: Can survive 11 losses and still profit
With 20¢ entry: Can survive 8 losses and still profit
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

from .config import AppConfig, load_config, StinkConfig
from .kalshi_client import KalshiClient, MarketData


@dataclass
class StinkState:
    """
    💩 Persistent state for quadratic betting 💩

    Tracks current position in the betting sequence.
    """
    bet_number: int = 1  # Current bet in sequence (1, 2, 3, 4...)
    round_number: int = 1  # Which round we're on
    cumulative_loss_this_round: float = 0.0  # Total lost this round so far

    # Session stats
    total_rounds: int = 0
    total_wins: int = 0
    total_bets: int = 0
    total_profit: float = 0.0
    max_streak: int = 0  # Longest loss streak
    current_streak: int = 0  # Current loss streak

    # History
    last_bet_time: str = ""
    last_ticker: str = ""

    def save(self, path: Path):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "StinkState":
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def on_win(self, profit: float, base_bet: float):
        """💩 Called when we WIN a bet 💩"""
        bet_amount = base_bet * self.bet_number
        payout = profit + bet_amount  # profit = payout - bet

        # Calculate round P/L
        round_cost = self.cumulative_loss_this_round + bet_amount
        round_pl = payout - round_cost

        # Update stats
        self.total_wins += 1
        self.total_rounds += 1
        self.total_bets += 1
        self.total_profit += profit  # Just the profit from this trade

        # Track max streak
        if self.current_streak > self.max_streak:
            self.max_streak = self.current_streak

        print(f"💩💩💩 WIN! Round {self.round_number} complete! 💩💩💩")
        print(f"    Bets in round: {self.bet_number}")
        print(f"    Round cost: ${round_cost:.2f}")
        print(f"    Payout: ${payout:.2f}")
        print(f"    Round P/L: ${round_pl:+.2f}")
        print(f"    Total profit: ${self.total_profit:.2f}")

        # Reset for next round
        self.round_number += 1
        self.bet_number = 1
        self.cumulative_loss_this_round = 0.0
        self.current_streak = 0

    def on_loss(self, loss_amount: float):
        """💩 Called when we LOSE a bet 💩"""
        self.total_bets += 1
        self.total_profit -= loss_amount
        self.cumulative_loss_this_round += loss_amount
        self.current_streak += 1

        print(f"💩 LOSS! Bet #{self.bet_number} in round {self.round_number}")
        print(f"    Lost: ${loss_amount:.2f}")
        print(f"    Cumulative this round: ${self.cumulative_loss_this_round:.2f}")
        print(f"    Current streak: {self.current_streak}")

        # Move to next bet in sequence
        self.bet_number += 1

    def get_next_bet_amount(self, base_bet: float) -> float:
        """💩 Get the next bet amount (quadratic) 💩"""
        return base_bet * self.bet_number

    def get_cumulative_if_lose(self, base_bet: float) -> float:
        """What will cumulative loss be if we lose this bet?"""
        return self.cumulative_loss_this_round + (base_bet * self.bet_number)


@dataclass
class TradeRecord:
    """Record of a single trade."""
    timestamp: str
    ticker: str
    side: str
    contracts: int
    entry_price: int
    bet_number: int
    round_number: int
    bet_amount: float
    result: str = "pending"  # "win", "loss", "pending"
    profit: float = 0.0
    payout: float = 0.0


class StinkTrader:
    """
    💩💩💩 THE STINKY TRADER 💩💩💩

    Implements quadratic progression betting on NO side longshots.
    """

    def __init__(self, config: AppConfig = None):
        self.config = config or load_config()
        self.stink = self.config.stink

        # Initialize client
        self.client = KalshiClient(self.config.kalshi)

        # Load state
        self.state_path = self.config.data_dir / "stink_state.json"
        self.state = StinkState.load(self.state_path)

        # Trade history
        self.history_path = self.config.data_dir / "stink_history.json"
        self.trade_history: list[TradeRecord] = []
        self._load_history()

        # Track which tickers we've bet on (avoid double-betting same window)
        self._bet_tickers: set[str] = set()

        # Balance
        self.balance = 0.0
        self.refresh_balance()

        print("💩💩💩 STINKY TRADER INITIALIZED 💩💩💩")
        print(f"    Base bet: ${self.stink.base_bet_dollars:.2f}")
        print(f"    Entry range: {self.stink.min_entry_price}-{self.stink.max_entry_price}¢")
        print(f"    Sweet spot: {self.stink.sweet_spot_min}-{self.stink.sweet_spot_max}¢")
        print(f"    Current bet #: {self.state.bet_number}")
        print(f"    Round #: {self.state.round_number}")
        print(f"    Total profit: ${self.state.total_profit:.2f}")

    def _load_history(self):
        """Load trade history from disk."""
        if self.history_path.exists():
            with open(self.history_path) as f:
                data = json.load(f)
                self.trade_history = [TradeRecord(**r) for r in data]

    def _save_history(self):
        """Save trade history to disk."""
        with open(self.history_path, "w") as f:
            json.dump([asdict(r) for r in self.trade_history], f, indent=2)

    def log(self, message: str, level: str = "INFO"):
        """Log with timestamp and poop."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        poop = "💩" if level != "ERROR" else "🚨"
        print(f"{timestamp} {poop} [{level}] {message}")

    def refresh_balance(self):
        """Refresh balance from Kalshi."""
        try:
            self.balance = self.client.get_balance_dollars()
            self.log(f"Balance: ${self.balance:.2f}")
        except Exception as e:
            self.log(f"Could not refresh balance: {e}", "ERROR")

    def get_next_bet_info(self) -> dict:
        """Get info about the next bet."""
        bet_amount = self.state.get_next_bet_amount(self.stink.base_bet_dollars)
        cumulative_if_lose = self.state.get_cumulative_if_lose(self.stink.base_bet_dollars)

        # Calculate potential payout at sweet spot entry
        entry = self.stink.sweet_spot_max  # Use conservative estimate
        contracts = int((bet_amount * 100) / entry)
        potential_payout = contracts  # Each contract pays $1

        return {
            "bet_number": self.state.bet_number,
            "round_number": self.state.round_number,
            "bet_amount": bet_amount,
            "cumulative_this_round": self.state.cumulative_loss_this_round,
            "cumulative_if_lose": cumulative_if_lose,
            "potential_payout": potential_payout,
            "current_streak": self.state.current_streak,
            "max_streak": self.state.max_streak,
        }

    def can_afford_bet(self) -> bool:
        """Check if we can afford the next bet."""
        bet_amount = self.state.get_next_bet_amount(self.stink.base_bet_dollars)
        return self.balance >= bet_amount

    def find_opportunities(self) -> list[MarketData]:
        """
        💩 Find stinky opportunities (NO side at target price) 💩
        """
        opportunities = []

        try:
            markets = self.client.get_btc_15min_markets()

            for market in markets:
                # Skip already traded
                if market.ticker in self._bet_tickers:
                    continue

                # Check if NO ask is in our range
                no_ask = market.no_ask
                if no_ask == 0:
                    continue

                if self.stink.min_entry_price <= no_ask <= self.stink.max_entry_price:
                    opportunities.append(market)
                    self.log(f"💩 Opportunity: {market.ticker} NO @ {no_ask}¢")

        except Exception as e:
            self.log(f"Error finding opportunities: {e}", "ERROR")

        return opportunities

    def execute_stink_bet(self, market: MarketData, actual_entry: int = None) -> Optional[TradeRecord]:
        """
        💩 Execute a stinky bet! 💩

        Buy NO contracts at the current ask price.
        actual_entry: The actual market price (before slippage), used for cost calculation
        """
        print(f"💩 ========== EXECUTE_STINK_BET CALLED ==========")
        print(f"💩 INPUT market.ticker: {market.ticker}")
        print(f"💩 INPUT market.no_ask (limit price): {market.no_ask}¢")
        print(f"💩 INPUT actual_entry (market price): {actual_entry}¢")
        print(f"💩 CURRENT _bet_tickers: {self._bet_tickers}")

        if market.ticker in self._bet_tickers:
            print(f"💩 ABORT: Already bet on {market.ticker}")
            self.log(f"Already bet on {market.ticker}", "WARN")
            return None

        bet_amount = self.state.get_next_bet_amount(self.stink.base_bet_dollars)
        limit_price = market.no_ask  # May include slippage buffer
        # Use actual_entry for cost calculation if provided, otherwise use limit_price
        cost_price = actual_entry if actual_entry else limit_price

        print(f"💩 CALC: base_bet=${self.stink.base_bet_dollars}, bet_number={self.state.bet_number}")
        print(f"💩 CALC: bet_amount=${bet_amount:.2f}")
        print(f"💩 CALC: limit_price={limit_price}¢ (for order)")
        print(f"💩 CALC: cost_price={cost_price}¢ (for calculation)")

        if not limit_price or limit_price <= 0:
            print(f"💩 ABORT: Invalid limit_price: {limit_price}")
            return None

        # Calculate contracts based on expected fill price
        contracts = int((bet_amount * 100) / cost_price)
        if contracts < 1:
            contracts = 1

        # Estimated cost (actual fill may be at cost_price or better)
        actual_cost = (contracts * cost_price) / 100

        print(f"💩 ORDER DETAILS:")
        print(f"💩   Ticker: {market.ticker}")
        print(f"💩   Side: NO (buy)")
        print(f"💩   Limit Price: {limit_price}¢ (order will be placed at this)")
        print(f"💩   Expected Fill: {cost_price}¢")
        print(f"💩   Contracts: {contracts}")
        print(f"💩   Est. Cost: ${actual_cost:.2f}")
        print(f"💩   Bet #{self.state.bet_number} in round {self.state.round_number}")
        print(f"💩   use_market_orders: {self.stink.use_market_orders}")

        self.log(f"💩 EXECUTING STINK BET 💩")
        self.log(f"    Ticker: {market.ticker}")
        self.log(f"    Side: NO")
        self.log(f"    Limit: {limit_price}¢ (expected fill: {cost_price}¢)")
        self.log(f"    Contracts: {contracts}")
        self.log(f"    Est. Cost: ${actual_cost:.2f}")
        self.log(f"    Bet #{self.state.bet_number} in round {self.state.round_number}")

        try:
            print(f"💩 CALLING client.buy_no(ticker={market.ticker}, count={contracts}, price={limit_price})...")
            # Place the order at limit_price (includes slippage buffer)
            order = self.client.buy_no(
                ticker=market.ticker,
                count=contracts,
                price=limit_price if not self.stink.use_market_orders else None
            )

            print(f"💩 ORDER RESPONSE:")
            print(f"💩   order_id: {order.order_id}")
            print(f"💩   status: {order.status}")
            print(f"💩   filled_count: {order.filled_count}")

            self.log(f"    Order placed: {order.order_id}")
            self.log(f"    Status: {order.status}")
            self.log(f"    Filled: {order.filled_count}")

            # Record the trade (use cost_price as entry, that's what we expect to pay)
            record = TradeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                ticker=market.ticker,
                side="no",
                contracts=contracts,
                entry_price=cost_price,
                bet_number=self.state.bet_number,
                round_number=self.state.round_number,
                bet_amount=actual_cost,
                result="pending",
            )

            self.trade_history.append(record)
            self._save_history()

            # Mark ticker as bet
            self._bet_tickers.add(market.ticker)
            print(f"💩 ADDED {market.ticker} to _bet_tickers: {self._bet_tickers}")

            # Update state
            self.state.last_bet_time = record.timestamp
            self.state.last_ticker = market.ticker
            self.state.save(self.state_path)

            # Refresh balance
            self.refresh_balance()
            print(f"💩 NEW BALANCE: ${self.balance:.2f}")

            print(f"💩 ========== BET SUCCESSFUL ==========")
            return record

        except Exception as e:
            import traceback
            print(f"💩 ========== ORDER FAILED ==========")
            print(f"💩 ERROR: {e}")
            traceback.print_exc()
            self.log(f"Order failed: {e}", "ERROR")
            return None

    def check_settlements(self):
        """
        💩 Check for settled positions and update state 💩
        """
        try:
            # Get recent fills/settlements
            fills = self.client.get_fills(limit=50)

            for fill in fills:
                ticker = fill.get("ticker", "")

                # Find matching pending trade
                for trade in self.trade_history:
                    if trade.ticker == ticker and trade.result == "pending":
                        # Check if this is a settlement (is_taker with no order = settlement)
                        if fill.get("is_taker") is False and fill.get("action") == "sell":
                            # This is likely a settlement
                            # For NO side: if we got paid, we won
                            credits = fill.get("credits_cents", 0) / 100

                            if credits > 0:
                                # WIN!
                                trade.result = "win"
                                trade.payout = credits
                                trade.profit = credits - trade.bet_amount
                                self.state.on_win(trade.profit, self.stink.base_bet_dollars)
                            else:
                                # LOSS
                                trade.result = "loss"
                                trade.profit = -trade.bet_amount
                                self.state.on_loss(trade.bet_amount)

                            self._save_history()
                            self.state.save(self.state_path)

                            # Remove from bet tickers so we can bet on next window
                            self._bet_tickers.discard(ticker)

        except Exception as e:
            self.log(f"Error checking settlements: {e}", "ERROR")

    def get_status(self) -> dict:
        """Get current trader status."""
        return {
            "balance": self.balance,
            "state": asdict(self.state),
            "next_bet": self.get_next_bet_info(),
            "config": {
                "base_bet": self.stink.base_bet_dollars,
                "entry_range": f"{self.stink.min_entry_price}-{self.stink.max_entry_price}¢",
                "sweet_spot": f"{self.stink.sweet_spot_min}-{self.stink.sweet_spot_max}¢",
            },
            "recent_trades": [asdict(t) for t in self.trade_history[-10:]],
        }

    def run_once(self) -> Optional[TradeRecord]:
        """
        💩 Run one iteration of the stink loop 💩

        1. Check for settlements (update wins/losses)
        2. Find opportunities
        3. Execute bet if opportunity found
        """
        # Check settlements first
        self.check_settlements()

        # Check if we can afford next bet
        if not self.can_afford_bet():
            self.log(f"Cannot afford next bet (${self.state.get_next_bet_amount(self.stink.base_bet_dollars):.2f})", "WARN")
            return None

        # Find opportunities
        opportunities = self.find_opportunities()

        if not opportunities:
            return None

        # Sort by price (prefer cheaper = more profitable)
        opportunities.sort(key=lambda m: m.no_ask)

        # Execute on best opportunity
        best = opportunities[0]
        return self.execute_stink_bet(best)
