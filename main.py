import time
import logging
import sys
from datetime import datetime

from config import CHECK_INTERVAL_SECONDS, TICKER, SHORT_MA_PERIOD, LONG_MA_PERIOD
from strategy import get_signal
from trader import Trader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trading.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_once(trader: Trader):
    logger.info(f"--- 매매 체크 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    trader.print_status()

    signal = get_signal()

    if signal == "buy":
        trader.buy()
    elif signal == "sell":
        trader.sell()

    logger.info(f"--- 다음 체크까지 {CHECK_INTERVAL_SECONDS}초 대기 ---\n")


def main():
    logger.info("=" * 60)
    logger.info("  비트코인 자동매매 프로그램 시작")
    logger.info(f"  거래 대상: {TICKER}")
    logger.info(f"  전략: 이동평균선 크로스 (MA{SHORT_MA_PERIOD} x MA{LONG_MA_PERIOD})")
    logger.info(f"  체크 간격: {CHECK_INTERVAL_SECONDS}초")
    logger.info("=" * 60)

    try:
        trader = Trader()
    except ValueError as e:
        logger.error(f"초기화 실패: {e}")
        sys.exit(1)

    logger.info("자동매매를 시작합니다. 종료하려면 Ctrl+C를 누르세요.\n")

    try:
        while True:
            try:
                run_once(trader)
            except Exception as e:
                logger.error(f"매매 루프 중 오류 발생: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("\n프로그램이 사용자에 의해 종료되었습니다.")
        trader.print_status()


if __name__ == "__main__":
    main()
