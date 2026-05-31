# -*- coding: utf-8 -*-
import sys
import io
import tkinter as tk
from tkinter import ttk
import threading
import time
from datetime import datetime, timedelta
from collections import defaultdict

import pyupbit
import requests
import pandas as pd

from config import (
    UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY,
    KRW_BALANCE_BUFFER, MAX_DAILY_LOSS_PCT,
    TRADE_RATIO, MIN_TRADE_AMOUNT_KRW,
)

# ─── Settings ──────────────────────────────────────────────
CHECK_INTERVAL = 60
DAILY_TARGET_PCT = 2.0
TAKE_PROFIT_PCT = 2.5
STOP_LOSS_PCT = -1.2
TRAILING_STOP_PCT = 1.0
MAX_POSITIONS = 3
SCAN_WINDOW_SECONDS = 900
MIN_SCAN_POINTS = 5
MIN_MOMENTUM_PCT = 0.6
MAX_MOMENTUM_PCT = 4.0
MIN_ACCEL_PCT = 0.15
COOLDOWN_SECONDS = 600

# ─── Colors ────────────────────────────────────────────────
BG = "#0d1117"
BG2 = "#161b22"
BG3 = "#21262d"
BORDER = "#30363d"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
CYAN = "#58a6ff"
WHITE = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#1f6feb"


def get_normal_tickers():
    url = "https://api.upbit.com/v1/market/all?is_details=true"
    res = requests.get(url).json()
    tickers = []
    names = {}
    for m in res:
        if not m["market"].startswith("KRW-"):
            continue
        if m.get("market_event", {}).get("warning", False):
            continue
        tickers.append(m["market"])
        names[m["market"]] = m["korean_name"]
    return tickers, names


class AutoTrader:
    def __init__(self, upbit, log_fn, update_fn):
        self.upbit = upbit
        self.add_log = log_fn
        self.update_ui = update_fn
        self.positions = {}
        self.daily_profit = 0.0
        self.daily_trades = 0
        self.initial_balance = 0.0
        self.start_time = None
        self.running = False
        self.tickers = []
        self.names = {}
        self.price_history = defaultdict(list)
        self.cooldowns = {}
        self.loss_limit_logged = False

    def reset_daily(self):
        self.daily_profit = 0.0
        self.daily_trades = 0
        total = self._total_balance()
        self.initial_balance = total if total > 0 else self.initial_balance
        self.start_time = datetime.now()
        self.loss_limit_logged = False

    def _total_balance(self):
        try:
            krw = float(self.upbit.get_balance("KRW") or 0)
            balances = self.upbit.get_balances()
            total = krw
            for b in balances:
                if b["currency"] == "KRW":
                    continue
                ticker = f"KRW-{b['currency']}"
                amt = float(b.get("balance", 0))
                avg = float(b.get("avg_buy_price", 0))
                price = pyupbit.get_current_price(ticker) or avg
                total += amt * price
            return total
        except Exception:
            return 0

    def refresh_tickers(self):
        self.tickers, self.names = get_normal_tickers()
        self.add_log(f"[SCAN] {len(self.tickers)}개 종목 로드 완료", "info")

    def scan_momentum(self):
        try:
            prices = pyupbit.get_current_price(self.tickers)
            if not prices:
                return []

            signals = []
            for ticker in self.tickers:
                price = prices.get(ticker, 0)
                if not price:
                    continue

                self.price_history[ticker].append({"time": time.time(), "price": price})
                cutoff = time.time() - SCAN_WINDOW_SECONDS
                self.price_history[ticker] = [p for p in self.price_history[ticker] if p["time"] > cutoff]

                history = self.price_history[ticker]
                if len(history) < MIN_SCAN_POINTS:
                    continue

                oldest = history[0]["price"]
                change_pct = (price - oldest) / oldest * 100

                recent = history[-5:] if len(history) >= 5 else history
                avg_old = sum(p["price"] for p in recent[:len(recent)//2]) / max(len(recent)//2, 1)
                avg_new = sum(p["price"] for p in recent[len(recent)//2:]) / max(len(recent) - len(recent)//2, 1)
                accel = (avg_new - avg_old) / avg_old * 100 if avg_old > 0 else 0

                if MIN_MOMENTUM_PCT <= change_pct <= MAX_MOMENTUM_PCT and accel >= MIN_ACCEL_PCT:
                    signals.append({
                        "ticker": ticker,
                        "price": price,
                        "change": change_pct,
                        "accel": accel,
                        "score": change_pct * 0.6 + accel * 0.4,
                    })

            signals.sort(key=lambda x: x["score"], reverse=True)
            return signals[:10]
        except Exception as e:
            self.add_log(f"[ERR] 스캔 오류: {e}", "sell")
            return []

    def check_positions(self):
        to_sell = []
        for ticker, pos in list(self.positions.items()):
            try:
                price = pyupbit.get_current_price(ticker)
                if not price:
                    continue
                pnl = (price - pos["buy_price"]) / pos["buy_price"] * 100
                pos["highest_price"] = max(pos.get("highest_price", pos["buy_price"]), price)
                trail = (pos["highest_price"] - price) / pos["highest_price"] * 100

                if pnl >= TAKE_PROFIT_PCT and trail >= TRAILING_STOP_PCT:
                    to_sell.append((ticker, "TRAIL", pnl, price))
                elif pnl <= STOP_LOSS_PCT:
                    to_sell.append((ticker, "STOP", pnl, price))
                elif time.time() - pos["time"] > 1800 and pnl < 0.2:
                    to_sell.append((ticker, "TIMEOUT", pnl, price))
            except Exception:
                continue

        for ticker, reason, pnl, price in to_sell:
            self._sell(ticker, reason, pnl)

    def _buy(self, ticker, price):
        if ticker in self.positions:
            return
        if len(self.positions) >= MAX_POSITIONS:
            return

        pct = self.daily_profit / self.initial_balance * 100 if self.initial_balance > 0 else 0
        if pct >= DAILY_TARGET_PCT or pct <= -MAX_DAILY_LOSS_PCT:
            return

        last_trade_at = self.cooldowns.get(ticker, 0)
        if time.time() - last_trade_at < COOLDOWN_SECONDS:
            return

        try:
            krw = float(self.upbit.get_balance("KRW") or 0)
            total = self._total_balance()
            amount_per_slot = total * TRADE_RATIO / MAX_POSITIONS
            amount = min(krw * KRW_BALANCE_BUFFER, amount_per_slot)
            if amount < MIN_TRADE_AMOUNT_KRW:
                return

            result = self.upbit.buy_market_order(ticker, amount)
            if result and "error" not in result:
                coin = ticker.replace("KRW-", "")
                name = self.names.get(ticker, coin)
                self.positions[ticker] = {
                    "buy_price": price,
                    "highest_price": price,
                    "amount_krw": amount,
                    "time": time.time(),
                }
                self.daily_trades += 1
                self.add_log(f"[BUY] {name}({coin}) {price:,.0f}원 / {amount:,.0f}원", "buy")
        except Exception as e:
            self.add_log(f"[ERR] 매수 실패: {e}", "sell")

    def _sell(self, ticker, reason, pnl):
        try:
            balance = float(self.upbit.get_balance(ticker) or 0)
            if balance <= 0:
                del self.positions[ticker]
                return

            price = pyupbit.get_current_price(ticker)
            value = balance * price if price else 0
            if value < MIN_TRADE_AMOUNT_KRW:
                del self.positions[ticker]
                return

            result = self.upbit.sell_market_order(ticker, balance)
            if result and "error" not in result:
                coin = ticker.replace("KRW-", "")
                name = self.names.get(ticker, coin)
                profit_krw = self.positions[ticker]["amount_krw"] * pnl / 100
                self.daily_profit += profit_krw
                tag = "buy" if pnl > 0 else "sell"
                self.add_log(f"[SELL:{reason}] {name}({coin}) {pnl:+.2f}% ({profit_krw:+,.0f}원)", tag)
                self.cooldowns[ticker] = time.time()
                self.positions.pop(ticker, None)
        except Exception as e:
            self.add_log(f"[ERR] 매도 실패: {e}", "sell")

    def tick(self):
        self.check_positions()
        pct = self.daily_profit / self.initial_balance * 100 if self.initial_balance > 0 else 0
        if pct <= -MAX_DAILY_LOSS_PCT:
            if not self.loss_limit_logged:
                self.add_log(f"[RISK] Daily loss limit reached ({pct:+.2f}%). New entries paused.", "sell")
                self.loss_limit_logged = True
            self.update_ui()
            return

        signals = self.scan_momentum()
        for sig in signals:
            if sig["ticker"] not in self.positions:
                self._buy(sig["ticker"], sig["price"])
                break
        self.update_ui()

    def get_position_list(self):
        result = []
        for ticker, pos in self.positions.items():
            try:
                price = pyupbit.get_current_price(ticker)
                pnl = (price - pos["buy_price"]) / pos["buy_price"] * 100 if price else 0
                coin = ticker.replace("KRW-", "")
                result.append({
                    "coin": coin,
                    "name": self.names.get(ticker, coin),
                    "buy_price": pos["buy_price"],
                    "cur_price": price or 0,
                    "pnl": pnl,
                    "amount": pos["amount_krw"],
                })
            except Exception:
                continue
        return result


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AUTO TRADER")
        self.root.state("zoomed")
        self.root.configure(bg=BG)

        self.upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)
        self.trader = AutoTrader(self.upbit, self.add_log, self.refresh_dashboard)
        self.running = False

        self._build_ui()
        self._init_data()

    # ─── UI ────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, bg=BG2, pady=8)
        top.pack(fill=tk.X)

        tk.Label(top, text="  AUTO TRADER", font=("Consolas", 18, "bold"),
                 bg=BG2, fg=CYAN).pack(side=tk.LEFT, padx=10)

        self.status_dot = tk.Label(top, text="  STANDBY  ", font=("Consolas", 10, "bold"),
                                    bg=DIM, fg=BG)
        self.status_dot.pack(side=tk.LEFT, padx=10)

        right_info = tk.Frame(top, bg=BG2)
        right_info.pack(side=tk.RIGHT, padx=15)
        self.clock_label = tk.Label(right_info, text="", font=("Consolas", 10), bg=BG2, fg=DIM)
        self.clock_label.pack()

        # Stats row
        stats = tk.Frame(self.root, bg=BG, pady=6)
        stats.pack(fill=tk.X, padx=15)
        for i in range(6):
            stats.columnconfigure(i, weight=1)

        self.stat_balance = self._stat_card(stats, "KRW Balance", "0", 0)
        self.stat_total = self._stat_card(stats, "Total Assets", "0", 1)
        self.stat_profit = self._stat_card(stats, "Daily P&L", "0", 2)
        self.stat_pct = self._stat_card(stats, "Daily %", "0.00%", 3)
        self.stat_trades = self._stat_card(stats, "Trades Today", "0", 4)
        self.stat_target = self._stat_card(stats, "Target", f"{DAILY_TARGET_PCT}%", 5)

        # Buttons
        btn_row = tk.Frame(self.root, bg=BG, pady=4)
        btn_row.pack(fill=tk.X, padx=15)

        self.btn_start = tk.Button(btn_row, text="  START  ", font=("Consolas", 12, "bold"),
                                   bg=GREEN, fg=BG, activebackground="#2ea043", relief=tk.FLAT,
                                   padx=25, pady=6, cursor="hand2", command=self.start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = tk.Button(btn_row, text="  STOP  ", font=("Consolas", 12, "bold"),
                                  bg=RED, fg=WHITE, activebackground="#da3633", relief=tk.FLAT,
                                  padx=25, pady=6, cursor="hand2", command=self.stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))

        self.timer_label = tk.Label(btn_row, text="", font=("Consolas", 10), bg=BG, fg=DIM)
        self.timer_label.pack(side=tk.LEFT, padx=15)

        tk.Label(btn_row, text=f"TP: +{TAKE_PROFIT_PCT}% trail {TRAILING_STOP_PCT}%  SL: {STOP_LOSS_PCT}%  Max loss: -{MAX_DAILY_LOSS_PCT}%  Positions: {MAX_POSITIONS}",
                 font=("Consolas", 9), bg=BG, fg=DIM).pack(side=tk.RIGHT)

        # Divider
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill=tk.X, padx=15, pady=4)

        # Main content
        content = tk.Frame(self.root, bg=BG)
        content.pack(fill=tk.BOTH, expand=True, padx=15, pady=(4, 10))
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # Left: Positions
        left = tk.Frame(content, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        tk.Label(left, text="POSITIONS", font=("Consolas", 11, "bold"),
                 bg=BG, fg=CYAN, anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))

        pos_header = tk.Frame(left, bg=BG3)
        pos_header.grid(row=0, column=0, sticky="ew", pady=(20, 0))
        cols = [("Coin", 10), ("Name", 12), ("Buy Price", 14), ("Cur Price", 14), ("P&L", 10), ("Amount", 12)]
        for i, (name, w) in enumerate(cols):
            pos_header.columnconfigure(i, weight=1)
            tk.Label(pos_header, text=name, font=("Consolas", 9, "bold"), bg=BG3, fg=DIM,
                     width=w, anchor="w", padx=8, pady=4).grid(row=0, column=i, sticky="ew")

        self.pos_frame = tk.Frame(left, bg=BG2)
        self.pos_frame.grid(row=1, column=0, sticky="nsew")
        self.pos_frame.columnconfigure(0, weight=1)

        self.pos_empty = tk.Label(self.pos_frame, text="\n\n  No open positions\n\n",
                                   font=("Consolas", 11), bg=BG2, fg=DIM)
        self.pos_empty.pack(fill=tk.BOTH, expand=True)

        # Right: Log
        right = tk.Frame(content, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(right, text="TRADE LOG", font=("Consolas", 11, "bold"),
                 bg=BG, fg=CYAN, anchor="w").pack(fill=tk.X, pady=(0, 4))

        self.log_text = tk.Text(right, bg=BG2, fg=WHITE, font=("Consolas", 9),
                                relief=tk.FLAT, wrap=tk.WORD, state=tk.DISABLED,
                                borderwidth=0, padx=10, pady=8, insertbackground=WHITE)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("buy", foreground=GREEN)
        self.log_text.tag_configure("sell", foreground=RED)
        self.log_text.tag_configure("hold", foreground=YELLOW)
        self.log_text.tag_configure("info", foreground=DIM)

        self._update_clock()

    def _stat_card(self, parent, title, default, col):
        frame = tk.Frame(parent, bg=BG2, padx=12, pady=8, highlightbackground=BORDER,
                         highlightthickness=1)
        frame.grid(row=0, column=col, sticky="nsew", padx=3)
        tk.Label(frame, text=title, font=("Consolas", 8), bg=BG2, fg=DIM, anchor="w").pack(fill=tk.X)
        val = tk.Label(frame, text=default, font=("Consolas", 14, "bold"), bg=BG2, fg=WHITE, anchor="w")
        val.pack(fill=tk.X)
        return val

    def _update_clock(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.clock_label.configure(text=now)
        self.root.after(1000, self._update_clock)

    # ─── Log ───────────────────────────────────────────────
    def add_log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ─── Dashboard ─────────────────────────────────────────
    def refresh_dashboard(self):
        try:
            krw = float(self.upbit.get_balance("KRW") or 0)
            self.root.after(0, self.stat_balance.configure, {"text": f"{krw:,.0f}"})

            total = krw
            balances = self.upbit.get_balances()
            for b in balances:
                if b["currency"] == "KRW":
                    continue
                amt = float(b.get("balance", 0))
                ticker = f"KRW-{b['currency']}"
                price = pyupbit.get_current_price(ticker) or float(b.get("avg_buy_price", 0))
                total += amt * price

            self.root.after(0, self.stat_total.configure, {"text": f"{total:,.0f}"})

            dp = self.trader.daily_profit
            color = GREEN if dp >= 0 else RED
            self.root.after(0, self.stat_profit.configure, {"text": f"{dp:+,.0f}", "fg": color})

            pct = dp / self.trader.initial_balance * 100 if self.trader.initial_balance > 0 else 0
            self.root.after(0, self.stat_pct.configure, {"text": f"{pct:+.2f}%", "fg": color})
            self.root.after(0, self.stat_trades.configure, {"text": str(self.trader.daily_trades)})

            self._update_positions()
        except Exception:
            pass

    def _update_positions(self):
        positions = self.trader.get_position_list()

        for w in self.pos_frame.winfo_children():
            w.destroy()

        if not positions:
            self.pos_empty = tk.Label(self.pos_frame, text="\n\n  No open positions\n\n",
                                       font=("Consolas", 11), bg=BG2, fg=DIM)
            self.pos_empty.pack(fill=tk.BOTH, expand=True)
            return

        for i, p in enumerate(positions):
            bg = BG2 if i % 2 == 0 else BG3
            row = tk.Frame(self.pos_frame, bg=bg)
            row.pack(fill=tk.X)

            pnl_color = GREEN if p["pnl"] >= 0 else RED
            vals = [
                (p["coin"], WHITE, 10),
                (p["name"], DIM, 12),
                (f"{p['buy_price']:,.0f}", WHITE, 14),
                (f"{p['cur_price']:,.0f}", WHITE, 14),
                (f"{p['pnl']:+.2f}%", pnl_color, 10),
                (f"{p['amount']:,.0f}", DIM, 12),
            ]
            for text, fg, w in vals:
                tk.Label(row, text=text, font=("Consolas", 10), bg=bg, fg=fg,
                         width=w, anchor="w", padx=8, pady=5).pack(side=tk.LEFT, fill=tk.X, expand=True)

    # ─── Init ──────────────────────────────────────────────
    def _init_data(self):
        def _load():
            self.add_log("[SYS] Connecting to Upbit API...", "info")
            self.trader.refresh_tickers()
            self.trader.reset_daily()
            self.refresh_dashboard()
            self.add_log("[SYS] Ready. Press START to begin.", "info")
        threading.Thread(target=_load, daemon=True).start()

    # ─── Trading Loop ──────────────────────────────────────
    def start(self):
        self.running = True
        self.trader.running = True
        self.trader.reset_daily()
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.status_dot.configure(text="  RUNNING  ", bg=GREEN, fg=BG)
        self.add_log("[SYS] Auto trading started!", "buy")
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.trader.running = False
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self.status_dot.configure(text="  STOPPED  ", bg=RED, fg=WHITE)
        self.add_log("[SYS] Auto trading stopped.", "sell")

    def _loop(self):
        refresh_counter = 0
        while self.running:
            try:
                if refresh_counter % 30 == 0:
                    self.trader.refresh_tickers()
                refresh_counter += 1

                self.trader.tick()

                for sec in range(CHECK_INTERVAL, 0, -1):
                    if not self.running:
                        break
                    self.root.after(0, self.timer_label.configure, {"text": f"Next scan: {sec}s"})
                    time.sleep(1)
            except Exception as e:
                self.add_log(f"[ERR] {e}", "sell")
                time.sleep(5)

        self.root.after(0, self.timer_label.configure, {"text": ""})

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self.trader.running = False
        self.root.destroy()


if __name__ == "__main__":
    App().run()
