import os
from dotenv import load_dotenv

load_dotenv()

UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

TICKER = os.getenv("TICKER", "KRW-BTC")
TICKERS = {
    "KRW-BTC": {"name": "비트코인", "short_name": "BTC", "color": "#f7931a"},
    "KRW-ETH": {"name": "이더리움", "short_name": "ETH", "color": "#627eea"},
    "KRW-SOL": {"name": "솔라나", "short_name": "SOL", "color": "#00ffa3"},
}

# Strategy timeframe. minute60 is less noisy than short scalping and updates often enough.
CANDLE_INTERVAL = os.getenv("CANDLE_INTERVAL", "minute60")
CANDLE_COUNT = int(os.getenv("CANDLE_COUNT", "200"))

EMA_FAST_PERIOD = int(os.getenv("EMA_FAST_PERIOD", "8"))
EMA_SLOW_PERIOD = int(os.getenv("EMA_SLOW_PERIOD", "21"))
EMA_TREND_PERIOD = int(os.getenv("EMA_TREND_PERIOD", "55"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
VOLUME_MA_PERIOD = int(os.getenv("VOLUME_MA_PERIOD", "20"))
MOMENTUM_LOOKBACK = int(os.getenv("MOMENTUM_LOOKBACK", "3"))

RSI_BUY_MIN = float(os.getenv("RSI_BUY_MIN", "45"))
RSI_BUY_MAX = float(os.getenv("RSI_BUY_MAX", "68"))
RSI_SELL_MAX = float(os.getenv("RSI_SELL_MAX", "78"))
MIN_VOLUME_RATIO = float(os.getenv("MIN_VOLUME_RATIO", "0.8"))
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.15"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "6.0"))
MIN_MOMENTUM_PCT = float(os.getenv("MIN_MOMENTUM_PCT", "0.15"))

# Backward-compatible aliases used by older scripts.
SHORT_MA_PERIOD = EMA_FAST_PERIOD
LONG_MA_PERIOD = EMA_SLOW_PERIOD

# Risk controls. Keep this conservative until a backtest shows otherwise.
TRADE_RATIO = float(os.getenv("TRADE_RATIO", "0.25"))
KRW_BALANCE_BUFFER = float(os.getenv("KRW_BALANCE_BUFFER", "0.995"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0005"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "1.8"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))

MIN_TRADE_AMOUNT_KRW = float(os.getenv("MIN_TRADE_AMOUNT_KRW", "5000"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
STATE_FILE = os.getenv("STATE_FILE", "trade_state.json")
