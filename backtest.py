import io
import sys
from typing import Dict, List

import pyupbit

from config import (
    CANDLE_COUNT,
    CANDLE_INTERVAL,
    FEE_RATE,
    MIN_TRADE_AMOUNT_KRW,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TICKER,
    TRADE_RATIO,
    TRAILING_STOP_PCT,
)
from strategy import calculate_indicators, evaluate_rows

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _sell_value(amount_coin: float, price: float) -> float:
    return amount_coin * price * (1 - FEE_RATE)


def run_backtest(df, initial_krw: float = 1_000_000) -> Dict[str, object]:
    data = calculate_indicators(df).dropna()
    if len(data) < 2:
        raise ValueError("Not enough data after indicator warmup.")

    krw = initial_krw
    coin = 0.0
    entry_price = 0.0
    highest_price = 0.0
    max_equity = initial_krw
    max_drawdown = 0.0
    trades: List[Dict[str, object]] = []
    closed_pnls: List[float] = []

    for i in range(1, len(data)):
        prev = data.iloc[i - 1]
        curr = data.iloc[i]
        date = data.index[i]
        price = float(curr["close"])
        sold_this_bar = False

        if coin > 0:
            highest_price = max(highest_price, price)
            pnl_pct = (price - entry_price) / entry_price * 100 if entry_price else 0.0
            trail_from_high = (highest_price - price) / highest_price * 100 if highest_price else 0.0

            exit_reason = None
            if pnl_pct <= -STOP_LOSS_PCT:
                exit_reason = "stop"
            elif pnl_pct >= TAKE_PROFIT_PCT and trail_from_high >= TRAILING_STOP_PCT:
                exit_reason = "trail"

            if exit_reason:
                krw += _sell_value(coin, price)
                trades.append(
                    {
                        "date": date,
                        "type": "sell",
                        "reason": exit_reason,
                        "price": price,
                        "pnl_pct": pnl_pct,
                    }
                )
                closed_pnls.append(pnl_pct)
                coin = 0.0
                entry_price = 0.0
                highest_price = 0.0
                sold_this_bar = True

        decision = evaluate_rows(prev, curr)
        signal = decision["signal"]

        if signal == "buy" and coin <= 0 and not sold_this_bar:
            trade_amount = krw * TRADE_RATIO
            if trade_amount >= MIN_TRADE_AMOUNT_KRW:
                coin = trade_amount * (1 - FEE_RATE) / price
                krw -= trade_amount
                entry_price = price
                highest_price = price
                trades.append(
                    {
                        "date": date,
                        "type": "buy",
                        "reason": decision["reason"],
                        "price": price,
                        "amount_krw": trade_amount,
                    }
                )
        elif signal == "sell" and coin > 0:
            pnl_pct = (price - entry_price) / entry_price * 100 if entry_price else 0.0
            krw += _sell_value(coin, price)
            trades.append(
                {
                    "date": date,
                    "type": "sell",
                    "reason": decision["reason"],
                    "price": price,
                    "pnl_pct": pnl_pct,
                }
            )
            closed_pnls.append(pnl_pct)
            coin = 0.0
            entry_price = 0.0
            highest_price = 0.0

        equity = krw + coin * price
        max_equity = max(max_equity, equity)
        drawdown = (max_equity - equity) / max_equity * 100 if max_equity else 0.0
        max_drawdown = max(max_drawdown, drawdown)

    final_price = float(data.iloc[-1]["close"])
    final_value = krw + coin * final_price
    profit_rate = (final_value - initial_krw) / initial_krw * 100

    first_price = float(data.iloc[0]["close"])
    buy_hold_coin = initial_krw * (1 - FEE_RATE) / first_price
    buy_hold_value = buy_hold_coin * final_price * (1 - FEE_RATE)
    buy_hold_rate = (buy_hold_value - initial_krw) / initial_krw * 100

    wins = sum(1 for pnl in closed_pnls if pnl > 0)
    win_rate = wins / len(closed_pnls) * 100 if closed_pnls else 0.0

    return {
        "initial": initial_krw,
        "final": final_value,
        "profit_rate": profit_rate,
        "buy_hold_value": buy_hold_value,
        "buy_hold_rate": buy_hold_rate,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "closed_trades": len(closed_pnls),
        "win_rate": win_rate,
    }


def backtest(count: int = CANDLE_COUNT, initial_krw: float = 1_000_000) -> None:
    print("=" * 72)
    print("Backtest")
    print(f"ticker={TICKER} interval={CANDLE_INTERVAL} candles={count}")
    print(f"initial={initial_krw:,.0f} KRW fee={FEE_RATE * 100:.3f}% trade_ratio={TRADE_RATIO:.0%}")
    print("=" * 72)

    df = pyupbit.get_ohlcv(TICKER, interval=CANDLE_INTERVAL, count=count)
    if df is None or df.empty:
        print("Could not load candle data.")
        return

    result = run_backtest(df, initial_krw=initial_krw)
    trades = result["trades"]

    print(f"\nTrades ({len(trades)} orders)")
    print("-" * 72)
    for trade in trades[-30:]:
        if trade["type"] == "buy":
            print(
                f"{trade['date'].strftime('%Y-%m-%d %H:%M')} | BUY  | "
                f"{trade['price']:,.0f} | {trade['amount_krw']:,.0f} KRW"
            )
        else:
            print(
                f"{trade['date'].strftime('%Y-%m-%d %H:%M')} | SELL | "
                f"{trade['price']:,.0f} | {trade['pnl_pct']:+.2f}% | {trade['reason']}"
            )

    print("\n" + "=" * 72)
    print("Result")
    print("=" * 72)
    print(f"Initial assets:       {result['initial']:,.0f} KRW")
    print(f"Final assets:         {result['final']:,.0f} KRW")
    print(f"Strategy return:      {result['profit_rate']:+.2f}%")
    print(f"Buy and hold return:  {result['buy_hold_rate']:+.2f}%")
    print(f"Strategy vs hold:     {result['profit_rate'] - result['buy_hold_rate']:+.2f}%p")
    print(f"Max drawdown:         {result['max_drawdown']:.2f}%")
    print(f"Closed trades:        {result['closed_trades']}")
    print(f"Win rate:             {result['win_rate']:.1f}%")
    print("=" * 72)


if __name__ == "__main__":
    backtest()
