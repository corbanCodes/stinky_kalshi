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


async def refresh_market_subscriptions():
    """Fetch current BTC 15-min markets and subscribe to their orderbooks."""
    global rest_client, ws_client

    try:
        markets = rest_client.get_btc_15min_markets()
        active_tickers = {m.ticker for m in markets if m.status == "open"}

        # Unsubscribe from closed markets
        for ticker in list(ws_client.subscribed_tickers):
            if ticker not in active_tickers:
                await ws_client.unsubscribe(ticker)
                ws_client.orderbooks.pop(ticker, None)

        # Subscribe to new markets
        new_count = 0
        for ticker in active_tickers:
            if ticker not in ws_client.subscribed_tickers:
                await ws_client.subscribe(ticker)
                new_count += 1
                await asyncio.sleep(0.05)

        if new_count > 0:
            log_activity(f"Subscribed to {new_count} new markets (total: {len(ws_client.subscribed_tickers)})")

    except Exception as e:
        log_activity(f"Market refresh error: {e}", "loss")


async def ws_main_loop():
    """Main WebSocket loop with market discovery."""
    global ws_client, rest_client, is_ws_running

    is_ws_running = True
    log_activity("Starting WebSocket...")

    reconnect_delay = 1
    last_refresh = 0
    refresh_interval = 60  # Check for new markets every 60s

    while is_ws_running:
        try:
            # Connect if needed
            if not ws_client._connected:
                if not await ws_client.connect():
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30)
                    continue
                reconnect_delay = 1

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

        except Exception as e:
            log_activity(f"WebSocket error: {e}", "loss")
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

    while is_trading:
        try:
            trader.check_settlements()

            # Use WebSocket orderbooks for opportunities
            if ws_client and ws_client.is_connected:
                opportunities = ws_client.get_stinky_opportunities(
                    min_price=trader.stink.min_entry_price,
                    max_price=trader.stink.max_entry_price
                )

                if opportunities and trader.can_afford_bet():
                    opportunities.sort(key=lambda ob: ob.no_ask or 999)
                    best = opportunities[0]

                    if best.ticker not in trader._bet_tickers:
                        market = MarketData(
                            ticker=best.ticker,
                            yes_bid=best.best_yes_bid or 0,
                            yes_ask=best.yes_ask or 0,
                            no_bid=best.best_no_bid or 0,
                            no_ask=best.no_ask or 0,
                            volume=0,
                            status="open",
                            close_time="",
                        )
                        result = trader.execute_stink_bet(market)
                        if result:
                            log_activity(f"BET: {result.ticker} @ {result.entry_price}¢ (${result.bet_amount:.2f})", "win")

                elif not trader.can_afford_bet():
                    bet_amount = trader.state.get_next_bet_amount(trader.stink.base_bet_dollars)
                    log_activity(f"Cannot afford ${bet_amount:.2f}", "loss")
                    is_trading = False
                    break

            time.sleep(1)

        except Exception as e:
            log_activity(f"Trading error: {e}", "loss")
            time.sleep(5)

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
                obEl.innerHTML = d.ws.connected
                    ? '<p style="color:#555;grid-column:1/-1">Waiting for data...</p>'
                    : '<p style="color:#555;grid-column:1/-1">Disconnected</p>';
            }

            // Log
            if (d.log && d.log.length) {
                document.getElementById('log').innerHTML = d.log.slice(-25).reverse()
                    .map(e => `<div class="log-entry ${e.type||''}">${e.message}</div>`).join('');
            }
        }

        async function start() { await fetch('/api/start', {method:'POST'}); fetchStatus(); }
        async function stop() { await fetch('/api/stop', {method:'POST'}); fetchStatus(); }

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

    default_state = {
        "round_number": 1, "bet_number": 1, "cumulative_loss_this_round": 0,
        "current_streak": 0, "max_streak": 0, "total_profit": 0,
        "total_bets": 0, "total_wins": 0,
    }

    ws_status = {
        "connected": False, "subscribed_count": 0, "message_count": 0,
        "last_message_age": 9999, "uptime_seconds": 0,
    }

    orderbooks = []
    stinky_count = 0

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
