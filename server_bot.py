import io
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from config import (
    CHECK_INTERVAL_SECONDS,
    CANDLE_INTERVAL,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    EMA_TREND_PERIOD,
    TICKER,
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


def place_order(trader: Trader, signal_name: str, dry_run: bool) -> None:
    if signal_name == "buy":
        if dry_run:
            logger.info("DRY_RUN: buy signal received, order skipped")
        else:
            trader.buy()
    elif signal_name == "sell":
        if dry_run:
            logger.info("DRY_RUN: sell signal received, order skipped")
        else:
            trader.sell()
    else:
        logger.info("No order placed.")


def run_once(trader: Trader, dry_run: bool) -> None:
    logger.info("--- server bot check started: %s ---", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    trader.print_status()

    risk_signal = trader.get_risk_signal()
    if risk_signal == "sell":
        logger.warning("Risk signal requested an exit.")
        place_order(trader, "sell", dry_run)
        return

    decision = get_signal(return_details=True)
    place_order(trader, decision["signal"], dry_run)


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

    dry_run = _env_flag("DRY_RUN", False)

    logger.info("=" * 64)
    logger.info("Cloud server bot started")
    logger.info("ticker=%s interval=%s dry_run=%s", TICKER, CANDLE_INTERVAL, dry_run)
    logger.info(
        "strategy=EMA%d/EMA%d trend EMA%d with RSI, ATR, volume, and risk exits",
        EMA_FAST_PERIOD,
        EMA_SLOW_PERIOD,
        EMA_TREND_PERIOD,
    )
    logger.info("check_interval=%s seconds", CHECK_INTERVAL_SECONDS)
    logger.info("=" * 64)

    try:
        trader = Trader()
    except ValueError as exc:
        logger.error("Startup failed: %s", exc)
        sys.exit(1)

    while not shutdown_requested:
        started_at = time.monotonic()
        try:
            run_once(trader, dry_run=dry_run)
        except Exception:
            logger.exception("Server bot loop failed")
        sleep_until_next_check(started_at)

    logger.info("Cloud server bot stopped.")


if __name__ == "__main__":
    main()
