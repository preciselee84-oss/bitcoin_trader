import io
import sys
from typing import Dict, List

import pandas as pd
import pyupbit

from config import (
    ATR_PERIOD,
    CANDLE_INTERVAL,
    FEE_RATE,
    MAX_ATR_PCT,
    MIN_ATR_PCT,
    MIN_MOMENTUM_PCT,
    MIN_TRADE_AMOUNT_KRW,
    MIN_VOLUME_RATIO,
    MOMENTUM_LOOKBACK,
    RSI_BUY_MAX,
    RSI_BUY_MIN,
    RSI_PERIOD,
    RSI_SELL_MAX,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TICKER,
    TRAILING_STOP_PCT,
    VOLUME_MA_PERIOD,
)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COUNT = 200


def prepare_indicators(df: pd.DataFrame, fast: int, slow: int, trend: int) -> pd.DataFrame:
    data = df.copy()
    close = data["close"]
    data["ema_fast"] = close.ewm(span=fast, adjust=False).mean()
    data["ema_slow"] = close.ewm(span=slow, adjust=False).mean()
    data["ema_trend"] = close.ewm(span=trend, adjust=False).mean()

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
    return data.dropna()


def row_signal(prev, curr) -> str:
    trend_up = curr["close"] > curr["ema_trend"] and curr["ema_fast"] > curr["ema_slow"]
    cross_up = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    momentum_ok = curr["momentum_pct"] >= MIN_MOMENTUM_PCT
    rsi_buy_ok = RSI_BUY_MIN <= curr["rsi"] <= RSI_BUY_MAX
    volume_ok = curr["volume_ratio"] >= MIN_VOLUME_RATIO
    volatility_ok = MIN_ATR_PCT <= curr["atr_pct"] <= MAX_ATR_PCT
    if trend_up and rsi_buy_ok and volume_ok and volatility_ok and (cross_up or momentum_ok):
        return "buy"

    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
    trend_break = curr["close"] < curr["ema_slow"] and curr["momentum_pct"] < 0
    overheated_rollover = curr["rsi"] >= RSI_SELL_MAX and curr["close"] < prev["close"]
    if cross_down or trend_break or overheated_rollover:
        return "sell"
    return "hold"


def simulate(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    trend: int,
    trade_ratio: float,
    initial_krw: float = 1_000_000,
) -> Dict[str, float]:
    data = prepare_indicators(df, fast, slow, trend)
    if len(data) < 2:
        return {}

    krw = initial_krw
    coin = 0.0
    entry = 0.0
    high = 0.0
    max_equity = initial_krw
    max_drawdown = 0.0
    closed = 0
    wins = 0

    for i in range(1, len(data)):
        prev = data.iloc[i - 1]
        curr = data.iloc[i]
        price = float(curr["close"])
        sold = False

        if coin > 0:
            high = max(high, price)
            pnl = (price - entry) / entry * 100 if entry else 0.0
            trail = (high - price) / high * 100 if high else 0.0
            if pnl <= -STOP_LOSS_PCT or (pnl >= TAKE_PROFIT_PCT and trail >= TRAILING_STOP_PCT):
                krw += coin * price * (1 - FEE_RATE)
                wins += 1 if pnl > 0 else 0
                closed += 1
                coin = 0.0
                entry = 0.0
                high = 0.0
                sold = True

        signal = row_signal(prev, curr)
        if signal == "buy" and coin <= 0 and not sold:
            amount = krw * trade_ratio
            if amount >= MIN_TRADE_AMOUNT_KRW:
                coin = amount * (1 - FEE_RATE) / price
                krw -= amount
                entry = price
                high = price
        elif signal == "sell" and coin > 0:
            pnl = (price - entry) / entry * 100 if entry else 0.0
            krw += coin * price * (1 - FEE_RATE)
            wins += 1 if pnl > 0 else 0
            closed += 1
            coin = 0.0
            entry = 0.0
            high = 0.0

        equity = krw + coin * price
        max_equity = max(max_equity, equity)
        drawdown = (max_equity - equity) / max_equity * 100 if max_equity else 0.0
        max_drawdown = max(max_drawdown, drawdown)

    final_price = float(data.iloc[-1]["close"])
    final = krw + coin * final_price
    profit = (final - initial_krw) / initial_krw * 100
    win_rate = wins / closed * 100 if closed else 0.0
    score = profit - (max_drawdown * 0.6)
    return {
        "fast": fast,
        "slow": slow,
        "trend": trend,
        "ratio": trade_ratio,
        "profit": profit,
        "mdd": max_drawdown,
        "closed": closed,
        "win_rate": win_rate,
        "score": score,
    }


def main() -> None:
    print("=" * 86)
    print(f"Parameter scan: {TICKER} {CANDLE_INTERVAL} candles={COUNT}")
    print("Score = return - 0.6 * max_drawdown. Treat this as a filter, not a guarantee.")
    print("=" * 86)

    df = pyupbit.get_ohlcv(TICKER, interval=CANDLE_INTERVAL, count=COUNT)
    if df is None or df.empty:
        print("Could not load candle data.")
        return

    fast_periods = [5, 8, 10, 12]
    slow_periods = [18, 21, 26, 34]
    trend_periods = [50, 55, 89]
    trade_ratios = [0.15, 0.25, 0.35]

    results: List[Dict[str, float]] = []
    for fast in fast_periods:
        for slow in slow_periods:
            if fast >= slow:
                continue
            for trend in trend_periods:
                if slow >= trend:
                    continue
                for ratio in trade_ratios:
                    result = simulate(df, fast, slow, trend, ratio)
                    if result:
                        results.append(result)

    results.sort(key=lambda item: item["score"], reverse=True)

    print(f"{'rank':>4} | {'fast':>4} | {'slow':>4} | {'trend':>5} | {'ratio':>5} | {'return':>9} | {'mdd':>7} | {'trades':>6} | {'win':>6} | {'score':>8}")
    print("-" * 86)
    for idx, row in enumerate(results[:20], 1):
        print(
            f"{idx:>4} | "
            f"{row['fast']:>4.0f} | "
            f"{row['slow']:>4.0f} | "
            f"{row['trend']:>5.0f} | "
            f"{row['ratio']:>4.0%} | "
            f"{row['profit']:>+8.2f}% | "
            f"{row['mdd']:>6.2f}% | "
            f"{row['closed']:>6.0f} | "
            f"{row['win_rate']:>5.1f}% | "
            f"{row['score']:>+7.2f}"
        )
    print("=" * 86)


if __name__ == "__main__":
    main()
