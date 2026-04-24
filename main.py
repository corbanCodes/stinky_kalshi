#!/usr/bin/env python3
"""
💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩
💩                                                              💩
💩             STINKY KALSHI OFFICIAL                          💩
💩                                                              💩
💩     Quadratic Progression Betting on Longshots              💩
💩                                                              💩
💩     Bet NO side at 10-15 cents                              💩
💩     Progression: $1, $2, $3, $4... (quadratic)              💩
💩                                                              💩
💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩

Usage:
    python main.py                    # Run trader with defaults
    python main.py --base-bet 0.50    # Set base bet to $0.50
    python main.py --base-bet 2.00    # Set base bet to $2.00
    python main.py --dry-run          # Show what would happen without betting
    python main.py --status           # Show current status and exit

Environment Variables:
    KALSHI_API_KEY_ID       - Your Kalshi API key ID
    KALSHI_PRIVATE_KEY_BASE64 - Your private key (base64 encoded)
    BASE_BET_DOLLARS        - Base bet amount (default: 1.00)
    MIN_ENTRY_PRICE         - Min entry price in cents (default: 5)
    MAX_ENTRY_PRICE         - Max entry price in cents (default: 20)
    SWEET_SPOT_MIN          - Sweet spot min (default: 10)
    SWEET_SPOT_MAX          - Sweet spot max (default: 15)
"""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config, StinkConfig
from src.stink_trader import StinkTrader
from src.kalshi_client import KalshiClient


BANNER = """
💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩
💩                                                                  💩
💩   ███████╗████████╗██╗███╗   ██╗██╗  ██╗██╗   ██╗                💩
💩   ██╔════╝╚══██╔══╝██║████╗  ██║██║ ██╔╝╚██╗ ██╔╝                💩
💩   ███████╗   ██║   ██║██╔██╗ ██║█████╔╝  ╚████╔╝                 💩
💩   ╚════██║   ██║   ██║██║╚██╗██║██╔═██╗   ╚██╔╝                  💩
💩   ███████║   ██║   ██║██║ ╚████║██║  ██╗   ██║                   💩
💩   ╚══════╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝   ╚═╝                   💩
💩                                                                  💩
💩            K A L S H I   O F F I C I A L                        💩
💩                                                                  💩
💩     Quadratic Longshot Betting  •  Bet NO @ 10-15¢              💩
💩                                                                  💩
💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩💩
"""


def print_status(trader: StinkTrader):
    """Print current status."""
    status = trader.get_status()
    state = status["state"]
    next_bet = status["next_bet"]

    print("\n" + "="*60)
    print("💩 STINKY STATUS 💩")
    print("="*60)

    print(f"\n📊 BALANCE: ${status['balance']:.2f}")

    print(f"\n🎰 CURRENT POSITION:")
    print(f"   Round #{state['round_number']}")
    print(f"   Bet #{state['bet_number']} in round")
    print(f"   Cumulative loss this round: ${state['cumulative_loss_this_round']:.2f}")

    print(f"\n📈 NEXT BET:")
    print(f"   Amount: ${next_bet['bet_amount']:.2f}")
    print(f"   If we lose, cumulative: ${next_bet['cumulative_if_lose']:.2f}")
    print(f"   Potential payout (@ {trader.stink.sweet_spot_max}¢): ${next_bet['potential_payout']:.2f}")

    print(f"\n📉 STREAK INFO:")
    print(f"   Current streak: {state['current_streak']}")
    print(f"   Max streak ever: {state['max_streak']}")

    print(f"\n💰 LIFETIME STATS:")
    print(f"   Total rounds: {state['total_rounds']}")
    print(f"   Total wins: {state['total_wins']}")
    print(f"   Total bets: {state['total_bets']}")
    print(f"   Total profit: ${state['total_profit']:.2f}")

    print(f"\n⚙️ CONFIG:")
    print(f"   Base bet: ${trader.stink.base_bet_dollars:.2f}")
    print(f"   Entry range: {trader.stink.min_entry_price}-{trader.stink.max_entry_price}¢")
    print(f"   Sweet spot: {trader.stink.sweet_spot_min}-{trader.stink.sweet_spot_max}¢")

    print("\n" + "="*60)


def print_opportunities(trader: StinkTrader):
    """Print current opportunities."""
    print("\n💩 Scanning for stinky opportunities...")

    opps = trader.find_opportunities()

    if not opps:
        print("   No opportunities found (NO ask not in range)")
        return

    print(f"\n   Found {len(opps)} opportunities:")
    for m in sorted(opps, key=lambda x: x.no_ask):
        print(f"   • {m.ticker}: NO @ {m.no_ask}¢ (strike: {m.floor_strike})")


def run_trading_loop(trader: StinkTrader, dry_run: bool = False, interval: int = 30):
    """Main trading loop."""
    print("\n💩 Starting trading loop...")
    print(f"   Interval: {interval} seconds")
    print(f"   Dry run: {dry_run}")
    print("   Press Ctrl+C to stop\n")

    while True:
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{timestamp}] 💩 Scanning...")

            # Run one iteration
            if dry_run:
                # Just show opportunities
                opps = trader.find_opportunities()
                if opps:
                    print(f"   Would bet on: {opps[0].ticker} @ {opps[0].no_ask}¢")
                    print(f"   Bet amount: ${trader.state.get_next_bet_amount(trader.stink.base_bet_dollars):.2f}")
                else:
                    print("   No opportunities")
            else:
                result = trader.run_once()
                if result:
                    print(f"   ✅ Bet placed: {result.ticker}")

            # Check settlements
            trader.check_settlements()

            # Show quick status
            print(f"   Balance: ${trader.balance:.2f} | Round {trader.state.round_number} | Bet #{trader.state.bet_number}")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n\n💩 Stopping trader...")
            break
        except Exception as e:
            print(f"   ❌ Error: {e}")
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(
        description="💩 Stinky Kalshi - Quadratic Longshot Betting 💩"
    )
    parser.add_argument(
        "--base-bet",
        type=float,
        default=None,
        help="Base bet amount in dollars (default: $1.00)"
    )
    parser.add_argument(
        "--min-entry",
        type=int,
        default=None,
        help="Minimum entry price in cents (default: 5)"
    )
    parser.add_argument(
        "--max-entry",
        type=int,
        default=None,
        help="Maximum entry price in cents (default: 20)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without actually betting"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status and exit"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for opportunities once and exit"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Scan interval in seconds (default: 30)"
    )

    args = parser.parse_args()

    # Print banner
    print(BANNER)

    # Load config
    config = load_config()

    # Override with command line args
    if args.base_bet is not None:
        config.stink.base_bet_dollars = args.base_bet
        os.environ["BASE_BET_DOLLARS"] = str(args.base_bet)

    if args.min_entry is not None:
        config.stink.min_entry_price = args.min_entry

    if args.max_entry is not None:
        config.stink.max_entry_price = args.max_entry

    # Validate config
    if not config.kalshi.api_key_id:
        print("❌ ERROR: KALSHI_API_KEY_ID not set")
        print("   Set it in .env or as environment variable")
        sys.exit(1)

    if not config.kalshi.private_key_path and not config.kalshi.private_key_base64:
        print("❌ ERROR: KALSHI_PRIVATE_KEY_BASE64 or KALSHI_PRIVATE_KEY_PATH not set")
        print("   Set it in .env or as environment variable")
        sys.exit(1)

    # Initialize trader
    try:
        trader = StinkTrader(config)
    except Exception as e:
        print(f"❌ Failed to initialize trader: {e}")
        sys.exit(1)

    # Handle commands
    if args.status:
        print_status(trader)
        sys.exit(0)

    if args.scan:
        print_opportunities(trader)
        sys.exit(0)

    # Print initial status
    print_status(trader)

    # Auto-start on Railway (no stdin)
    if not args.dry_run:
        print("\n💩 LIVE TRADING MODE 💩")
        print(f"   Base bet: ${config.stink.base_bet_dollars:.2f}")
        print(f"   Next bet: ${trader.state.get_next_bet_amount(config.stink.base_bet_dollars):.2f}")
        print("   Starting in 3 seconds...")
        time.sleep(3)

    # Run trading loop
    run_trading_loop(trader, dry_run=args.dry_run, interval=args.interval)


if __name__ == "__main__":
    main()
