import io
import logging
import sys
import time
from datetime import datetime

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trading.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_once(trader: Trader) -> None:
    logger.info("--- trading check started: %s ---", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    trader.print_status()

    if trader.get_risk_signal() == "sell":
        trader.sell()
        logger.info("--- next check in %s seconds ---\n", CHECK_INTERVAL_SECONDS)
        return

    decision = get_signal(return_details=True)
    signal = decision["signal"]

    if signal == "buy":
        trader.buy()
    elif signal == "sell":
        trader.sell()
    else:
        logger.info("No order placed.")

    logger.info("--- next check in %s seconds ---\n", CHECK_INTERVAL_SECONDS)


def main() -> None:
    logger.info("=" * 60)
    logger.info("Bitcoin auto trader started")
    logger.info("ticker=%s interval=%s", TICKER, CANDLE_INTERVAL)
    logger.info(
        "strategy=EMA%d/EMA%d trend EMA%d with RSI, ATR, volume, and trailing risk exits",
        EMA_FAST_PERIOD,
        EMA_SLOW_PERIOD,
        EMA_TREND_PERIOD,
    )
    logger.info("check_interval=%s seconds", CHECK_INTERVAL_SECONDS)
    logger.info("=" * 60)

    try:
        trader = Trader()
    except ValueError as exc:
        logger.error("Startup failed: %s", exc)
        sys.exit(1)

    try:
        while True:
            try:
                run_once(trader)
            except Exception as exc:
                logger.error("Trading loop error: %s", exc)
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
        trader.print_status()


if __name__ == "__main__":
    main()
