#!/usr/bin/env python3
"""
💩 Stinky Kalshi Web UI 💩
Real-time WebSocket orderbook with Start/Stop trading controls
"""

import os
import sys
import json
import threading
import asyncio
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config
from src.stink_trader import StinkTrader
from src.websocket_client import KalshiWebSocket, Orderbook
from src.kalshi_client import KalshiClient, MarketData

app = Flask(__name__)

# Global state
trader = None
ws_client = None
rest_client = None
config = None

# Thread management
ws_thread = None
trading_thread = None
ws_loop = None

# State flags
is_trading = False
is_ws_running = False
use_slippage = True  # Default ON: add 3¢ buffer to limit orders
SLIPPAGE_CENTS = 3

# Activity log
activity_log = []
MAX_LOG_SIZE = 100


def log_activity(message: str, entry_type: str = ""):
    """Add entry to activity log."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    activity_log.append({
        "message": f"[{timestamp}] {message}",
        "type": entry_type,
        "time": time.time()
    })
    if len(activity_log) > MAX_LOG_SIZE:
        activity_log.pop(0)
    print(f"💩 {message}")


def on_orderbook_update(ob: Orderbook):
    """Callback when orderbook updates - fires on every WebSocket message."""
    pass  # State is updated in ws_client.orderbooks, no logging needed


def on_connection_change(connected: bool, message: str):
    """Callback when WebSocket connection state changes."""
    if connected:
        log_activity(f"WebSocket connected", "win")
    else:
        log_activity(f"WebSocket: {message}", "loss")


# Daily balance logging
last_balance_log_date = None

def check_daily_balance_log():
    """Log balance at midnight each day."""
    global last_balance_log_date, trader

    today = datetime.now().strftime("%Y-%m-%d")
    current_hour = datetime.now().hour

    # Log at midnight (hour 0) if we haven't logged today
    if current_hour == 0 and last_balance_log_date != today and trader:
        try:
            trader.refresh_balance()
            log_activity(f"📊 DAILY BALANCE ({today}): ${trader.balance:.2f}", "win")
            last_balance_log_date = today
        except Exception as e:
            log_activity(f"Failed to log daily balance: {e}", "loss")


async def refresh_market_subscriptions():
    """Fetch current BTC 15-min markets and subscribe to their orderbooks."""
    global rest_client, ws_client

    try:
        markets = rest_client.get_btc_15min_markets()

        # Use time-based filtering - check if market is currently tradeable
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        # Log what we found
        if len(ws_client.subscribed_tickers) == 0 and markets:
            active_count = sum(1 for m in markets if m.status in ("active", "open"))
            log_activity(f"Found {len(markets)} markets ({active_count} active) at {now.strftime('%H:%M:%S UTC')}")

        # Filter for active/open markets (API should return these with status=open query)
        active_markets = [m for m in markets if m.status in ("active", "open") or m.is_currently_active()]

        active_tickers = {m.ticker for m in active_markets}

        # Log summary
        if active_markets:
            log_activity(f"Found {len(active_markets)} active markets")
            for m in active_markets[:3]:
                log_activity(f"  {m.ticker} status={m.status} no_ask={m.no_ask}¢")

        if not active_tickers:
            # No active markets - find when next one opens
            if markets:
                next_open = None
                for m in markets:
                    if m.open_time:
                        try:
                            open_dt = datetime.fromisoformat(m.open_time.replace('Z', '+00:00'))
                            if open_dt > now and (next_open is None or open_dt < next_open):
                                next_open = open_dt
                        except:
                            pass
                if next_open:
                    mins_until = (next_open - now).total_seconds() / 60
                    log_activity(f"No active markets. Next opens in {mins_until:.0f} min ({next_open.strftime('%H:%M UTC')})")
            return

        # Unsubscribe from closed markets
        for ticker in list(ws_client.subscribed_tickers):
            if ticker not in active_tickers:
                await ws_client.unsubscribe(ticker)
                ws_client.orderbooks.pop(ticker, None)

        # Subscribe to new markets
        new_count = 0
        for ticker in active_tickers:
            if ticker not in ws_client.subscribed_tickers:
                log_activity(f"Subscribing to {ticker}...")
                success = await ws_client.subscribe(ticker)
                if success:
                    new_count += 1
                else:
                    log_activity(f"Failed to subscribe to {ticker}", "loss")
                await asyncio.sleep(0.1)

        if new_count > 0:
            log_activity(f"Subscribed to {new_count} new markets (total: {len(ws_client.subscribed_tickers)})", "win")

    except Exception as e:
        import traceback
        log_activity(f"Market refresh error: {e}", "loss")
        log_activity(f"Traceback: {traceback.format_exc()}", "loss")


async def ws_main_loop():
    """Main WebSocket loop with market discovery."""
    global ws_client, rest_client, is_ws_running

    is_ws_running = True
    log_activity("Starting WebSocket...")

    reconnect_delay = 1
    last_refresh = 0
    refresh_interval = 30  # Check for new markets every 30s (15-min markets cycle quickly)

    while is_ws_running:
        try:
            # Connect if needed
            if not ws_client._connected:
                log_activity("Attempting WebSocket connection...")
                if not await ws_client.connect():
                    log_activity(f"Connection failed, retrying in {reconnect_delay}s", "loss")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30)
                    continue
                reconnect_delay = 1
                # Immediately subscribe to markets after connecting
                log_activity("Connected! Subscribing to markets...")
                await refresh_market_subscriptions()
                last_refresh = time.time()

            # Refresh market subscriptions periodically
            if time.time() - last_refresh > refresh_interval:
                await refresh_market_subscriptions()
                last_refresh = time.time()

            # Receive and process messages
            if ws_client.ws:
                try:
                    message = await asyncio.wait_for(ws_client.ws.recv(), timeout=1.0)
                    await ws_client._handle_message(message)
                except asyncio.TimeoutError:
                    pass
                except Exception as recv_err:
                    log_activity(f"Recv error: {recv_err}", "loss")
                    raise

        except Exception as e:
            log_activity(f"WebSocket loop error: {e}", "loss")
            ws_client._connected = False
            await asyncio.sleep(reconnect_delay)

    is_ws_running = False


def run_ws_loop():
    """Run WebSocket in its own thread."""
    global ws_loop
    ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_loop)
    ws_loop.run_until_complete(ws_main_loop())


def trading_loop():
    """Background trading loop."""
    global is_trading, trader, ws_client

    log_activity("Trading started! 💩", "win")
    loop_count = 0

    while is_trading:
        try:
            loop_count += 1

            # Log every 10 loops to show we're alive
            if loop_count % 10 == 1:
                print(f"💩 TRADE LOOP #{loop_count}: is_trading={is_trading}, ws_connected={ws_client.is_connected if ws_client else False}")
                print(f"💩 TRADE CONFIG: range={trader.stink.min_entry_price}-{trader.stink.max_entry_price}¢, base_bet=${trader.stink.base_bet_dollars:.2f}")
                print(f"💩 TRADE STATE: balance=${trader.balance:.2f}, bet_number={trader.state.bet_number}, already_bet_on={list(trader._bet_tickers)}")

            trader.check_settlements()

            # Use WebSocket orderbooks for opportunities
            if not ws_client:
                print(f"💩 TRADE SKIP: ws_client is None")
                time.sleep(1)
                continue

            if not ws_client.is_connected:
                print(f"💩 TRADE SKIP: WebSocket not connected")
                time.sleep(1)
                continue

            # Get all orderbooks
            all_obs = list(ws_client.orderbooks.values())
            if not all_obs:
                if loop_count % 10 == 1:
                    print(f"💩 TRADE SKIP: No orderbooks available")
                time.sleep(1)
                continue

            # Log ALL orderbook prices every 10 loops
            if loop_count % 10 == 1:
                for ob in all_obs:
                    in_range = trader.stink.min_entry_price <= (ob.no_ask or 999) <= trader.stink.max_entry_price
                    print(f"💩 ORDERBOOK: {ob.ticker} NO_ask={ob.no_ask}¢ YES_ask={ob.yes_ask}¢ IN_RANGE={in_range}")

            # Check for opportunities
            opportunities = ws_client.get_stinky_opportunities(
                min_price=trader.stink.min_entry_price,
                max_price=trader.stink.max_entry_price
            )

            if not opportunities:
                if loop_count % 10 == 1:
                    print(f"💩 TRADE: No opportunities in range {trader.stink.min_entry_price}-{trader.stink.max_entry_price}¢")
                time.sleep(1)
                continue

            # Found opportunities!
            print(f"💩 FOUND {len(opportunities)} STINKY OPPORTUNITIES!")
            for opp in opportunities:
                print(f"💩   -> {opp.ticker} NO_ask={opp.no_ask}¢")

            # Check if we can afford
            can_afford = trader.can_afford_bet()
            next_bet = trader.state.get_next_bet_amount(trader.stink.base_bet_dollars)
            print(f"💩 AFFORD CHECK: can_afford={can_afford}, next_bet=${next_bet:.2f}, balance=${trader.balance:.2f}")

            if not can_afford:
                log_activity(f"Cannot afford ${next_bet:.2f}", "loss")
                print(f"💩 STOPPING: Cannot afford next bet")
                is_trading = False
                break

            # Sort and get best
            opportunities.sort(key=lambda ob: ob.no_ask or 999)
            best = opportunities[0]
            print(f"💩 BEST OPPORTUNITY: {best.ticker} @ {best.no_ask}¢")

            # Check if already bet on this ticker
            if best.ticker in trader._bet_tickers:
                print(f"💩 SKIP: Already bet on {best.ticker}")
                time.sleep(1)
                continue

            # EXECUTE THE BET!
            # Apply slippage buffer if enabled
            actual_no_ask = best.no_ask or 0
            limit_price = actual_no_ask + SLIPPAGE_CENTS if use_slippage else actual_no_ask

            print(f"💩 ========== EXECUTING BET ==========")
            print(f"💩 TICKER: {best.ticker}")
            print(f"💩 NO_ASK: {actual_no_ask}¢")
            print(f"💩 SLIPPAGE: {'ON +' + str(SLIPPAGE_CENTS) + '¢' if use_slippage else 'OFF'}")
            print(f"💩 LIMIT_PRICE: {limit_price}¢")
            print(f"💩 BET_AMOUNT: ${next_bet:.2f}")
            print(f"💩 CONTRACTS: {int((next_bet * 100) / actual_no_ask) if actual_no_ask else 0}")

            market = MarketData(
                ticker=best.ticker,
                yes_bid=best.best_yes_bid or 0,
                yes_ask=best.yes_ask or 0,
                no_bid=best.best_no_bid or 0,
                no_ask=limit_price,  # Use limit price with slippage
                volume=0,
                status="open",
                close_time="",
            )

            result = trader.execute_stink_bet(market, actual_entry=actual_no_ask)

            if result:
                print(f"💩 ========== BET PLACED ==========")
                print(f"💩 RESULT: {result}")
                log_activity(f"BET: {result.ticker} @ {result.entry_price}¢ (${result.bet_amount:.2f})", "win")
            else:
                print(f"💩 ========== BET FAILED ==========")
                print(f"💩 execute_stink_bet returned None")

            time.sleep(1)

        except Exception as e:
            import traceback
            print(f"💩 TRADING ERROR: {e}")
            traceback.print_exc()
            log_activity(f"Trading error: {e}", "loss")
            time.sleep(5)

    print(f"💩 TRADING LOOP ENDED: is_trading={is_trading}")
    log_activity("Trading stopped")


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>💩 Stinky Kalshi - Live</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 12px;
            font-size: 14px;
        }
        .container { max-width: 1400px; margin: 0 auto; }

        .header {
            text-align: center;
            padding: 15px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border-radius: 10px;
            margin-bottom: 15px;
        }
        .header h1 { font-size: 1.8em; }
        .header .sub { color: #888; font-size: 0.85em; }

        .status-row {
            display: flex;
            gap: 8px;
            justify-content: center;
            flex-wrap: wrap;
            margin-bottom: 15px;
        }
        .badge {
            padding: 5px 12px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
        }
        .badge-green { background: #00ff88; color: #000; }
        .badge-red { background: #ff4444; color: #fff; }
        .badge-yellow { background: #ffaa00; color: #000; }
        .badge-gray { background: #444; color: #aaa; }

        .controls {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 15px;
        }
        .btn {
            padding: 10px 25px;
            font-size: 0.95em;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
        }
        .btn-start { background: #00ff88; color: #000; }
        .btn-stop { background: #ff4444; color: #fff; }
        .btn:disabled { background: #333; color: #555; cursor: not-allowed; }

        .grid {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 10px;
            margin-bottom: 15px;
        }
        @media (max-width: 900px) {
            .grid { grid-template-columns: repeat(3, 1fr); }
        }
        .card {
            background: #1a1a2e;
            padding: 15px;
            border-radius: 8px;
        }
        .card h3 { color: #666; font-size: 0.7em; margin-bottom: 5px; text-transform: uppercase; }
        .card .val { font-size: 1.5em; font-weight: bold; color: #00ff88; }
        .card .val.neg { color: #ff4444; }
        .card .sub { color: #555; font-size: 0.75em; }

        .section {
            background: #1a1a2e;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 12px;
        }
        .section h2 {
            color: #00ff88;
            margin-bottom: 12px;
            font-size: 1em;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .section h2 .cnt { background: #333; padding: 2px 8px; border-radius: 8px; font-size: 0.8em; }

        .ws-stats {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            margin-bottom: 12px;
            font-size: 0.8em;
            color: #666;
        }
        .ws-stats span { color: #00ff88; }
        .ws-stats .stale span { color: #ffaa00; }

        .ob-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
            gap: 8px;
            max-height: 350px;
            overflow-y: auto;
        }
        .ob-item {
            background: #252540;
            padding: 10px 12px;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .ob-item.stinky { border: 1px solid #00ff88; background: #1a2a1a; }
        .ob-item .tk { font-weight: bold; font-size: 0.85em; }
        .ob-item .age { color: #555; font-size: 0.7em; }
        .ob-item .prices { display: flex; gap: 6px; font-size: 0.8em; }
        .ob-item .no { background: #00ff88; color: #000; padding: 2px 8px; border-radius: 10px; font-weight: bold; }
        .ob-item .yes { background: #ff4444; color: #fff; padding: 2px 8px; border-radius: 10px; }
        .ob-item .qty { color: #666; font-size: 0.7em; }

        .log {
            background: #0d0d15;
            padding: 10px;
            border-radius: 6px;
            font-size: 0.8em;
            max-height: 180px;
            overflow-y: auto;
        }
        .log-entry { padding: 3px 0; border-bottom: 1px solid #1a1a2e; }
        .log-entry.win { color: #00ff88; }
        .log-entry.loss { color: #ff4444; }

        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .pulse { animation: pulse 1s infinite; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>💩 Stinky Kalshi</h1>
            <div class="sub">Live WebSocket Orderbook • NO @ 10-15¢</div>
        </div>

        <div class="status-row">
            <span id="wsStatus" class="badge badge-red">WS: Disconnected</span>
            <span id="tradingStatus" class="badge badge-gray">Trading: Off</span>
        </div>

        <div class="controls">
            <div style="display:flex;align-items:center;gap:8px;">
                <label style="color:#888;font-size:0.85em;">Base Bet: $</label>
                <input type="number" id="baseBetInput" value="1.00" step="0.01" min="0.25" max="100"
                    style="width:70px;padding:8px;border-radius:6px;border:1px solid #333;background:#1a1a2e;color:#00ff88;font-size:1em;font-weight:bold;"
                    oninput="onBaseBetInput()" onfocus="onBaseBetInput()" onkeydown="if(event.key==='Enter'){setBaseBet();event.preventDefault();}">
                <button class="btn" onclick="setBaseBet()" style="background:#666;padding:8px 15px;">Set</button>
                <span id="baseBetStatus" style="color:#00ff88;font-size:0.8em;"></span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
                <input type="checkbox" id="slippageCheck" onchange="toggleSlippage()" style="width:18px;height:18px;cursor:pointer;">
                <label for="slippageCheck" style="color:#888;font-size:0.85em;cursor:pointer;">+3¢ slippage buffer</label>
            </div>
            <button id="startBtn" class="btn btn-start" onclick="start()">▶️ START</button>
            <button id="stopBtn" class="btn btn-stop" onclick="stop()" disabled>⏹️ STOP</button>
        </div>

        <div class="grid">
            <div class="card">
                <h3>💰 Balance</h3>
                <div class="val" id="balance">$0</div>
            </div>
            <div class="card">
                <h3>🎯 Round</h3>
                <div class="val" id="round">#1</div>
                <div class="sub">Bet #<span id="betNum">1</span></div>
            </div>
            <div class="card">
                <h3>📊 Next Bet</h3>
                <div class="val" id="nextBet">$1</div>
                <div class="sub">Cum: $<span id="cum">0</span></div>
            </div>
            <div class="card">
                <h3>🔥 Streak</h3>
                <div class="val" id="streak">0</div>
                <div class="sub">Max: <span id="maxStreak">0</span></div>
            </div>
            <div class="card">
                <h3>📈 P/L</h3>
                <div class="val" id="profit">$0</div>
                <div class="sub"><span id="bets">0</span> bets</div>
            </div>
            <div class="card">
                <h3>⚙️ Base</h3>
                <div class="val" id="base">$1</div>
                <div class="sub"><span id="range">5-20¢</span></div>
            </div>
        </div>

        <div class="section">
            <h2>📡 Live Orderbook <span class="cnt" id="subCnt">0</span></h2>
            <div class="ws-stats">
                <div>Messages: <span id="msgCnt">0</span></div>
                <div id="lastMsgDiv">Last: <span id="lastMsg">-</span></div>
                <div>Uptime: <span id="uptime">0s</span></div>
                <div>🎯 Stinky: <span id="stinkyCnt">0</span></div>
            </div>
            <div id="nextOpen" style="color:#ffaa00;font-size:0.85em;margin-bottom:8px;display:none;"></div>
            <div class="ob-grid" id="orderbooks">
                <p style="color:#555;grid-column:1/-1">Connecting...</p>
            </div>
        </div>

        <div class="section">
            <h2>📜 Log</h2>
            <div class="log" id="log"><div class="log-entry">[--:--] Init...</div></div>
        </div>
    </div>

    <script>
        async function fetchStatus() {
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                update(d);
            } catch(e) { console.error(e); }
        }

        function update(d) {
            // WS status
            const ws = document.getElementById('wsStatus');
            ws.textContent = d.ws.connected ? 'WS: Connected' : 'WS: Disconnected';
            ws.className = 'badge ' + (d.ws.connected ? 'badge-green' : 'badge-red');

            // Trading
            const tr = document.getElementById('tradingStatus');
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');
            if (d.is_trading) {
                tr.textContent = 'Trading: ACTIVE';
                tr.className = 'badge badge-yellow pulse';
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                tr.textContent = 'Trading: Off';
                tr.className = 'badge badge-gray';
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }

            // Stats
            document.getElementById('balance').textContent = '$' + d.balance.toFixed(2);
            document.getElementById('round').textContent = '#' + d.state.round_number;
            document.getElementById('betNum').textContent = d.state.bet_number;
            document.getElementById('nextBet').textContent = '$' + d.next_bet.bet_amount.toFixed(2);
            document.getElementById('cum').textContent = d.state.cumulative_loss_this_round.toFixed(2);
            document.getElementById('streak').textContent = d.state.current_streak;
            document.getElementById('maxStreak').textContent = d.state.max_streak;

            const pl = document.getElementById('profit');
            pl.textContent = '$' + d.state.total_profit.toFixed(2);
            pl.className = 'val' + (d.state.total_profit < 0 ? ' neg' : '');

            document.getElementById('bets').textContent = d.state.total_bets;
            document.getElementById('base').textContent = '$' + d.config.base_bet.toFixed(2);
            document.getElementById('range').textContent = d.config.entry_range;
            // Update input field if not focused and not recently set
            const input = document.getElementById('baseBetInput');
            if (document.activeElement !== input && !skipInputUpdate) {
                input.value = d.config.base_bet.toFixed(2);
            }
            // Sync slippage checkbox
            document.getElementById('slippageCheck').checked = d.config.use_slippage;

            // WS stats
            document.getElementById('subCnt').textContent = d.ws.subscribed_count + ' mkts';
            document.getElementById('msgCnt').textContent = d.ws.message_count.toLocaleString();
            const age = d.ws.last_message_age;
            const lastEl = document.getElementById('lastMsg');
            const lastDiv = document.getElementById('lastMsgDiv');
            if (age < 999) {
                lastEl.textContent = age.toFixed(1) + 's';
                lastDiv.className = age > 5 ? 'stale' : '';
            } else {
                lastEl.textContent = '-';
            }
            document.getElementById('uptime').textContent = Math.floor(d.ws.uptime_seconds) + 's';
            document.getElementById('stinkyCnt').textContent = d.stinky_count;

            // Next market open indicator
            const nextOpenEl = document.getElementById('nextOpen');
            if (d.ws.next_market_open && (!d.orderbooks || d.orderbooks.length === 0)) {
                nextOpenEl.textContent = '⏰ Next market opens in: ' + d.ws.next_market_open;
                nextOpenEl.style.display = 'block';
            } else {
                nextOpenEl.style.display = 'none';
            }

            // Orderbooks
            const obEl = document.getElementById('orderbooks');
            if (d.orderbooks && d.orderbooks.length > 0) {
                obEl.innerHTML = d.orderbooks.map(ob => {
                    const stinky = ob.no_ask && ob.no_ask >= d.config.min_entry && ob.no_ask <= d.config.max_entry;
                    const age = ((Date.now()/1000) - ob.last_update).toFixed(1);
                    const short = ob.ticker.split('-').pop();
                    return `<div class="ob-item ${stinky?'stinky':''}">
                        <div><div class="tk">${short}</div><div class="age">${age}s • ${ob.update_count}x</div></div>
                        <div class="prices">
                            ${ob.no_ask ? `<span class="no">NO ${ob.no_ask}¢</span>` : ''}
                            ${ob.yes_ask ? `<span class="yes">YES ${ob.yes_ask}¢</span>` : ''}
                            ${ob.no_ask_quantity ? `<span class="qty">${ob.no_ask_quantity}</span>` : ''}
                        </div>
                    </div>`;
                }).join('');
            } else {
                if (!d.ws.connected) {
                    obEl.innerHTML = '<p style="color:#555;grid-column:1/-1">Disconnected</p>';
                } else if (d.ws.next_market_open) {
                    obEl.innerHTML = '<p style="color:#555;grid-column:1/-1">No active markets - waiting for next trading window</p>';
                } else {
                    obEl.innerHTML = '<p style="color:#555;grid-column:1/-1">Waiting for data...</p>';
                }
            }

            // Log
            if (d.log && d.log.length) {
                document.getElementById('log').innerHTML = d.log.slice(-25).reverse()
                    .map(e => `<div class="log-entry ${e.type||''}">${e.message}</div>`).join('');
            }
        }

        async function start() { await fetch('/api/start', {method:'POST'}); fetchStatus(); }
        async function stop() { await fetch('/api/stop', {method:'POST'}); fetchStatus(); }

        let skipInputUpdate = false;
        let inputUpdateTimeout = null;

        function onBaseBetInput() {
            // Prevent status updates from overwriting user input for 3 seconds
            skipInputUpdate = true;
            if (inputUpdateTimeout) clearTimeout(inputUpdateTimeout);
            inputUpdateTimeout = setTimeout(() => { skipInputUpdate = false; }, 3000);
        }

        async function setBaseBet() {
            const input = document.getElementById('baseBetInput');
            const statusEl = document.getElementById('baseBetStatus');
            const rawValue = input.value;
            const val = parseFloat(rawValue);

            console.log('💩 setBaseBet called');
            console.log('💩 input.value (raw):', rawValue);
            console.log('💩 parseFloat result:', val);
            statusEl.textContent = '...';
            statusEl.style.color = '#ffaa00';

            if (isNaN(val)) {
                console.log('💩 REJECTED: val is NaN');
                statusEl.textContent = '❌ Invalid';
                statusEl.style.color = '#ff4444';
                return;
            }

            if (val < 0.25 || val > 100) {
                console.log('💩 REJECTED: val out of range:', val);
                statusEl.textContent = '❌ $0.25-$100';
                statusEl.style.color = '#ff4444';
                return;
            }

            console.log('💩 SENDING to server:', {base_bet: val});
            skipInputUpdate = true;

            try {
                const res = await fetch('/api/set_base_bet', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({base_bet: val})
                });
                const data = await res.json();
                console.log('💩 SERVER RESPONSE:', data);

                if (data.status === 'ok') {
                    input.value = data.base_bet.toFixed(2);
                    document.getElementById('base').textContent = '$' + data.base_bet.toFixed(2);
                    statusEl.textContent = '✓ Set!';
                    statusEl.style.color = '#00ff88';
                    console.log('💩 SUCCESS: base_bet set to', data.base_bet);
                    setTimeout(() => { statusEl.textContent = ''; }, 2000);
                } else {
                    console.log('💩 SERVER ERROR:', data);
                    statusEl.textContent = '❌ Error';
                    statusEl.style.color = '#ff4444';
                }
            } catch (err) {
                console.log('💩 FETCH ERROR:', err);
                statusEl.textContent = '❌ Failed';
                statusEl.style.color = '#ff4444';
            }

            setTimeout(() => { skipInputUpdate = false; }, 2000);
            fetchStatus();
        }

        async function toggleSlippage() {
            const checked = document.getElementById('slippageCheck').checked;
            console.log('💩 toggleSlippage:', checked);
            await fetch('/api/set_slippage', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({use_slippage: checked})
            });
            fetchStatus();
        }

        fetchStatus();
        setInterval(fetchStatus, 500);
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/status')
def api_status():
    global trader, ws_client, is_trading, config

    # Check if we need to log daily balance
    check_daily_balance_log()

    default_state = {
        "round_number": 1, "bet_number": 1, "cumulative_loss_this_round": 0,
        "current_streak": 0, "max_streak": 0, "total_profit": 0,
        "total_bets": 0, "total_wins": 0,
    }

    ws_status = {
        "connected": False, "subscribed_count": 0, "message_count": 0,
        "last_message_age": 9999, "uptime_seconds": 0, "next_market_open": None,
    }

    orderbooks = []
    stinky_count = 0

    # Check for next market open time if no active markets
    if rest_client and (not ws_client or len(ws_client.subscribed_tickers) == 0):
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            markets = rest_client.get_btc_15min_markets()
            next_open = None
            for m in markets:
                if m.open_time:
                    try:
                        open_dt = datetime.fromisoformat(m.open_time.replace('Z', '+00:00'))
                        if open_dt > now and (next_open is None or open_dt < next_open):
                            next_open = open_dt
                    except:
                        pass
            if next_open:
                mins_until = (next_open - now).total_seconds() / 60
                ws_status["next_market_open"] = f"{mins_until:.0f}min ({next_open.strftime('%H:%M UTC')})"
        except:
            pass

    if ws_client:
        ws_status = ws_client.get_status()
        for ob in ws_client.orderbooks.values():
            ob_dict = ob.to_dict()
            orderbooks.append(ob_dict)
            if config and ob.is_stinky(config.stink.min_entry_price, config.stink.max_entry_price):
                stinky_count += 1

        orderbooks.sort(key=lambda x: (
            not (x['no_ask'] and config and config.stink.min_entry_price <= x['no_ask'] <= config.stink.max_entry_price),
            x['no_ask'] or 999
        ))

    balance = 0
    state = default_state
    next_bet = {"bet_amount": config.stink.base_bet_dollars if config else 1.0}

    if trader:
        status = trader.get_status()
        balance = status["balance"]
        state = status["state"]
        next_bet = status["next_bet"]

    return jsonify({
        "is_trading": is_trading,
        "balance": balance,
        "state": state,
        "next_bet": next_bet,
        "config": {
            "base_bet": config.stink.base_bet_dollars if config else 1.0,
            "entry_range": f"{config.stink.min_entry_price}-{config.stink.max_entry_price}¢" if config else "5-20¢",
            "min_entry": config.stink.min_entry_price if config else 5,
            "max_entry": config.stink.max_entry_price if config else 20,
            "use_slippage": use_slippage,
            "slippage_cents": SLIPPAGE_CENTS,
        },
        "ws": ws_status,
        "orderbooks": orderbooks[:50],
        "stinky_count": stinky_count,
        "log": activity_log,
    })


@app.route('/api/start', methods=['POST'])
def api_start():
    global trader, trading_thread, is_trading

    if is_trading:
        return jsonify({"status": "already running"})

    try:
        is_trading = True
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()
        return jsonify({"status": "started"})
    except Exception as e:
        log_activity(f"Failed to start: {e}", "loss")
        is_trading = False
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def api_stop():
    global is_trading
    is_trading = False
    log_activity("Stop requested...")
    return jsonify({"status": "stopping"})


@app.route('/api/set_base_bet', methods=['POST'])
def api_set_base_bet():
    global trader, config

    print(f"💩 ========== SET BASE BET REQUEST ==========")
    data = request.get_json()
    print(f"💩 RAW REQUEST DATA: {data}")

    base_bet = data.get('base_bet', 1.0)
    print(f"💩 PARSED base_bet: {base_bet} (type: {type(base_bet).__name__})")

    if base_bet < 0.25 or base_bet > 100:
        print(f"💩 REJECTED: base_bet {base_bet} out of range 0.25-100")
        return jsonify({"status": "error", "message": "Base bet must be between $0.25 and $100"}), 400

    # Update config
    old_config_val = config.stink.base_bet_dollars if config else None
    if config:
        config.stink.base_bet_dollars = base_bet
        print(f"💩 CONFIG UPDATED: {old_config_val} -> {config.stink.base_bet_dollars}")

    # Update trader
    old_trader_val = trader.stink.base_bet_dollars if trader else None
    if trader:
        trader.stink.base_bet_dollars = base_bet
        print(f"💩 TRADER UPDATED: {old_trader_val} -> {trader.stink.base_bet_dollars}")

    log_activity(f"Base bet set to ${base_bet:.2f}")
    print(f"💩 BASE BET SUCCESSFULLY SET TO: ${base_bet:.2f}")
    print(f"💩 ==========================================")
    return jsonify({"status": "ok", "base_bet": base_bet})


@app.route('/api/set_slippage', methods=['POST'])
def api_set_slippage():
    global use_slippage

    data = request.get_json()
    use_slippage = data.get('use_slippage', True)

    print(f"💩 SLIPPAGE SET TO: {use_slippage} (+{SLIPPAGE_CENTS}¢ buffer)")
    log_activity(f"Slippage {'ON' if use_slippage else 'OFF'} (+{SLIPPAGE_CENTS}¢)")
    return jsonify({"status": "ok", "use_slippage": use_slippage})


def main():
    global trader, ws_client, rest_client, config, ws_thread

    print("💩" * 40)
    print("💩 STINKY KALSHI - LIVE ORDERBOOK 💩")
    print("💩" * 40)

    config = load_config()
    rest_client = KalshiClient(config.kalshi)

    try:
        trader = StinkTrader(config)
        log_activity(f"Trader ready. Balance: ${trader.balance:.2f}")
    except Exception as e:
        log_activity(f"Trader init failed: {e}", "loss")

    ws_client = KalshiWebSocket(
        config.kalshi,
        on_orderbook_update=on_orderbook_update,
        on_connection_change=on_connection_change,
    )

    ws_thread = threading.Thread(target=run_ws_loop, daemon=True)
    ws_thread.start()
    log_activity("WebSocket thread started")

    port = int(os.environ.get("PORT", 8080))
    print(f"\n💩 http://localhost:{port}\n")

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
