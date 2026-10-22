import os
import time
import math
from decimal import Decimal, ROUND_DOWN

import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from ta.trend import MACD

load_dotenv()

# ===== НАСТРОЙКИ =====
API_KEY    = os.getenv("BYBIT_KEY")
API_SECRET = os.getenv("BYBIT_SECRET")

DRY_RUN = False              # True = только логирует сигналы, ордера не отправляет

SYMBOL          = "ADAUSDT"
TIMEFRAME_ENTRY = "1"       # таймфрейм сигналов MACD
TIMEFRAME_TREND = "15"      # старший таймфрейм для тренда (60 = 1h, 240 = 4h)
LEVERAGE        = 10        # ВАЖНО: при SL 1% плечо 50x почти равно ликвидации
CHECK_INTERVAL  = 60        # секунд

DEPOSIT_PCT = 50.0          # какой % депозита использовать как маржу на сделку
STOP_LOSS_PCT   = 0.01      # 1%
TAKE_PROFIT_PCT = 0.01      # 1%

# Режим выхода по MACD-пересечению в дополнение к TP/SL
EXIT_ON_MACD = True

# Тренд: EMA fast/slow на старшем таймфрейме
EMA_FAST = 50
EMA_SLOW = 200

# ===== ПОДКЛЮЧЕНИЕ =====
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# Состояние для DRY RUN
dry_position = None   # {"side": "Buy"/"Sell", "entry", "sl", "tp"}
dry_stats = {"trades": 0, "wins": 0, "pnl_pct": 0.0}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


# ===== СВЕЧИ =====
def get_klines(interval, limit=200):
    resp = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=interval,
        limit=limit,
    )
    df = pd.DataFrame(resp['result']['list'])
    df = df.astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    df.columns = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]
    return df


# ===== ТРЕНД (старший таймфрейм) =====
def get_trend():
    """
    uptrend   : close > EMA200 и EMA50 > EMA200
    downtrend : close < EMA200 и EMA50 < EMA200
    neutral   : всё остальное — не торгуем
    """
    df = get_klines(TIMEFRAME_TREND, limit=250)
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    last = df.iloc[-1]

    if last['close'] > last['ema_slow'] and last['ema_fast'] > last['ema_slow']:
        return "uptrend"
    if last['close'] < last['ema_slow'] and last['ema_fast'] < last['ema_slow']:
        return "downtrend"
    return "neutral"


# ===== ИНДИКАТОРЫ =====
def calculate_indicators(df):
    macd = MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    return df


# ===== СИГНАЛЫ (логика твоих ботов) =====
def cross_up(df):
    """MACD пересёк сигнальную снизу вверх."""
    last, prev = df.iloc[-1], df.iloc[-2]
    return prev['macd'] < prev['macd_signal'] and last['macd'] >= last['macd_signal'] and last['macd'] > 0


def cross_down(df):
    """MACD пересёк сигнальную сверху вниз."""
    last, prev = df.iloc[-1], df.iloc[-2]
    return prev['macd'] > prev['macd_signal'] and last['macd'] <= last['macd_signal'] and last['macd'] < 0

def exit_cross_up(df):
    """Простое пересечение вверх — для ВЫХОДА из шорта."""
    last, prev = df.iloc[-1], df.iloc[-2]
    return (
        prev['macd'] < prev['macd_signal'] and
        last['macd'] >= last['macd_signal']
    )

def exit_cross_down(df):
    """Простое пересечение вниз — для ВЫХОДА из лонга."""
    last, prev = df.iloc[-1], df.iloc[-2]
    return (
        prev['macd'] > prev['macd_signal'] and
        last['macd'] <= last['macd_signal']
    )


# ===== ИНФОРМАЦИЯ ОБ ИНСТРУМЕНТЕ =====
def get_instrument_rules():
    resp = session.get_instruments_info(category="linear", symbol=SYMBOL)
    info = resp['result']['list'][0]
    return {
        "qty_step":  Decimal(info['lotSizeFilter']['qtyStep']),
        "min_qty":   Decimal(info['lotSizeFilter']['minOrderQty']),
        "tick_size": Decimal(info['priceFilter']['tickSize']),
    }


def round_step(value, step):
    """Округляем вниз до шага."""
    v = Decimal(str(value))
    return (v / step).to_integral_value(rounding=ROUND_DOWN) * step


# ===== БАЛАНС И РАЗМЕР ПОЗИЦИИ =====
def get_balance():
    resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    coin = resp['result']['list'][0]['coin'][0]
    return float(coin['walletBalance'])


def calc_qty(price, rules):
    """
    Маржа = баланс * DEPOSIT_PCT%
    Размер позиции (нотионал) = маржа * плечо
    """
    balance = get_balance() if not DRY_RUN else 1000.0   # в dry run условный депозит
    margin = balance * DEPOSIT_PCT / 100
    notional = margin * LEVERAGE
    qty = round_step(notional / price, rules["qty_step"])

    if qty < rules["min_qty"]:
        log(f"Qty {qty} меньше минимального {rules['min_qty']} — увеличь DEPOSIT_PCT или плечо")
        return None

    log(f"Баланс: ${balance:.2f} | Маржа: ${margin:.2f} ({DEPOSIT_PCT}%) | Позиция: ${notional:.2f} | qty: {qty}")
    return qty


# ===== ПОЗИЦИЯ =====
def has_position():
    if DRY_RUN:
        return dry_position is not None
    resp = session.get_positions(category="linear", symbol=SYMBOL)
    if not resp['result']['list']:
        return None
    pos = resp['result']['list'][0]
    return pos if float(pos['size']) > 0 else None


def set_leverage():
    if DRY_RUN:
        return
    try:
        session.set_leverage(
            category="linear",
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
        log(f"Плечо установлено: {LEVERAGE}x")
    except Exception as e:
        # ErrCode 110043 = плечо уже такое, это не ошибка
        if "110043" not in str(e):
            log(f"Ошибка установки плеча: {e}")

def set_position_mode():
    if DRY_RUN:
        return
    try:
        session.switch_position_mode(
            category="linear",
            symbol=SYMBOL,
            mode=0   # 0 = one-way
        )
        log("Режим позиции: one-way")
    except Exception as e:
        if "110025" not in str(e) and "not modified" not in str(e).lower():
            log(f"Ошибка смены режима: {e}")


# ===== ОРДЕРА =====
def open_position(side, price, rules):
    global dry_position

    qty = calc_qty(price, rules)
    if qty is None:
        return

    if side == "Buy":
        sl = price * (1 - STOP_LOSS_PCT)
        tp = price * (1 + TAKE_PROFIT_PCT)
    else:
        sl = price * (1 + STOP_LOSS_PCT)
        tp = price * (1 - TAKE_PROFIT_PCT)

    sl = round_step(sl, rules["tick_size"])
    tp = round_step(tp, rules["tick_size"])

    log(f"Открываю {'LONG' if side == 'Buy' else 'SHORT'} @ ~{price} | SL: {sl} | TP: {tp}")

    if DRY_RUN:
        dry_position = {"side": side, "entry": price, "sl": float(sl), "tp": float(tp)}
        return

    try:
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            orderType="Market",
            qty=str(qty),
            takeProfit=str(tp),
            stopLoss=str(sl),
            tpTriggerBy="LastPrice",
            slTriggerBy="LastPrice",
            timeInForce="ImmediateOrCancel",
        )
    except Exception as e:
        log(f"Ошибка открытия: {e}")


def close_position(pos):
    """Закрываем всю позицию по рынку (на реальный размер, а не на фиксированный QTY)."""
    global dry_position

    if DRY_RUN:
        price = float(get_klines(TIMEFRAME_ENTRY, limit=2).iloc[-1]['close'])
        side = dry_position["side"]
        move = (price - dry_position["entry"]) / dry_position["entry"]
        pnl = move if side == "Buy" else -move
        dry_stats["trades"] += 1
        dry_stats["wins"] += 1 if pnl > 0 else 0
        dry_stats["pnl_pct"] += pnl * 100
        log(f"[DRY RUN] Закрыто по MACD @ {price} | {pnl*100:+.2f}% (без плеча)")
        dry_position = None
        return

    close_side = "Sell" if pos['side'] == "Buy" else "Buy"
    try:
        log("Закрываю позицию по MACD")
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=close_side,
            orderType="Market",
            qty=pos['size'],
            reduceOnly=True,
            timeInForce="ImmediateOrCancel",
        )
    except Exception as e:
        log(f"Ошибка закрытия: {e}")


def check_dry_tp_sl(df):
    """Имитация срабатывания TP/SL на бирже по high/low последней свечи."""
    global dry_position
    if not DRY_RUN or dry_position is None:
        return

    last = df.iloc[-1]
    p = dry_position
    hit = None

    if p["side"] == "Buy":
        if last['low'] <= p["sl"]:
            hit = ("SL", p["sl"])
        elif last['high'] >= p["tp"]:
            hit = ("TP", p["tp"])
    else:
        if last['high'] >= p["sl"]:
            hit = ("SL", p["sl"])
        elif last['low'] <= p["tp"]:
            hit = ("TP", p["tp"])

    if hit:
        name, price = hit
        move = (price - p["entry"]) / p["entry"]
        pnl = move if p["side"] == "Buy" else -move
        dry_stats["trades"] += 1
        dry_stats["wins"] += 1 if pnl > 0 else 0
        dry_stats["pnl_pct"] += pnl * 100
        log(f"[DRY RUN] {name} сработал @ {price} | {pnl*100:+.2f}% (без плеча)")
        dry_position = None
        s = dry_stats
        log(f"[DRY RUN] Итого: сделок={s['trades']} | винрейт={s['wins']/s['trades']*100:.0f}% | сумма={s['pnl_pct']:+.2f}%")


# ===== ОСНОВНОЙ ЦИКЛ =====
def main():
    log("=" * 55)
    log(f"MACD TREND BOT {'[DRY RUN]' if DRY_RUN else '[LIVE]'}")
    log(f"{SYMBOL} | сигналы: {TIMEFRAME_ENTRY}m | тренд: {TIMEFRAME_TREND}m")
    log(f"Плечо: {LEVERAGE}x | Депозит на сделку: {DEPOSIT_PCT}% | SL/TP: {STOP_LOSS_PCT*100:.1f}%/{TAKE_PROFIT_PCT*100:.1f}%")
    log("=" * 55)

    set_position_mode()
    rules = get_instrument_rules()
    set_leverage()

    while True:
        try:
            trend = get_trend()
            df = calculate_indicators(get_klines(TIMEFRAME_ENTRY))
            price = float(df['close'].iloc[-1])
            pos = has_position()          # один запрос за итерацию

            log(f"Тренд: {trend.upper()} | Цена: {price} | MACD: {df['macd'].iloc[-1]:.6f} "
                f"Signal: {df['macd_signal'].iloc[-1]:.6f} | Позиция: {'да' if pos else 'нет'}")

            check_dry_tp_sl(df)
            pos = has_position() if DRY_RUN else pos

            if not pos:
                # Открываем только по направлению тренда
                if trend == "uptrend" and cross_up(df):
                    open_position("Buy", price, rules)
                elif trend == "downtrend" and cross_down(df):
                    open_position("Sell", price, rules)
            elif EXIT_ON_MACD:
                # Позиция уже открыта — выходим по обратному пересечению MACD
                side = dry_position["side"] if DRY_RUN else pos['side']
                if side == "Buy" and exit_cross_down(df):
                    close_position(pos)
                elif side == "Sell" and exit_cross_up(df):
                    close_position(pos)

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Остановлено пользователем")
            break
        except Exception as e:
            log(f"Ошибка цикла: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
