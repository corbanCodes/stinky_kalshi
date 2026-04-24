"""
💩 Configuration for Stinky Kalshi - Quadratic Longshot Betting 💩
"""

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@dataclass
class StinkConfig:
    """
    💩 STINK THEORY CONFIGURATION 💩

    Strategy: Bet on NO side (longshots) at 5-20 cents with quadratic progression.

    Quadratic betting: $base, $base*2, $base*3, $base*4...
    NOT exponential: $1, $2, $3, $4... (cumulative: $1, $3, $6, $10...)

    Break-even math: At entry P cents, profitable if ≤ (200/P - 2) losses before win
    - At 10¢: Can survive 18 losses
    - At 15¢: Can survive 11 losses
    - At 20¢: Can survive 8 losses
    """

    # 💩 Entry criteria - we want CHEAP contracts (longshots)
    min_entry_price: int = 5   # cents - minimum (too cheap = no liquidity)
    max_entry_price: int = 20  # cents - maximum (above this breaks math)
    sweet_spot_min: int = 10   # cents - ideal range start
    sweet_spot_max: int = 15   # cents - ideal range end

    # 💩 Quadratic betting
    base_bet_dollars: float = 1.0  # Starting bet amount
    # Bet progression: base * 1, base * 2, base * 3, base * 4...

    # 💩 Timing
    bet_immediately: bool = True  # True = bet as soon as we see good price
    # If False, could wait for specific minute mark

    # 💩 Risk management
    max_loss_streak_abort: int = 30  # Stop trading if we hit this streak (shouldn't happen)

    # 💩 Order execution
    use_market_orders: bool = True  # Market orders fill immediately


@dataclass
class KalshiConfig:
    """Kalshi API configuration."""
    api_key_id: str = ""
    private_key_path: str = ""
    private_key_base64: str = ""

    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"

    environment: str = "production"

    @property
    def api_url(self) -> str:
        return self.base_url


@dataclass
class AppConfig:
    """Main application configuration."""
    stink: StinkConfig
    kalshi: KalshiConfig

    base_dir: Path = Path(__file__).parent.parent
    logs_dir: Path = None
    data_dir: Path = None

    def __post_init__(self):
        self.logs_dir = self.base_dir / "logs"
        self.data_dir = self.base_dir / "data"
        self.logs_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)


def load_config() -> AppConfig:
    """Load configuration from environment variables."""

    stink = StinkConfig(
        min_entry_price=int(os.getenv("MIN_ENTRY_PRICE", "5")),
        max_entry_price=int(os.getenv("MAX_ENTRY_PRICE", "20")),
        sweet_spot_min=int(os.getenv("SWEET_SPOT_MIN", "10")),
        sweet_spot_max=int(os.getenv("SWEET_SPOT_MAX", "15")),
        base_bet_dollars=float(os.getenv("BASE_BET_DOLLARS", "1.0")),
        bet_immediately=os.getenv("BET_IMMEDIATELY", "true").lower() == "true",
        use_market_orders=os.getenv("USE_MARKET_ORDERS", "true").lower() == "true",
    )

    kalshi = KalshiConfig(
        api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
        private_key_base64=os.getenv("KALSHI_PRIVATE_KEY_BASE64", ""),
        environment=os.getenv("KALSHI_ENV", "production"),
    )

    return AppConfig(stink=stink, kalshi=kalshi)
