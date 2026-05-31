# -*- coding: utf-8 -*-
"""
Monitoring server: Web dashboard + Telegram alerts.
Run alongside server_bot.py on GCP.

Usage:
    python3 monitor.py
    nohup python3 monitor.py > monitor.log 2>&1 &

Web dashboard: http://<GCP_IP>:8080
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))

import requests
from flask import Flask, jsonify, Response

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOG_FILE = os.getenv("LOG_FILE", "trading.log")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

app = Flask(__name__)

bot_state = {
    "status": "unknown",
    "last_update": "",
    "krw": 0,
    "total": 0,
    "invested": 0,
    "daily_pnl": "",
    "positions": [],
    "recent_trades": [],
    "recent_logs": [],
    "coins": {},
}


# ─── Telegram ─────────────────────────────────────────────
def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        pass


# ─── Log Watcher ──────────────────────────────────────────
class LogWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.last_pos = 0
        self.seen_lines = set()

    def run(self):
        while True:
            try:
                self._read_log()
            except Exception:
                pass
            time.sleep(5)

    def _read_log(self):
        path = Path(LOG_FILE)
        if not path.exists():
            self.last_pos = 0
            return

        file_size = path.stat().st_size
        if file_size < self.last_pos:
            self.last_pos = 0

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.last_pos)
            new_lines = f.readlines()
            self.last_pos = f.tell()

        for line in new_lines:
            line = line.strip()
            if not line:
                continue

            bot_state["recent_logs"].append(line)
            bot_state["recent_logs"] = bot_state["recent_logs"][-100:]
            bot_state["last_update"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

            self._parse_line(line)

    def _parse_line(self, line: str):
        if "portfolio total=" in line:
            m = re.search(r"total=(\d+).*cash=(\d+).*invested=(\d+)", line)
            if m:
                bot_state["total"] = int(m.group(1))
                bot_state["krw"] = int(m.group(2))
                bot_state["invested"] = int(m.group(3))
            bot_state["status"] = "running"

        if "bot check started" in line.lower() or "check started" in line.lower():
            bot_state["status"] = "running"

        m = re.search(
            r"\[KRW-(\w+)\] price=(\d+) ema\d+=(\d+) ema\d+=(\d+) rsi=([\d.]+) atr=([\d.]+)% vol=([\d.]+)x mom=(-?[\d.]+)%",
            line,
        )
        if m:
            coin = m.group(1)
            bot_state["coins"][coin] = {
                "price": int(m.group(2)),
                "ema_fast": int(m.group(3)),
                "ema_slow": int(m.group(4)),
                "rsi": float(m.group(5)),
                "atr": float(m.group(6)),
                "vol": float(m.group(7)),
                "mom": float(m.group(8)),
                "signal": "",
                "reason": "",
            }

        m = re.search(r"\[KRW-(\w+)\] signal=(\w+) reason=(.*)", line)
        if m:
            coin = m.group(1)
            if coin in bot_state["coins"]:
                bot_state["coins"][coin]["signal"] = m.group(2)
                bot_state["coins"][coin]["reason"] = m.group(3)

        if "BUY" in line and ("market order" in line.lower() or "buy" in line):
            if "skipped" not in line.lower() and "DRY_RUN" not in line:
                trade = {"time": line[:19], "action": "BUY", "detail": line}
                bot_state["recent_trades"].append(trade)
                bot_state["recent_trades"] = bot_state["recent_trades"][-50:]
                send_telegram(f"🟢 <b>BUY</b>\n{line[20:]}")

        if "SELL" in line and "sell" in line.lower():
            if "skipped" not in line.lower() and "DRY_RUN" not in line:
                trade = {"time": line[:19], "action": "SELL", "detail": line}
                bot_state["recent_trades"].append(trade)
                bot_state["recent_trades"] = bot_state["recent_trades"][-50:]
                send_telegram(f"🔴 <b>SELL</b>\n{line[20:]}")

        if "risk exit" in line.lower():
            send_telegram(f"⚠️ <b>RISK EXIT</b>\n{line[20:]}")

        if "error" in line.lower() or "failed" in line.lower():
            send_telegram(f"❌ <b>ERROR</b>\n{line[20:]}")

        if "stopped" in line.lower():
            bot_state["status"] = "stopped"
            send_telegram(f"🛑 <b>BOT STOPPED</b>\n{line[20:]}")


# ─── Web Dashboard ────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="10">
<title>AUTO TRADER Monitor</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; padding:20px; }
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
.header h1 { color:#58a6ff; font-size:24px; }
.status { padding:6px 16px; border-radius:20px; font-weight:bold; font-size:14px; }
.status.running { background:#3fb950; color:#0d1117; }
.status.stopped { background:#f85149; color:white; }
.status.unknown { background:#8b949e; color:#0d1117; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:20px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; }
.card .label { color:#8b949e; font-size:12px; margin-bottom:4px; }
.card .value { font-size:22px; font-weight:bold; font-family:Consolas,monospace; }
.section { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin-bottom:16px; }
.section h2 { color:#58a6ff; font-size:16px; margin-bottom:12px; }
.log-box { background:#0d1117; border:1px solid #30363d; border-radius:6px; padding:12px;
           font-family:Consolas,monospace; font-size:12px; max-height:400px; overflow-y:auto;
           white-space:pre-wrap; word-break:break-all; line-height:1.6; }
.trade-row { padding:8px 12px; border-bottom:1px solid #21262d; font-family:Consolas,monospace; font-size:13px; }
.trade-row:last-child { border-bottom:none; }
.buy { color:#3fb950; } .sell { color:#f85149; }
.footer { text-align:center; color:#8b949e; font-size:12px; margin-top:20px; }
.green { color:#3fb950; } .red { color:#f85149; }
.coin-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; margin-bottom:16px; }
.coin-card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; }
.coin-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
.coin-name { font-size:18px; font-weight:bold; }
.coin-signal { padding:4px 12px; border-radius:12px; font-size:12px; font-weight:bold; text-transform:uppercase; }
.signal-buy { background:#3fb950; color:#0d1117; }
.signal-sell { background:#f85149; color:white; }
.signal-hold { background:#8b949e; color:#0d1117; }
.coin-metrics { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.coin-metric { display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #21262d; font-size:13px; font-family:Consolas,monospace; }
.coin-metric .mk { color:#8b949e; } .coin-metric .mv { color:#e6edf3; }
.coin-reason { margin-top:8px; font-size:11px; color:#8b949e; font-style:italic; }
</style>
</head>
<body>
<div class="header">
  <h1>AUTO TRADER</h1>
  <div>
    <span class="status {{STATUS_CLASS}}">{{STATUS}}</span>
    <span style="color:#8b949e;font-size:12px;margin-left:12px;">{{LAST_UPDATE}}</span>
  </div>
</div>

<div class="cards">
  <div class="card"><div class="label">KRW Balance</div><div class="value">{{KRW}}</div></div>
  <div class="card"><div class="label">Invested</div><div class="value">{{INVESTED}}</div></div>
  <div class="card"><div class="label">Total Assets</div><div class="value">{{TOTAL}}</div></div>
  <div class="card"><div class="label">Trades</div><div class="value">{{TRADE_COUNT}}</div></div>
</div>

<div class="section">
  <h2>Coin Status</h2>
  <div class="coin-grid">{{COINS}}</div>
</div>

<div class="section">
  <h2>Recent Trades</h2>
  {{TRADES}}
</div>

<div class="section">
  <h2>Live Log (last 30 lines)</h2>
  <div class="log-box">{{LOGS}}</div>
</div>

<div class="footer">Auto-refresh every 10 seconds | Page loaded at {{NOW}}</div>
</body>
</html>"""


@app.route("/")
def dashboard():
    status = bot_state["status"]
    status_class = status if status in ("running", "stopped") else "unknown"

    coin_colors = {"BTC": "#f7931a", "ETH": "#627eea", "SOL": "#00ffa3", "XRP": "#00aae4", "HBAR": "#8259ef"}
    coins_html = ""
    for coin, data in bot_state["coins"].items():
        sig = data.get("signal", "hold")
        sig_cls = f"signal-{sig}" if sig in ("buy", "sell") else "signal-hold"
        color = coin_colors.get(coin, "#58a6ff")
        mom = data.get("mom", 0)
        mom_cls = "green" if mom >= 0 else "red"
        coins_html += f'''<div class="coin-card">
  <div class="coin-header">
    <span class="coin-name" style="color:{color}">{coin}</span>
    <span class="coin-signal {sig_cls}">{sig}</span>
  </div>
  <div class="coin-metrics">
    <div class="coin-metric"><span class="mk">Price</span><span class="mv">{data.get("price",0):,}</span></div>
    <div class="coin-metric"><span class="mk">RSI</span><span class="mv">{data.get("rsi",0):.1f}</span></div>
    <div class="coin-metric"><span class="mk">EMA Fast</span><span class="mv">{data.get("ema_fast",0):,}</span></div>
    <div class="coin-metric"><span class="mk">EMA Slow</span><span class="mv">{data.get("ema_slow",0):,}</span></div>
    <div class="coin-metric"><span class="mk">ATR</span><span class="mv">{data.get("atr",0):.2f}%</span></div>
    <div class="coin-metric"><span class="mk">Volume</span><span class="mv">{data.get("vol",0):.2f}x</span></div>
    <div class="coin-metric"><span class="mk">Momentum</span><span class="mv {mom_cls}">{mom:+.2f}%</span></div>
  </div>
  <div class="coin-reason">{data.get("reason","")}</div>
</div>\n'''
    if not coins_html:
        coins_html = '<div style="color:#8b949e;">Waiting for data...</div>'

    trades_html = ""
    for t in reversed(bot_state["recent_trades"][-20:]):
        cls = "buy" if t["action"] == "BUY" else "sell"
        trades_html += f'<div class="trade-row {cls}">{t["detail"]}</div>\n'
    if not trades_html:
        trades_html = '<div class="trade-row" style="color:#8b949e;">No trades yet</div>'

    logs = "\n".join(bot_state["recent_logs"][-30:])

    html = DASHBOARD_HTML
    html = html.replace("{{STATUS}}", status.upper())
    html = html.replace("{{STATUS_CLASS}}", status_class)
    html = html.replace("{{LAST_UPDATE}}", bot_state["last_update"] or "-")
    html = html.replace("{{KRW}}", f'{bot_state["krw"]:,}')
    html = html.replace("{{INVESTED}}", f'{bot_state["invested"]:,}')
    html = html.replace("{{TOTAL}}", f'{bot_state["total"]:,}')
    html = html.replace("{{TRADE_COUNT}}", str(len(bot_state["recent_trades"])))
    html = html.replace("{{COINS}}", coins_html)
    html = html.replace("{{TRADES}}", trades_html)
    html = html.replace("{{LOGS}}", logs)
    html = html.replace("{{NOW}}", datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))

    return Response(html, content_type="text/html; charset=utf-8")


@app.route("/api/status")
def api_status():
    return jsonify(bot_state)


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "status": bot_state["status"]})


def main():
    send_telegram("🚀 <b>MONITOR STARTED</b>\nWeb dashboard is now live.")

    watcher = LogWatcher()
    watcher.start()

    print(f"Dashboard: http://0.0.0.0:{WEB_PORT}")
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)


if __name__ == "__main__":
    main()
