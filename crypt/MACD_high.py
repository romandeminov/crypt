import os
from dotenv import load_dotenv
import pandas as pd
import time
from pybit.unified_trading import HTTP
from ta.trend import MACD

load_dotenv()

# ===== НАСТРОЙКИ =====
API_KEY = os.getenv("BYBIT_KEY")
API_SECRET = os.getenv("BYBIT_SECRET")

SYMBOL = "HUSDT"
TIMEFRAME = "60"
QTY = 4340
LEVERAGE = 10
CHECK_INTERVAL = 300  # секунд

# ===== ПОДКЛЮЧЕНИЕ =====
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ===== ПОЛУЧЕНИЕ СВЕЧЕЙ =====
def get_klines():
    response = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=200
    )

    df = pd.DataFrame(response['result']['list'])
    df = df.astype(float)
    df = df.iloc[::-1]

    df.columns = [
        "timestamp", "open", "high",
        "low", "close", "volume", "turnover"
    ]

    return df


# ===== ИНДИКАТОРЫ =====
def calculate_indicators(df):
    macd = MACD(close=df['close'])

    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()

    return df


# ===== ПРОВЕРКА ПОЗИЦИИ =====
def has_position():
    positions = session.get_positions(
        category="linear",
        symbol=SYMBOL
    )

    if not positions['result']['list']:
        return False

    size = float(positions['result']['list'][0]['size'])
    return size > 0


# ===== УСТАНОВКА ПЛЕЧА =====
def set_leverage():
    try:
        session.set_leverage(
            category="linear",
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
        print(f"Плечо установлено: {LEVERAGE}x")
    except Exception as e:
        print("Ошибка установки плеча:", e)


# ===== СИГНАЛЫ =====
def check_long_entry(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    d_prev = df.iloc[-3]

    # MACD пересёк сигнальную снизу вверх
    return (
        d_prev['macd'] < d_prev['macd_signal'] and prev['macd'] >= prev['macd_signal'] and
        last['macd'] >= last['macd_signal']
    )


def check_long_exit(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # MACD пересёк сигнальную сверху вниз
    return (
        prev['macd'] > prev['macd_signal'] and
        last['macd'] <= last['macd_signal']
    )


# ===== ОРДЕРА =====
def open_long():
    try:
        print("Открываю LONG")
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Buy",
            orderType="Market",
            qty=QTY,
            timeInForce="ImmediateOrCancel"
        )
    except Exception as e:
        print("Ошибка открытия:", e)


def close_position():
    try:
        print("Закрываю LONG")
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Sell",
            orderType="Market",
            qty=QTY,
            reduceOnly=True,
            timeInForce="ImmediateOrCancel"
        )
    except Exception as e:
        print("Ошибка закрытия:", e)


# ===== ОСНОВНОЙ ЦИКЛ =====
print("STA-MACD LONG бот запущен...")

while True:
    try:
        df = get_klines()
        df = calculate_indicators(df)

        print(
            "MACD:", df['macd'].iloc[-1],
            "Signal:", df['macd_signal'].iloc[-1],
            "Есть позиция:", has_position()
        )

        if not has_position() and check_long_entry(df):
            set_leverage()
            open_long()

        elif has_position() and check_long_exit(df):
            close_position()

        time.sleep(CHECK_INTERVAL)

    except Exception as e:
        print("Ошибка цикла:", e)
        time.sleep(5)