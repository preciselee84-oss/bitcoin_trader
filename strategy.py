import pyupbit
import pandas as pd
import logging

from config import TICKER, SHORT_MA_PERIOD, LONG_MA_PERIOD, CANDLE_INTERVAL, CANDLE_COUNT

logger = logging.getLogger(__name__)


def get_candles() -> pd.DataFrame | None:
    try:
        if CANDLE_INTERVAL == "day":
            df = pyupbit.get_ohlcv(TICKER, interval="day", count=CANDLE_COUNT)
        elif CANDLE_INTERVAL == "minute60":
            df = pyupbit.get_ohlcv(TICKER, interval="minute60", count=CANDLE_COUNT)
        else:
            df = pyupbit.get_ohlcv(TICKER, interval=CANDLE_INTERVAL, count=CANDLE_COUNT)

        if df is None or df.empty:
            logger.error("캔들 데이터를 가져올 수 없습니다.")
            return None
        return df
    except Exception as e:
        logger.error(f"캔들 데이터 조회 실패: {e}")
        return None


def calculate_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df[f"ma_{SHORT_MA_PERIOD}"] = df["close"].rolling(window=SHORT_MA_PERIOD).mean()
    df[f"ma_{LONG_MA_PERIOD}"] = df["close"].rolling(window=LONG_MA_PERIOD).mean()
    return df


def get_signal() -> str:
    df = get_candles()
    if df is None:
        return "hold"

    df = calculate_moving_averages(df)
    df = df.dropna()

    if len(df) < 2:
        logger.warning("이동평균선 계산에 필요한 데이터가 부족합니다.")
        return "hold"

    short_col = f"ma_{SHORT_MA_PERIOD}"
    long_col = f"ma_{LONG_MA_PERIOD}"

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_short = prev[short_col]
    prev_long = prev[long_col]
    curr_short = curr[short_col]
    curr_long = curr[long_col]

    current_price = curr["close"]
    logger.info(
        f"현재가: {current_price:,.0f}원 | "
        f"단기MA({SHORT_MA_PERIOD}): {curr_short:,.0f}원 | "
        f"장기MA({LONG_MA_PERIOD}): {curr_long:,.0f}원"
    )

    if prev_short <= prev_long and curr_short > curr_long:
        logger.info("🔵 골든크로스 감지 → 매수 신호")
        return "buy"

    if prev_short >= prev_long and curr_short < curr_long:
        logger.info("🔴 데드크로스 감지 → 매도 신호")
        return "sell"

    logger.info("⚪ 신호 없음 → 홀드")
    return "hold"
