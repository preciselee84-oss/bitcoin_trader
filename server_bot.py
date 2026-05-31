import io
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
from typing import Dict

from config import (
    CHECK_INTERVAL_SECONDS,
    CANDLE_INTERVAL,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    EMA_TREND_PERIOD,
    KRW_BALANCE_BUFFER,
    MIN_TRADE_AMOUNT_KRW,
    PORTFOLIO_REBALANCE_GAP_PCT,
    PORTFOLIO_TICKERS,
    PORTFOLIO_WEIGHTS,
    STATE_FILE,
    TRADE_RATIO,
)
from strategy import get_signal
from trader import Trader

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logger = logging.getLogger(__name__)
shutdown_requested = False


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def setup_logging() -> None:
    log_path = Path(os.getenv("LOG_FILE", "trading.log"))
    if not log_path.is_absolute():
        log_path = Path(__file__).resolve().parent / log_path

    logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def request_shutdown(signum, _frame) -> None:
    global shutdown_requested
    shutdown_requested = True
    logger.info("Shutdown requested by signal %s", signum)


def state_file_for(ticker: str) -> Path:
    base = Path(STATE_FILE)
    safe_ticker = ticker.replace("-", "_")
    suffix = base.suffix or ".json"
    return base.with_name(f"{base.stem}.{safe_ticker}{suffix}")


def build_traders() -> Dict[str, Trader]:
    traders: Dict[str, Trader] = {}
    shared_upbit = None
    for ticker in PORTFOLIO_TICKERS:
        trader = Trader(ticker=ticker, upbit=shared_upbit, state_file=state_file_for(ticker))
        shared_upbit = trader.upbit
        traders[ticker] = trader
    return traders


def get_portfolio_snapshot(traders: Dict[str, Trader]) -> Dict[str, object]:
    first_trader = next(iter(traders.values()))
    krw = first_trader.get_krw_balance()
    positions = {ticker: trader.get_position() for ticker, trader in traders.items()}
    invested = sum(position["value"] for position in positions.values())
    total = krw + invested
    return {"krw": krw, "invested": invested, "total": total, "positions": positions}


def log_portfolio_snapshot(snapshot: Dict[str, object], weights: Dict[str, float]) -> None:
    total = float(snapshot["total"])
    logger.info("portfolio total=%0.0f KRW cash=%0.0f invested=%0.0f", total, snapshot["krw"], snapshot["invested"])
    positions = snapshot["positions"]

    for ticker in PORTFOLIO_TICKERS:
        position = positions[ticker]
        current_weight = position["value"] / total if total > 0 else 0.0
        logger.info(
            "%s value=%0.0f KRW weight=%0.1f%% target=%0.1f%% price=%0.0f",
            ticker,
            position["value"],
            current_weight * 100,
            weights[ticker] * 100,
            position["current_price"],
        )


def place_sell(trader: Trader, dry_run: bool, reason: str) -> bool:
    position = trader.get_position()
    if position["value"] < MIN_TRADE_AMOUNT_KRW:
        logger.info("%s sell skipped: no meaningful position", trader.ticker)
        return False

    if dry_run:
        logger.info("DRY_RUN: %s sell skipped (%s)", trader.ticker, reason)
        return False

    return trader.sell()


def place_buy(trader: Trader, amount_krw: float, dry_run: bool, reason: str) -> bool:
    if amount_krw < MIN_TRADE_AMOUNT_KRW:
        logger.info(
            "%s buy skipped: amount %0.0f KRW below minimum %0.0f KRW",
            trader.ticker,
            amount_krw,
            MIN_TRADE_AMOUNT_KRW,
        )
        return False

    if dry_run:
        logger.info("DRY_RUN: %s buy %0.0f KRW skipped (%s)", trader.ticker, amount_krw, reason)
        return False

    return trader.buy(max_krw_amount=amount_krw, allow_existing=True)


def init_fixed_targets(traders: Dict[str, Trader], weights: Dict[str, float], total: float) -> None:
    for ticker, trader in traders.items():
        target = total * weights[ticker]
        trader.set_fixed_target(target)
        logger.info("%s fixed target set: %0.0f KRW", ticker, target)


def check_principal_recovery(traders: Dict[str, Trader], dry_run: bool) -> None:
    for ticker, trader in traders.items():
        inv = trader.get_investment_state()
        if inv["principal_recovered"] or inv["total_invested"] <= 0:
            continue

        position = trader.get_position()
        current_value = position["value"]
        net_invested = inv["total_invested"] - inv["total_recovered"]

        if net_invested <= 0:
            continue

        profit = current_value - net_invested
        if profit >= net_invested:
            logger.info(
                "%s PRINCIPAL RECOVERY: value=%0.0f invested=%0.0f profit=%0.0f -> recovering %0.0f KRW",
                ticker, current_value, net_invested, profit, net_invested,
            )
            if dry_run:
                logger.info("DRY_RUN: %s principal recovery skipped", ticker)
            else:
                if trader.sell_partial_krw(net_invested):
                    trader.mark_principal_recovered()
                    logger.info("%s principal recovered! Now trading with profits only", ticker)


def check_fixed_rebalance(traders: Dict[str, Trader], snapshot: Dict[str, object], dry_run: bool) -> None:
    cash_available = float(snapshot["krw"])
    positions = snapshot["positions"]

    for ticker, trader in traders.items():
        inv = trader.get_investment_state()
        fixed_target = inv["fixed_target"]
        if fixed_target <= 0:
            continue

        current_value = positions[ticker]["value"]
        diff = current_value - fixed_target
        gap_pct = abs(diff) / fixed_target if fixed_target > 0 else 0

        if gap_pct < PORTFOLIO_REBALANCE_GAP_PCT:
            continue

        if diff > MIN_TRADE_AMOUNT_KRW:
            excess = diff
            logger.info("%s FIXED REBALANCE SELL: value=%0.0f target=%0.0f excess=%0.0f", ticker, current_value, fixed_target, excess)
            if dry_run:
                logger.info("DRY_RUN: %s rebalance sell skipped", ticker)
            else:
                trader.sell_partial_krw(excess)

        elif diff < -MIN_TRADE_AMOUNT_KRW and cash_available > MIN_TRADE_AMOUNT_KRW:
            shortfall = min(abs(diff), cash_available * KRW_BALANCE_BUFFER)
            logger.info("%s FIXED REBALANCE BUY: value=%0.0f target=%0.0f shortfall=%0.0f", ticker, current_value, fixed_target, shortfall)
            if dry_run:
                logger.info("DRY_RUN: %s rebalance buy skipped", ticker)
            else:
                if trader.buy(max_krw_amount=shortfall, allow_existing=True):
                    cash_available -= shortfall


def run_once(traders: Dict[str, Trader], weights: Dict[str, float], dry_run: bool) -> None:
    logger.info("--- portfolio bot check started: %s ---", datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))

    snapshot = get_portfolio_snapshot(traders)
    log_portfolio_snapshot(snapshot, weights)

    init_fixed_targets(traders, weights, float(snapshot["total"]))

    check_principal_recovery(traders, dry_run=dry_run)

    decisions = {}
    blocked_from_buy = set()
    sold = False

    for ticker, trader in traders.items():
        risk_signal = trader.get_risk_signal()
        if risk_signal == "sell":
            logger.warning("%s risk exit requested", ticker)
            blocked_from_buy.add(ticker)
            sold = place_sell(trader, dry_run=dry_run, reason="risk") or sold
            continue

        decisions[ticker] = get_signal(ticker=ticker, return_details=True)

    for ticker, decision in decisions.items():
        if decision["signal"] != "sell":
            continue
        blocked_from_buy.add(ticker)
        sold = place_sell(traders[ticker], dry_run=dry_run, reason=decision["reason"]) or sold

    if sold:
        time.sleep(1)
        snapshot = get_portfolio_snapshot(traders)
        log_portfolio_snapshot(snapshot, weights)

    total = float(snapshot["total"])
    cash_available = float(snapshot["krw"])
    positions = snapshot["positions"]

    for ticker, decision in decisions.items():
        if ticker in blocked_from_buy or decision["signal"] != "buy":
            continue

        inv = traders[ticker].get_investment_state()
        target_value = inv["fixed_target"] if inv["fixed_target"] > 0 else total * weights[ticker]
        current_value = positions[ticker]["value"]
        value_gap = target_value - current_value
        min_gap = max(MIN_TRADE_AMOUNT_KRW, target_value * PORTFOLIO_REBALANCE_GAP_PCT)

        if value_gap < min_gap:
            logger.info(
                "%s buy skipped: current value is close to target (gap=%0.0f KRW target=%0.0f KRW)",
                ticker,
                value_gap,
                target_value,
            )
            continue

        max_step = target_value * TRADE_RATIO
        buy_amount = min(value_gap, max_step, cash_available * KRW_BALANCE_BUFFER)
        if place_buy(traders[ticker], buy_amount, dry_run=dry_run, reason=decision["reason"]):
            cash_available -= buy_amount
        elif dry_run:
            cash_available -= min(buy_amount, cash_available)

    check_fixed_rebalance(traders, snapshot, dry_run=dry_run)


def sleep_until_next_check(started_at: float) -> None:
    elapsed = time.monotonic() - started_at
    remaining = max(1, CHECK_INTERVAL_SECONDS - int(elapsed))
    logger.info("--- next check in %s seconds ---\n", remaining)

    for _ in range(remaining):
        if shutdown_requested:
            return
        time.sleep(1)


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    dry_run = _env_flag("DRY_RUN", True)
    weights = dict(zip(PORTFOLIO_TICKERS, PORTFOLIO_WEIGHTS))

    logger.info("=" * 72)
    logger.info("Cloud portfolio bot started")
    logger.info("tickers=%s interval=%s dry_run=%s", ",".join(PORTFOLIO_TICKERS), CANDLE_INTERVAL, dry_run)
    logger.info("weights=%s", ", ".join(f"{ticker}:{weights[ticker] * 100:.0f}%" for ticker in PORTFOLIO_TICKERS))
    logger.info(
        "strategy=EMA%d/EMA%d trend EMA%d with RSI, ATR, volume, and risk exits",
        EMA_FAST_PERIOD,
        EMA_SLOW_PERIOD,
        EMA_TREND_PERIOD,
    )
    logger.info("trade_step=%0.1f%% of target check_interval=%s seconds", TRADE_RATIO * 100, CHECK_INTERVAL_SECONDS)
    logger.info("=" * 72)

    try:
        traders = build_traders()
    except ValueError as exc:
        logger.error("Startup failed: %s", exc)
        sys.exit(1)

    while not shutdown_requested:
        started_at = time.monotonic()
        try:
            run_once(traders, weights=weights, dry_run=dry_run)
        except Exception:
            logger.exception("Server bot loop failed")
        sleep_until_next_check(started_at)

    logger.info("Cloud portfolio bot stopped.")


if __name__ == "__main__":
    main()
