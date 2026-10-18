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

SYMBOL = "DOGEUSDT"
TIMEFRAME = "1"
LEVERAGE = 5
CHECK_INTERVAL = 10  # секунд

# ===== ПОДКЛЮЧЕНИЕ =====
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
)


# ===== РАСЧЁТ ОБЪЁМА =====
def get_qty() -> float:
    # Получаем шаг лота для символа
    info = session.get_instruments_info(
        category="linear",
        symbol=SYMBOL
    )
    qty_step = float(info['result']['list'][0]['lotSizeFilter']['qtyStep'])

    current_price = float(session.get_tickers(
        category="linear",
        symbol=SYMBOL
    )['result']['list'][0]['lastPrice'])

    balance = session.get_wallet_balance(accountType="UNIFIED")
    usdt = float(balance['result']['list'][0]['totalAvailableBalance'])

    print(f"Баланс: {usdt} USDT | Цена: {current_price} | Шаг лота: {qty_step}")

    qty = (usdt * LEVERAGE) / current_price

    # Округляем вниз по шагу лота
    qty = (qty // qty_step) * qty_step

    decimals = len(str(qty_step).rstrip('0').split('.')[-1]) if '.' in str(qty_step) else 0
    return round(qty, decimals)


# ===== ПОЛУЧЕНИЕ СВЕЧЕЙ =====
def get_klines():
    response = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=200
    )

    df = pd.DataFrame(response['result']['list'])
    df = df.iloc[::-1].reset_index(drop=True)
    df.columns = [
        "timestamp", "open", "high",
        "low", "close", "volume", "turnover"
    ]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')

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


def get_position_size() -> float:
    """Возвращает реальный размер открытой позиции с биржи"""
    positions = session.get_positions(
        category="linear",
        symbol=SYMBOL
    )
    return float(positions['result']['list'][0]['size'])


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
def check_short_entry(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        prev['macd'] > prev['macd_signal'] and
        last['macd'] <= last['macd_signal']
    )


def check_short_exit(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        prev['macd'] < prev['macd_signal'] and
        last['macd'] >= last['macd_signal']
    )


# ===== ОРДЕРА =====
def open_short():
    try:
        qty = get_qty()
        print(f"Открываю SHORT | Объём: {qty} DOGE")
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Sell",
            orderType="Market",
            qty=str(qty),
            timeInForce="ImmediateOrCancel"
        )
    except Exception as e:
        print("Ошибка открытия:", e)


def close_position():
    try:
        qty = get_position_size()  # берём реальный размер с биржи
        print(f"Закрываю SHORT | Объём: {qty} DOGE")
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side="Buy",
            orderType="Market",
            qty=str(qty),
            reduceOnly=True,
            timeInForce="ImmediateOrCancel"
        )
    except Exception as e:
        print("Ошибка закрытия:", e)


# ===== ОСНОВНОЙ ЦИКЛ =====
print("STA-MACD SHORT бот запущен...")

while True:
    try:
        df = get_klines()
        df = calculate_indicators(df)

        print(
            "MACD:", df['macd'].iloc[-1],
            "Signal:", df['macd_signal'].iloc[-1],
            "Есть позиция:", has_position()
        )

        if not has_position() and check_short_entry(df):
            set_leverage()
            open_short()

        elif has_position() and check_short_exit(df):
            close_position()

        time.sleep(CHECK_INTERVAL)

    except Exception as e:
        print("Ошибка цикла:", e)
        time.sleep(5)