"""
2-Stage Recovery Martingale System

A "safe" betting system that:
- Enters at 80-87c with 5 minutes or less remaining
- Stage 1 + 2 recovery capped at 87c (90c with 3c slippage)
- Two modes: Aggressive (50% Stage 2) and Conservative (20% Stage 2)
- Uses WebSocket orderbook for fast entries

The System:
- Base bet: Small % of bankroll
- If Base loses -> Stage 1: Recover Base + small profit
- If Stage 1 loses -> Stage 2: Recover Base + Stage 1 + profit
- If Stage 2 loses -> Give up, reset, start fresh
- Any win -> Reset to Base and auto-compound
"""

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class RecoveryState:
    """Persistent state for 2-stage recovery system."""
    current_stage: int = 0  # 0=base, 1=stage1, 2=stage2
    base_loss_cents: int = 0  # Loss from base bet
    stage1_loss_cents: int = 0  # Loss from stage 1
    total_loss_cents: int = 0  # Cumulative loss to recover
    base_contracts: int = 0  # Track base size for reference

    # Round tracking
    round_number: int = 1
    total_rounds: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_profit: float = 0.0

    def reset_to_base(self):
        """Reset to base bet state (after win or stage 2 loss)."""
        self.current_stage = 0
        self.base_loss_cents = 0
        self.stage1_loss_cents = 0
        self.total_loss_cents = 0
        self.base_contracts = 0
        self.round_number += 1

    def advance_to_stage1(self, loss_cents: int):
        """Advance to stage 1 after base bet loss."""
        self.current_stage = 1
        self.base_loss_cents = loss_cents
        self.total_loss_cents = loss_cents

    def advance_to_stage2(self, loss_cents: int):
        """Advance to stage 2 after stage 1 loss."""
        self.current_stage = 2
        self.stage1_loss_cents = loss_cents
        self.total_loss_cents = self.base_loss_cents + loss_cents

    def on_win(self, profit: float):
        """Record a win."""
        self.total_wins += 1
        self.total_rounds += 1
        self.total_profit += profit

    def on_loss(self, loss: float):
        """Record a loss (stage 2 failed)."""
        self.total_losses += 1
        self.total_rounds += 1
        self.total_profit -= loss

    def save(self, path: Path):
        """Save state to JSON file."""
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "RecoveryState":
        """Load state from JSON file."""
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()


class RecoveryCalculator:
    """
    Calculator for 2-stage recovery betting.

    Entry conditions:
    - All bets: 80-87c ask price (90c max with 3c slippage)
    - 5 minutes or less remaining in the market

    Modes:
    - Aggressive: Stage 2 = 50% of bankroll
    - Conservative: Stage 2 = 20% of bankroll

    Fee formula: 0.07 * price * (1 - price)
    Recovery buffer: 5% over loss to ensure profit
    """

    # Entry price limits
    MIN_ENTRY_PRICE = 80  # cents
    MAX_ENTRY_PRICE = 87  # cents (90c with 3c slippage)

    # Slippage allowance
    SLIPPAGE_CENTS = 3

    # Recovery buffer (5% over loss)
    RECOVERY_BUFFER = 1.05

    # Mode configurations
    AGGRESSIVE_STAGE2_PCT = 0.50  # 50% of bankroll
    CONSERVATIVE_STAGE2_PCT = 0.20  # 20% of bankroll

    @staticmethod
    def calc_fee(price_cents: int) -> float:
        """
        Calculate Kalshi fee per contract in dollars.
        Formula: ceil(0.07 * price * (1 - price)) rounded to nearest cent
        """
        price = price_cents / 100
        fee = 0.07 * price * (1 - price)
        return max(0.01, round(fee + 0.005, 2))

    @staticmethod
    def calc_net_profit(entry_price_cents: int) -> float:
        """
        Calculate net profit per contract in dollars.
        net_profit = (1.00 - fill_price) - fee
        """
        price = entry_price_cents / 100
        gross_profit = 1.0 - price
        fee = RecoveryCalculator.calc_fee(entry_price_cents)
        return gross_profit - fee

    def calculate_bet_sizing(self, bankroll: float, mode: str = "conservative") -> dict:
        """
        Calculate bet sizing for all stages given bankroll and mode.

        Args:
            bankroll: Total bankroll in dollars
            mode: "aggressive" (50% S2) or "conservative" (20% S2)

        Returns:
            dict with base_contracts, stage1_contracts, stage2_contracts, costs, valid
        """
        if bankroll <= 0:
            return {"valid": False, "error": "Invalid bankroll"}

        # Determine Stage 2 cap
        if mode == "aggressive":
            stage2_max_pct = self.AGGRESSIVE_STAGE2_PCT
        else:
            stage2_max_pct = self.CONSERVATIVE_STAGE2_PCT

        stage2_max_cost = bankroll * stage2_max_pct

        # Use worst-case fill price (87c + 3c slippage = 90c)
        worst_fill_price = self.MAX_ENTRY_PRICE + self.SLIPPAGE_CENTS
        cost_per_contract = worst_fill_price / 100
        net_profit = self.calc_net_profit(worst_fill_price)
        fee_per_contract = self.calc_fee(worst_fill_price)
        loss_per_contract = cost_per_contract + fee_per_contract

        # Max Stage 2 contracts given cost cap
        max_s2_contracts = int(stage2_max_cost / cost_per_contract)
        if max_s2_contracts < 1:
            min_bankroll = cost_per_contract / stage2_max_pct
            return {
                "valid": False,
                "error": f"Bankroll too small. Need at least ${min_bankroll:.2f} for {mode} mode.",
            }

        # Max loss Stage 2 can recover
        max_recoverable_loss = (max_s2_contracts * net_profit) / self.RECOVERY_BUFFER

        # Find max base contracts where base + stage1 losses can be recovered by stage2
        best_valid = 0
        best_result = None

        for base_contracts in range(1, 100):
            base_cost = base_contracts * cost_per_contract
            base_loss = base_contracts * loss_per_contract

            # Stage 1 to recover base
            s1_contracts = math.ceil((base_loss * self.RECOVERY_BUFFER) / net_profit)
            s1_cost = s1_contracts * cost_per_contract
            s1_loss = s1_contracts * loss_per_contract

            # Total loss before Stage 2
            total_loss_before_s2 = base_loss + s1_loss

            # Can Stage 2 recover this?
            s2_contracts_needed = math.ceil((total_loss_before_s2 * self.RECOVERY_BUFFER) / net_profit)
            s2_cost_needed = s2_contracts_needed * cost_per_contract

            if s2_cost_needed <= stage2_max_cost:
                total_risk = base_cost + s1_cost + s2_cost_needed
                best_valid = base_contracts
                best_result = {
                    "base_contracts": base_contracts,
                    "stage1_contracts": s1_contracts,
                    "stage2_contracts": s2_contracts_needed,
                    "base_cost": round(base_cost, 2),
                    "stage1_cost": round(s1_cost, 2),
                    "stage2_cost": round(s2_cost_needed, 2),
                    "total_risk": round(total_risk, 2),
                    "stage2_max_allowed": round(stage2_max_cost, 2),
                    "stage2_pct_of_bankroll": round(s2_cost_needed / bankroll * 100, 1),
                    "net_profit_per_contract": round(net_profit, 2),
                }
            else:
                break

        if best_valid == 0:
            return {
                "valid": False,
                "error": f"Cannot calculate bet sizing for ${bankroll:.2f} in {mode} mode.",
            }

        return {
            **best_result,
            "valid": True,
            "bankroll": bankroll,
            "mode": mode,
        }

    def calculate_recovery_bet(
        self,
        loss_to_recover: float,
        entry_price_cents: int,
    ) -> Optional[dict]:
        """
        Calculate recovery bet size to recover a loss plus buffer.

        Args:
            loss_to_recover: Total loss to recover in dollars
            entry_price_cents: Current ask price in cents

        Returns:
            dict with contracts, cost, expected_profit, or None if invalid
        """
        if loss_to_recover <= 0:
            return None

        if entry_price_cents > self.MAX_ENTRY_PRICE:
            return None

        # Add slippage assumption
        fill_price = entry_price_cents + self.SLIPPAGE_CENTS
        net_profit = self.calc_net_profit(fill_price)

        if net_profit <= 0:
            return None

        # Calculate contracts needed
        target_recovery = loss_to_recover * self.RECOVERY_BUFFER
        contracts = math.ceil(target_recovery / net_profit)

        if contracts < 1:
            contracts = 1

        cost = contracts * (fill_price / 100)
        expected_profit = contracts * net_profit

        return {
            "contracts": contracts,
            "cost": round(cost, 2),
            "expected_profit": round(expected_profit, 2),
            "net_recovery": round(expected_profit - loss_to_recover, 2),
            "entry_price": entry_price_cents,
            "assumed_fill": fill_price,
        }

    def is_valid_entry(self, price_cents: int, minutes_remaining: float) -> bool:
        """Check if entry conditions are met."""
        price_ok = self.MIN_ENTRY_PRICE <= price_cents <= self.MAX_ENTRY_PRICE
        time_ok = 0.5 <= minutes_remaining <= 5.0
        return price_ok and time_ok

    def get_sizing_summary(self, bankroll: float) -> dict:
        """Get sizing for both modes for comparison."""
        conservative = self.calculate_bet_sizing(bankroll, "conservative")
        aggressive = self.calculate_bet_sizing(bankroll, "aggressive")

        return {
            "bankroll": bankroll,
            "conservative": conservative,
            "aggressive": aggressive,
        }


def print_sizing_analysis(bankroll: float):
    """Print detailed sizing analysis for a bankroll."""
    calc = RecoveryCalculator()

    print(f"\n{'='*60}")
    print(f"2-STAGE RECOVERY SIZING ANALYSIS")
    print(f"{'='*60}")
    print(f"Bankroll: ${bankroll:.2f}")
    print(f"{'='*60}")

    for mode in ["conservative", "aggressive"]:
        result = calc.calculate_bet_sizing(bankroll, mode)
        pct = "20%" if mode == "conservative" else "50%"

        print(f"\n{mode.upper()} MODE (Stage 2 = {pct} of bankroll):")

        if not result.get("valid"):
            print(f"  INVALID: {result.get('error', 'Unknown error')}")
            continue

        print(f"  Base bet:   {result['base_contracts']} contracts @ ${result['base_cost']:.2f}")
        print(f"  Stage 1:    {result['stage1_contracts']} contracts @ ${result['stage1_cost']:.2f}")
        print(f"  Stage 2:    {result['stage2_contracts']} contracts @ ${result['stage2_cost']:.2f}")
        print(f"  Total risk: ${result['total_risk']:.2f}")
        print(f"  Stage 2 cap: ${result['stage2_max_allowed']:.2f}")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    for bankroll in [50, 100, 200, 320, 500]:
        print_sizing_analysis(bankroll)
