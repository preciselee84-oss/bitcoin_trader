import json
import logging
from pathlib import Path
from typing import Any, Dict

import pyupbit

from config import (
    KRW_BALANCE_BUFFER,
    MIN_TRADE_AMOUNT_KRW,
    STATE_FILE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TICKER,
    TRADE_RATIO,
    TRAILING_STOP_PCT,
    UPBIT_ACCESS_KEY,
    UPBIT_SECRET_KEY,
)

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self, ticker: str = TICKER, upbit=None, state_file: str | Path | None = None):
        if not UPBIT_ACCESS_KEY or not UPBIT_SECRET_KEY:
            raise ValueError("API keys are missing. Check your .env file.")

        self.ticker = ticker
        self.upbit = upbit or pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)
        self.state_path = Path(state_file or STATE_FILE)
        self.state = self._load_state()
        logger.info("Connected to Upbit API for %s", self.ticker)

    @property
    def coin_symbol(self) -> str:
        return self.ticker.split("-")[-1]

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"positions": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
            if "positions" not in state:
                state["positions"] = {}
            return state
        except Exception as exc:
            logger.warning("Could not read state file, starting fresh: %s", exc)
            return {"positions": {}}

    def _save_state(self) -> None:
        try:
            with self.state_path.open("w", encoding="utf-8") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Could not save state file: %s", exc)

    def _position_state(self) -> Dict[str, Any]:
        return self.state.setdefault("positions", {}).setdefault(self.ticker, {})

    def _clear_position_state(self) -> None:
        self.state.setdefault("positions", {}).pop(self.ticker, None)
        self._save_state()

    def get_krw_balance(self) -> float:
        try:
            balance = self.upbit.get_balance("KRW")
            return float(balance) if balance else 0.0
        except Exception as exc:
            logger.error("Failed to load KRW balance: %s", exc)
            return 0.0

    def get_current_price(self) -> float:
        try:
            price = pyupbit.get_current_price(self.ticker)
            return float(price) if price else 0.0
        except Exception as exc:
            logger.error("Failed to load current price: %s", exc)
            return 0.0

    def get_position(self) -> Dict[str, float]:
        try:
            balances = self.upbit.get_balances() or []
            current_price = self.get_current_price()
            for balance in balances:
                if balance.get("currency") != self.coin_symbol:
                    continue

                available = float(balance.get("balance") or 0.0)
                locked = float(balance.get("locked") or 0.0)
                avg_buy_price = float(balance.get("avg_buy_price") or 0.0)
                total_amount = available + locked
                mark_price = current_price or avg_buy_price
                return {
                    "available": available,
                    "locked": locked,
                    "amount": total_amount,
                    "avg_buy_price": avg_buy_price,
                    "current_price": current_price,
                    "value": total_amount * mark_price,
                }
        except Exception as exc:
            logger.error("Failed to load position: %s", exc)

        return {
            "available": 0.0,
            "locked": 0.0,
            "amount": 0.0,
            "avg_buy_price": 0.0,
            "current_price": 0.0,
            "value": 0.0,
        }

    def has_position(self) -> bool:
        return self.get_position()["value"] >= MIN_TRADE_AMOUNT_KRW

    def get_risk_signal(self) -> str:
        position = self.get_position()
        if position["value"] < MIN_TRADE_AMOUNT_KRW:
            self._clear_position_state()
            return "hold"

        current_price = position["current_price"]
        entry_price = position["avg_buy_price"]
        if current_price <= 0 or entry_price <= 0:
            return "hold"

        pos_state = self._position_state()
        highest_price = max(
            float(pos_state.get("highest_price") or entry_price),
            current_price,
        )
        pos_state["entry_price"] = entry_price
        pos_state["highest_price"] = highest_price
        self._save_state()

        pnl_pct = (current_price - entry_price) / entry_price * 100
        trail_from_high_pct = (highest_price - current_price) / highest_price * 100

        logger.info(
            "position pnl=%+.2f%% high_drawdown=%0.2f%% entry=%0.0f high=%0.0f",
            pnl_pct,
            trail_from_high_pct,
            entry_price,
            highest_price,
        )

        if pnl_pct <= -STOP_LOSS_PCT:
            logger.warning("Risk exit: stop loss hit (%+.2f%%)", pnl_pct)
            return "sell"

        if pnl_pct >= TAKE_PROFIT_PCT and trail_from_high_pct >= TRAILING_STOP_PCT:
            logger.info(
                "Risk exit: trailing take profit hit (pnl=%+.2f%% drawdown=%0.2f%%)",
                pnl_pct,
                trail_from_high_pct,
            )
            return "sell"

        return "hold"

    def buy(self, max_krw_amount: float | None = None, allow_existing: bool = False) -> bool:
        if self.has_position() and not allow_existing:
            logger.info("Buy skipped: existing %s position is already open", self.ticker)
            return False

        krw_balance = self.get_krw_balance()
        trade_amount = min(krw_balance * TRADE_RATIO, krw_balance * KRW_BALANCE_BUFFER)
        if max_krw_amount is not None:
            trade_amount = min(trade_amount, max_krw_amount, krw_balance * KRW_BALANCE_BUFFER)

        if trade_amount < MIN_TRADE_AMOUNT_KRW:
            logger.warning(
                "Buy skipped: trade amount %0.0f KRW is below minimum %0.0f KRW",
                trade_amount,
                MIN_TRADE_AMOUNT_KRW,
            )
            return False

        try:
            result = self.upbit.buy_market_order(self.ticker, trade_amount)
            if result and "error" not in result:
                current_price = self.get_current_price()
                pos_state = self._position_state()
                pos_state["entry_price"] = current_price
                pos_state["highest_price"] = current_price
                self._save_state()
                logger.info("Buy order placed: %s %0.0f KRW", self.ticker, trade_amount)
                self.record_buy(trade_amount)
                return True

            error_msg = (result or {}).get("error", {}).get("message", "unknown error")
            logger.error("Buy order failed: %s", error_msg)
            return False
        except Exception as exc:
            logger.error("Buy order raised an exception: %s", exc)
            return False

    def record_buy(self, amount_krw: float) -> None:
        ps = self._position_state()
        ps["total_invested"] = ps.get("total_invested", 0) + amount_krw
        self._save_state()

    def record_sell(self, amount_krw: float) -> None:
        ps = self._position_state()
        ps["total_recovered"] = ps.get("total_recovered", 0) + amount_krw
        self._save_state()

    def get_investment_state(self) -> Dict[str, Any]:
        ps = self._position_state()
        return {
            "total_invested": ps.get("total_invested", 0),
            "total_recovered": ps.get("total_recovered", 0),
            "principal_recovered": ps.get("principal_recovered", False),
            "fixed_target": ps.get("fixed_target", 0),
        }

    def set_fixed_target(self, amount_krw: float) -> None:
        ps = self._position_state()
        if not ps.get("fixed_target"):
            ps["fixed_target"] = amount_krw
            self._save_state()

    def mark_principal_recovered(self) -> None:
        ps = self._position_state()
        ps["principal_recovered"] = True
        self._save_state()

    def sell_partial_krw(self, amount_krw: float) -> bool:
        position = self.get_position()
        current_price = position["current_price"]
        if current_price <= 0:
            return False

        sell_amount = amount_krw / current_price
        available = position["available"]
        sell_amount = min(sell_amount, available)
        estimated_krw = sell_amount * current_price

        if estimated_krw < MIN_TRADE_AMOUNT_KRW:
            logger.info("%s partial sell skipped: %0.0f KRW below minimum", self.ticker, estimated_krw)
            return False

        try:
            result = self.upbit.sell_market_order(self.ticker, sell_amount)
            if result and "error" not in result:
                logger.info("Partial sell: %s %.8f (approx %0.0f KRW)", self.ticker, sell_amount, estimated_krw)
                self.record_sell(estimated_krw)
                return True
            error_msg = (result or {}).get("error", {}).get("message", "unknown error")
            logger.error("Partial sell failed: %s", error_msg)
            return False
        except Exception as exc:
            logger.error("Partial sell exception: %s", exc)
            return False

    def sell(self) -> bool:
        position = self.get_position()
        available = position["available"]
        current_price = position["current_price"]
        estimated_krw = available * current_price

        if estimated_krw < MIN_TRADE_AMOUNT_KRW:
            logger.warning(
                "Sell skipped: available value %0.0f KRW is below minimum %0.0f KRW",
                estimated_krw,
                MIN_TRADE_AMOUNT_KRW,
            )
            self._clear_position_state()
            return False

        try:
            result = self.upbit.sell_market_order(self.ticker, available)
            if result and "error" not in result:
                logger.info("Sell order placed: %s %.8f", self.ticker, available)
                self.record_sell(estimated_krw)
                self._clear_position_state()
                return True

            error_msg = (result or {}).get("error", {}).get("message", "unknown error")
            logger.error("Sell order failed: %s", error_msg)
            return False
        except Exception as exc:
            logger.error("Sell order raised an exception: %s", exc)
            return False

    def print_status(self) -> None:
        krw = self.get_krw_balance()
        position = self.get_position()
        total = krw + position["value"]

        logger.info("=" * 56)
        logger.info("KRW balance: %0.0f", krw)
        logger.info(
            "%s holding: %.8f available + %.8f locked (%0.0f KRW)",
            self.coin_symbol,
            position["available"],
            position["locked"],
            position["value"],
        )
        logger.info("Total marked assets: %0.0f KRW", total)
        logger.info("Current %s price: %0.0f KRW", self.coin_symbol, position["current_price"])
        logger.info("=" * 56)
