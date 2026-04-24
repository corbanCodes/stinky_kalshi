# 💩 Stinky Kalshi Official 💩

Quadratic progression betting on BTC 15-minute longshots.

## The Stink Theory 💩

Instead of betting on likely outcomes (80-90¢), we bet on **unlikely outcomes** (10-15¢).

- **Entry**: Buy NO contracts at 10-15 cents
- **Payout**: If NO wins, each contract pays $1 (5-10x return)
- **Progression**: Quadratic ($1, $2, $3, $4...) not exponential

### Why It Works

| Entry Price | Payout Multiple | Max Losses to Profit |
|-------------|-----------------|---------------------|
| 10¢ | 10x | 18 losses |
| 15¢ | 6.67x | 11 losses |
| 20¢ | 5x | 8 losses |

With 3 months of BTC data (4,272 markets):
- Max loss streak: **9**
- Win rate: ~49% (basically a coin flip)
- **Result: All entry prices 5-25¢ are profitable!**

### Quadratic Math

After N losses, cumulative cost = `1+2+3+...+N = N(N+1)/2`

| Bet # | Bet Amount | Cumulative |
|-------|-----------|------------|
| 1 | $1 | $1 |
| 2 | $2 | $3 |
| 3 | $3 | $6 |
| 4 | $4 | $10 |
| 5 | $5 | $15 |
| ... | ... | ... |
| 9 | $9 | $45 |

At 15¢ entry, bet 9 wins: `9 * $1 * 100/15 = $60 payout`
Round P/L: `$60 - $45 = +$15` ✅

## Setup

1. Clone repo
2. Create `.env` from `.env.example`
3. Add your Kalshi API credentials
4. Install dependencies: `pip install -r requirements.txt`
5. Run: `python main.py`

## Usage

```bash
# Show current status
python main.py --status

# Scan for opportunities
python main.py --scan

# Dry run (no actual bets)
python main.py --dry-run

# Live trading with $1 base
python main.py --base-bet 1.00

# Live trading with $0.50 base (safer)
python main.py --base-bet 0.50

# Live trading with $5 base (more profit, more risk)
python main.py --base-bet 5.00
```

## Capital Requirements

| Base Bet | Max Risk (9 streak) | Recommended Bankroll |
|----------|--------------------|--------------------|
| $0.25 | $11.25 | $50+ |
| $0.50 | $22.50 | $100+ |
| $1.00 | $45.00 | $200+ |
| $2.00 | $90.00 | $400+ |
| $5.00 | $225.00 | $1,000+ |

With $330 bankroll: **$1 base is safe**, $2 base is comfortable.

## Deploy to Railway

1. Push to GitHub
2. Connect to Railway
3. Add environment variables in Railway dashboard
4. Deploy!

## Files

```
💩 Stinky Kalshi Official/
├── main.py              # Entry point
├── src/
│   ├── auth.py          # Kalshi authentication
│   ├── config.py        # Configuration
│   ├── kalshi_client.py # REST API client
│   ├── websocket_client.py # WebSocket for orderbook
│   └── stink_trader.py  # Core trading logic
├── data/                # State files (auto-created)
├── logs/                # Logs (auto-created)
├── requirements.txt
├── railway.json
└── .env                 # Your credentials (not in git)
```

## 💩 FAQ

**Q: Does base bet amount matter?**
A: Same ratio - everything scales linearly. $1 base = $100K profit, $0.50 base = $50K profit. Pick based on your capital.

**Q: Why only 9 max streak instead of 26?**
A: Your actual trades had a 5-minute filter which clustered bets during volatile periods. Betting on ALL markets = more random = shorter streaks.

**Q: What if I hit a long streak?**
A: At 15¢ entry, you can survive 11 losses. The worst in 3 months of data was 9. But always be prepared!

---

*Made with 💩 by Stink Theory Labs*
