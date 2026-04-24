#!/usr/bin/env python3
"""
💩 Stinky Kalshi Web UI 💩
Flask dashboard with Start/Stop controls
"""

import os
import sys
import json
import threading
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config
from src.stink_trader import StinkTrader

app = Flask(__name__)

# Global state
trader = None
trading_thread = None
is_running = False
last_scan_result = None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>💩 Stinky Kalshi</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }

        .header {
            text-align: center;
            padding: 30px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border-radius: 16px;
            margin-bottom: 30px;
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header .poop { font-size: 3em; }
        .header p { color: #888; }

        .controls {
            display: flex;
            gap: 20px;
            justify-content: center;
            margin-bottom: 30px;
        }
        .btn {
            padding: 15px 40px;
            font-size: 1.2em;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-start {
            background: #00ff88;
            color: #000;
        }
        .btn-start:hover { background: #00cc6a; }
        .btn-start:disabled { background: #333; color: #666; cursor: not-allowed; }

        .btn-stop {
            background: #ff4444;
            color: #fff;
        }
        .btn-stop:hover { background: #cc3333; }
        .btn-stop:disabled { background: #333; color: #666; cursor: not-allowed; }

        .status-badge {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 20px;
            font-weight: bold;
            margin-bottom: 20px;
        }
        .status-running { background: #00ff88; color: #000; }
        .status-stopped { background: #ff4444; color: #fff; }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .card {
            background: #1a1a2e;
            padding: 25px;
            border-radius: 12px;
        }
        .card h3 {
            color: #888;
            font-size: 0.9em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }
        .card .value {
            font-size: 2em;
            font-weight: bold;
            color: #00ff88;
        }
        .card .value.negative { color: #ff4444; }
        .card .subtitle { color: #666; font-size: 0.85em; margin-top: 5px; }

        .section {
            background: #1a1a2e;
            padding: 25px;
            border-radius: 12px;
            margin-bottom: 20px;
        }
        .section h2 {
            color: #00ff88;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .log {
            background: #0d0d15;
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.85em;
            max-height: 300px;
            overflow-y: auto;
        }
        .log-entry { padding: 5px 0; border-bottom: 1px solid #1a1a2e; }
        .log-entry.win { color: #00ff88; }
        .log-entry.loss { color: #ff4444; }
        .log-entry .time { color: #666; }

        .opportunity {
            background: #252540;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .opportunity .ticker { font-weight: bold; }
        .opportunity .price {
            background: #00ff88;
            color: #000;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
        }

        .config-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .config-item {
            background: #252540;
            padding: 15px;
            border-radius: 8px;
        }
        .config-item label { color: #888; font-size: 0.85em; }
        .config-item .val { font-size: 1.2em; font-weight: bold; margin-top: 5px; }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .pulsing { animation: pulse 1s infinite; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="poop">💩</div>
            <h1>Stinky Kalshi</h1>
            <p>Quadratic Longshot Betting • NO side @ 10-15¢</p>
        </div>

        <div style="text-align: center;">
            <div id="statusBadge" class="status-badge status-stopped">STOPPED</div>
        </div>

        <div class="controls">
            <button id="startBtn" class="btn btn-start" onclick="startTrading()">
                ▶️ START
            </button>
            <button id="stopBtn" class="btn btn-stop" onclick="stopTrading()" disabled>
                ⏹️ STOP
            </button>
        </div>

        <div class="grid">
            <div class="card">
                <h3>💰 Balance</h3>
                <div class="value" id="balance">$0.00</div>
            </div>
            <div class="card">
                <h3>🎯 Current Round</h3>
                <div class="value" id="round">#1</div>
                <div class="subtitle">Bet #<span id="betNum">1</span> in round</div>
            </div>
            <div class="card">
                <h3>📊 Next Bet</h3>
                <div class="value" id="nextBet">$1.00</div>
                <div class="subtitle">Cumulative: $<span id="cumulative">0.00</span></div>
            </div>
            <div class="card">
                <h3>🔥 Current Streak</h3>
                <div class="value" id="streak">0</div>
                <div class="subtitle">Max ever: <span id="maxStreak">0</span></div>
            </div>
            <div class="card">
                <h3>📈 Total Profit</h3>
                <div class="value" id="profit">$0.00</div>
                <div class="subtitle"><span id="totalBets">0</span> bets, <span id="totalWins">0</span> wins</div>
            </div>
            <div class="card">
                <h3>⚙️ Base Bet</h3>
                <div class="value" id="baseBet">$1.00</div>
                <div class="subtitle">Entry: <span id="entryRange">10-15¢</span></div>
            </div>
        </div>

        <div class="section">
            <h2>💩 Opportunities</h2>
            <div id="opportunities">
                <p style="color: #666;">Click START to scan for opportunities</p>
            </div>
        </div>

        <div class="section">
            <h2>📜 Activity Log</h2>
            <div class="log" id="log">
                <div class="log-entry">
                    <span class="time">[--:--:--]</span> Waiting to start...
                </div>
            </div>
        </div>
    </div>

    <script>
        let refreshInterval = null;

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                updateUI(data);
            } catch (e) {
                console.error('Failed to fetch status:', e);
            }
        }

        function updateUI(data) {
            // Update running status
            const badge = document.getElementById('statusBadge');
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');

            if (data.is_running) {
                badge.textContent = 'RUNNING';
                badge.className = 'status-badge status-running pulsing';
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                badge.textContent = 'STOPPED';
                badge.className = 'status-badge status-stopped';
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }

            // Update stats
            document.getElementById('balance').textContent = '$' + data.balance.toFixed(2);
            document.getElementById('round').textContent = '#' + data.state.round_number;
            document.getElementById('betNum').textContent = data.state.bet_number;
            document.getElementById('nextBet').textContent = '$' + data.next_bet.bet_amount.toFixed(2);
            document.getElementById('cumulative').textContent = data.state.cumulative_loss_this_round.toFixed(2);
            document.getElementById('streak').textContent = data.state.current_streak;
            document.getElementById('maxStreak').textContent = data.state.max_streak;

            const profitEl = document.getElementById('profit');
            profitEl.textContent = '$' + data.state.total_profit.toFixed(2);
            profitEl.className = 'value' + (data.state.total_profit < 0 ? ' negative' : '');

            document.getElementById('totalBets').textContent = data.state.total_bets;
            document.getElementById('totalWins').textContent = data.state.total_wins;
            document.getElementById('baseBet').textContent = '$' + data.config.base_bet.toFixed(2);
            document.getElementById('entryRange').textContent = data.config.entry_range;

            // Update opportunities
            const oppsEl = document.getElementById('opportunities');
            if (data.opportunities && data.opportunities.length > 0) {
                oppsEl.innerHTML = data.opportunities.map(o => `
                    <div class="opportunity">
                        <span class="ticker">${o.ticker}</span>
                        <span class="price">NO @ ${o.no_ask}¢</span>
                    </div>
                `).join('');
            } else {
                oppsEl.innerHTML = '<p style="color: #666;">No opportunities in range right now</p>';
            }

            // Update log
            if (data.log && data.log.length > 0) {
                const logEl = document.getElementById('log');
                logEl.innerHTML = data.log.slice(-20).reverse().map(entry => `
                    <div class="log-entry ${entry.type || ''}">${entry.message}</div>
                `).join('');
            }
        }

        async function startTrading() {
            try {
                await fetch('/api/start', { method: 'POST' });
                fetchStatus();
            } catch (e) {
                console.error('Failed to start:', e);
            }
        }

        async function stopTrading() {
            try {
                await fetch('/api/stop', { method: 'POST' });
                fetchStatus();
            } catch (e) {
                console.error('Failed to stop:', e);
            }
        }

        // Initial fetch and start polling
        fetchStatus();
        refreshInterval = setInterval(fetchStatus, 2000);
    </script>
</body>
</html>
"""

# Activity log
activity_log = []

def log_activity(message, entry_type=""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    activity_log.append({
        "message": f"[{timestamp}] {message}",
        "type": entry_type
    })
    # Keep last 100 entries
    if len(activity_log) > 100:
        activity_log.pop(0)
    print(f"💩 {message}")


def trading_loop():
    """Background trading loop."""
    global is_running, trader

    log_activity("Trading started! 💩")

    while is_running:
        try:
            # Check settlements
            trader.check_settlements()

            # Find and execute opportunities
            if trader.can_afford_bet():
                result = trader.run_once()
                if result:
                    log_activity(f"BET PLACED: {result.ticker} @ {result.entry_price}¢", "")
            else:
                log_activity(f"Cannot afford next bet (${trader.state.get_next_bet_amount(trader.stink.base_bet_dollars):.2f})", "loss")
                is_running = False
                break

            time.sleep(30)  # Scan every 30 seconds

        except Exception as e:
            log_activity(f"Error: {str(e)}", "loss")
            time.sleep(5)

    log_activity("Trading stopped.")


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/status')
def api_status():
    global trader, is_running

    if trader is None:
        return jsonify({
            "is_running": False,
            "balance": 0,
            "state": {
                "round_number": 1,
                "bet_number": 1,
                "cumulative_loss_this_round": 0,
                "current_streak": 0,
                "max_streak": 0,
                "total_profit": 0,
                "total_bets": 0,
                "total_wins": 0,
            },
            "next_bet": {"bet_amount": 1.0},
            "config": {"base_bet": 1.0, "entry_range": "10-15¢"},
            "opportunities": [],
            "log": activity_log
        })

    # Get opportunities
    opportunities = []
    try:
        opps = trader.find_opportunities()
        opportunities = [{"ticker": m.ticker, "no_ask": m.no_ask} for m in opps[:5]]
    except:
        pass

    status = trader.get_status()
    return jsonify({
        "is_running": is_running,
        "balance": status["balance"],
        "state": status["state"],
        "next_bet": status["next_bet"],
        "config": status["config"],
        "opportunities": opportunities,
        "log": activity_log
    })


@app.route('/api/start', methods=['POST'])
def api_start():
    global trader, trading_thread, is_running

    if is_running:
        return jsonify({"status": "already running"})

    try:
        # Initialize trader if needed
        if trader is None:
            config = load_config()
            trader = StinkTrader(config)
            log_activity(f"Trader initialized. Balance: ${trader.balance:.2f}")

        # Start trading thread
        is_running = True
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()

        return jsonify({"status": "started"})
    except Exception as e:
        log_activity(f"Failed to start: {str(e)}", "loss")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def api_stop():
    global is_running

    is_running = False
    log_activity("Stop requested...")
    return jsonify({"status": "stopping"})


def main():
    global trader

    print("💩" * 40)
    print("💩 STINKY KALSHI WEB UI 💩")
    print("💩" * 40)

    # Pre-initialize trader to check credentials
    try:
        config = load_config()
        trader = StinkTrader(config)
        log_activity(f"Connected to Kalshi. Balance: ${trader.balance:.2f}")
    except Exception as e:
        log_activity(f"Warning: Could not initialize trader: {e}", "loss")

    port = int(os.environ.get("PORT", 8080))
    print(f"\n💩 Starting web server on port {port}...")
    print(f"💩 Open http://localhost:{port} in your browser\n")

    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
