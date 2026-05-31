import logging
from typing import Any, Dict, Optional

import pandas as pd
import pyupbit

from config import (
    ATR_PERIOD,
    CANDLE_COUNT,
    CANDLE_INTERVAL,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    EMA_TREND_PERIOD,
    MAX_ATR_PCT,
    MIN_ATR_PCT,
    MIN_MOMENTUM_PCT,
    MIN_VOLUME_RATIO,
    MOMENTUM_LOOKBACK,
    RSI_BUY_MAX,
    RSI_BUY_MIN,
    RSI_PERIOD,
    RSI_SELL_MAX,
    TICKER,
    VOLUME_MA_PERIOD,
)

logger = logging.getLogger(__name__)


def get_candles(
    ticker: str = TICKER,
    interval: str = CANDLE_INTERVAL,
    count: int = CANDLE_COUNT,
) -> Optional[pd.DataFrame]:
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        if df is None or df.empty:
            logger.error("Could not load candle data.")
            return None

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.error("Candle data is missing required OHLCV columns.")
            return None

        return df
    except Exception as exc:
        logger.error("Failed to load candle data: %s", exc)
        return None


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    close = data["close"]

    data["ema_fast"] = close.ewm(span=EMA_FAST_PERIOD, adjust=False).mean()
    data["ema_slow"] = close.ewm(span=EMA_SLOW_PERIOD, adjust=False).mean()
    data["ema_trend"] = close.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=RSI_PERIOD).mean()
    avg_loss = loss.rolling(window=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0)
    data["rsi"] = 100 - (100 / (1 + rs))
    data.loc[(avg_loss == 0) & (avg_gain > 0), "rsi"] = 100
    data.loc[(avg_loss == 0) & (avg_gain == 0), "rsi"] = 50

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = true_range.rolling(window=ATR_PERIOD).mean()
    data["atr_pct"] = data["atr"] / close * 100

    volume_ma = data["volume"].rolling(window=VOLUME_MA_PERIOD).mean()
    data["volume_ratio"] = data["volume"] / volume_ma.where(volume_ma != 0)
    data["momentum_pct"] = close.pct_change(periods=MOMENTUM_LOOKBACK) * 100
    return data


def _metrics(curr: pd.Series) -> Dict[str, float]:
    return {
        "price": float(curr["close"]),
        "ema_fast": float(curr["ema_fast"]),
        "ema_slow": float(curr["ema_slow"]),
        "ema_trend": float(curr["ema_trend"]),
        "rsi": float(curr["rsi"]),
        "atr_pct": float(curr["atr_pct"]),
        "volume_ratio": float(curr["volume_ratio"]),
        "momentum_pct": float(curr["momentum_pct"]),
    }


def _decision(signal: str, reason: str, curr: pd.Series) -> Dict[str, Any]:
    return {"signal": signal, "reason": reason, "metrics": _metrics(curr)}


def evaluate_rows(prev: pd.Series, curr: pd.Series) -> Dict[str, Any]:
    trend_up = curr["close"] > curr["ema_trend"] and curr["ema_fast"] > curr["ema_slow"]
    cross_up = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    momentum_ok = curr["momentum_pct"] >= MIN_MOMENTUM_PCT
    rsi_buy_ok = RSI_BUY_MIN <= curr["rsi"] <= RSI_BUY_MAX
    volume_ok = curr["volume_ratio"] >= MIN_VOLUME_RATIO
    volatility_ok = MIN_ATR_PCT <= curr["atr_pct"] <= MAX_ATR_PCT

    if trend_up and rsi_buy_ok and volume_ok and volatility_ok and (cross_up or momentum_ok):
        return _decision("buy", "trend, momentum, RSI, volume, and volatility are aligned", curr)

    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
    trend_break = curr["close"] < curr["ema_slow"] and curr["momentum_pct"] < 0
    overheated_rollover = curr["rsi"] >= RSI_SELL_MAX and curr["close"] < prev["close"]

    if cross_down:
        return _decision("sell", "fast EMA crossed below slow EMA", curr)
    if trend_break:
        return _decision("sell", "price broke below slow EMA with negative momentum", curr)
    if overheated_rollover:
        return _decision("sell", "RSI is overheated and price rolled over", curr)

    return _decision("hold", "no confirmed edge", curr)


def evaluate_signal(df: pd.DataFrame) -> Dict[str, Any]:
    data = calculate_indicators(df).dropna()
    if len(data) < 2:
        return {
            "signal": "hold",
            "reason": "not enough data after indicator warmup",
            "metrics": {},
        }
    return evaluate_rows(data.iloc[-2], data.iloc[-1])


def get_signal(ticker: str = TICKER, return_details: bool = False) -> Any:
    df = get_candles(ticker=ticker)
    if df is None:
        decision = {"signal": "hold", "reason": "candle load failed", "metrics": {}}
    else:
        decision = evaluate_signal(df)

    metrics = decision.get("metrics", {})
    if metrics:
        logger.info(
            "price=%0.0f ema%d=%0.0f ema%d=%0.0f rsi=%0.1f atr=%0.2f%% vol=%0.2fx mom=%0.2f%%",
            metrics["price"],
            EMA_FAST_PERIOD,
            metrics["ema_fast"],
            EMA_SLOW_PERIOD,
            metrics["ema_slow"],
            metrics["rsi"],
            metrics["atr_pct"],
            metrics["volume_ratio"],
            metrics["momentum_pct"],
        )
    logger.info("signal=%s reason=%s", decision["signal"], decision["reason"])
    return decision if return_details else decision["signal"]
