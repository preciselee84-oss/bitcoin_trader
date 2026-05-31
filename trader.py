import pyupbit
import logging

from config import (
    UPBIT_ACCESS_KEY,
    UPBIT_SECRET_KEY,
    TICKER,
    TRADE_RATIO,
    MIN_TRADE_AMOUNT_KRW,
)

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self):
        if not UPBIT_ACCESS_KEY or not UPBIT_SECRET_KEY:
            raise ValueError(
                "API 키가 설정되지 않았습니다. .env 파일을 확인해주세요."
            )
        self.upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)
        logger.info("업비트 API 연결 완료")

    def get_krw_balance(self) -> float:
        try:
            balance = self.upbit.get_balance("KRW")
            return float(balance) if balance else 0.0
        except Exception as e:
            logger.error(f"KRW 잔고 조회 실패: {e}")
            return 0.0

    def get_btc_balance(self) -> float:
        try:
            balance = self.upbit.get_balance(TICKER)
            return float(balance) if balance else 0.0
        except Exception as e:
            logger.error(f"BTC 잔고 조회 실패: {e}")
            return 0.0

    def get_current_price(self) -> float:
        try:
            price = pyupbit.get_current_price(TICKER)
            return float(price) if price else 0.0
        except Exception as e:
            logger.error(f"현재가 조회 실패: {e}")
            return 0.0

    def buy(self) -> bool:
        krw_balance = self.get_krw_balance()
        trade_amount = krw_balance * TRADE_RATIO

        if trade_amount < MIN_TRADE_AMOUNT_KRW:
            logger.warning(
                f"매수 가능 금액 부족: {trade_amount:,.0f}원 "
                f"(최소 {MIN_TRADE_AMOUNT_KRW:,.0f}원)"
            )
            return False

        try:
            result = self.upbit.buy_market_order(TICKER, trade_amount)
            if result and "error" not in result:
                logger.info(
                    f"✅ 매수 주문 성공: {trade_amount:,.0f}원 어치 BTC 매수"
                )
                return True
            else:
                error_msg = result.get("error", {}).get("message", "알 수 없는 오류")
                logger.error(f"매수 주문 실패: {error_msg}")
                return False
        except Exception as e:
            logger.error(f"매수 주문 중 예외 발생: {e}")
            return False

    def sell(self) -> bool:
        btc_balance = self.get_btc_balance()
        current_price = self.get_current_price()
        estimated_krw = btc_balance * current_price

        if estimated_krw < MIN_TRADE_AMOUNT_KRW:
            logger.warning(
                f"매도 가능 금액 부족: {estimated_krw:,.0f}원 "
                f"(최소 {MIN_TRADE_AMOUNT_KRW:,.0f}원)"
            )
            return False

        try:
            result = self.upbit.sell_market_order(TICKER, btc_balance)
            if result and "error" not in result:
                logger.info(
                    f"✅ 매도 주문 성공: {btc_balance:.8f} BTC 전량 매도"
                )
                return True
            else:
                error_msg = result.get("error", {}).get("message", "알 수 없는 오류")
                logger.error(f"매도 주문 실패: {error_msg}")
                return False
        except Exception as e:
            logger.error(f"매도 주문 중 예외 발생: {e}")
            return False

    def print_status(self):
        krw = self.get_krw_balance()
        btc = self.get_btc_balance()
        price = self.get_current_price()
        btc_value = btc * price
        total = krw + btc_value

        logger.info("=" * 50)
        logger.info(f"💰 KRW 잔고: {krw:,.0f}원")
        logger.info(f"₿  BTC 보유: {btc:.8f} BTC ({btc_value:,.0f}원)")
        logger.info(f"📊 총 자산: {total:,.0f}원")
        logger.info(f"📈 BTC 현재가: {price:,.0f}원")
        logger.info("=" * 50)
