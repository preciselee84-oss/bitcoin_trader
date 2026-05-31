"""
백테스트 모듈 - 실제 매매 전에 과거 데이터로 전략을 검증합니다.
실행: python backtest.py
"""
import pyupbit
import pandas as pd
from config import TICKER, SHORT_MA_PERIOD, LONG_MA_PERIOD


def backtest(days: int = 200, initial_krw: float = 1_000_000):
    print(f"\n{'='*60}")
    print(f"  백테스트 시작")
    print(f"  기간: 최근 {days}일 | 초기 자금: {initial_krw:,.0f}원")
    print(f"  전략: MA{SHORT_MA_PERIOD} x MA{LONG_MA_PERIOD} 크로스")
    print(f"{'='*60}\n")

    df = pyupbit.get_ohlcv(TICKER, interval="day", count=days)
    if df is None or df.empty:
        print("데이터를 가져올 수 없습니다.")
        return

    df[f"ma_{SHORT_MA_PERIOD}"] = df["close"].rolling(window=SHORT_MA_PERIOD).mean()
    df[f"ma_{LONG_MA_PERIOD}"] = df["close"].rolling(window=LONG_MA_PERIOD).mean()
    df = df.dropna()

    short_col = f"ma_{SHORT_MA_PERIOD}"
    long_col = f"ma_{LONG_MA_PERIOD}"

    krw = initial_krw
    btc = 0.0
    trades = []

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        date = df.index[i]
        price = curr["close"]

        if prev[short_col] <= prev[long_col] and curr[short_col] > curr[long_col]:
            if krw > 0:
                btc = krw / price
                trades.append(
                    {"date": date, "type": "매수", "price": price, "amount_krw": krw}
                )
                krw = 0

        elif prev[short_col] >= prev[long_col] and curr[short_col] < curr[long_col]:
            if btc > 0:
                sell_krw = btc * price
                trades.append(
                    {"date": date, "type": "매도", "price": price, "amount_krw": sell_krw}
                )
                krw = sell_krw
                btc = 0

    final_price = df.iloc[-1]["close"]
    total_value = krw + btc * final_price
    profit_rate = (total_value - initial_krw) / initial_krw * 100

    buy_hold_value = initial_krw / df.iloc[0]["close"] * final_price
    buy_hold_rate = (buy_hold_value - initial_krw) / initial_krw * 100

    print(f"📋 거래 내역 ({len(trades)}건):")
    print("-" * 60)
    for t in trades:
        print(f"  {t['date'].strftime('%Y-%m-%d')} | {t['type']} | "
              f"가격: {t['price']:,.0f}원 | 금액: {t['amount_krw']:,.0f}원")

    print(f"\n{'='*60}")
    print(f"  📊 백테스트 결과")
    print(f"{'='*60}")
    print(f"  초기 자금:      {initial_krw:,.0f}원")
    print(f"  최종 자산:      {total_value:,.0f}원")
    print(f"  수익률:         {profit_rate:+.2f}%")
    print(f"  총 거래 횟수:   {len(trades)}회")
    print(f"{'='*60}")
    print(f"  📈 바이앤홀드:  {buy_hold_value:,.0f}원 ({buy_hold_rate:+.2f}%)")
    print(f"  전략 vs 홀드:   {profit_rate - buy_hold_rate:+.2f}%p")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    backtest()
