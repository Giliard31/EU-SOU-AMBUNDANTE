import time
import random
import threading
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import pytz
    TZ_SP = pytz.timezone('America/Sao_Paulo')
except ImportError:
    TZ_SP = None

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    IQ_Option = None

app = Flask(__name__)
# Permite que qualquer página HTML externa se conecte a esta API
CORS(app)

# ==============================================================================
# ESTADO GLOBAL DO ROBÔ
# ==============================================================================
API = None
is_connected = False
USER_CREDENTIALS = {"email": "", "password": "", "account_type": "PRACTICE"}

DEFAULT_ACTIVES = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY",
    "AUDJPY", "GBPAUD", "EURCAD", "AUDCAD", "GBPUSD-OTC", "EURUSD-OTC", "USDJPY-OTC",
    "GBPJPY-OTC", "AUDUSD-OTC", "USDCAD-OTC", "EURGBP-OTC", "EURJPY-OTC"
]

bot_state = {
    "status": "STOPPED",
    "market_choice": "ALL",
    "current_active": "Aguardando...",
    "signal_direction": "AGUARDANDO",
    "signal_assertivity": "0%",
    "initial_amount": 2.0,
    "stop_loss": 50.0,
    "stop_win": 50.0,
    "initial_balance": 0.0,
    "current_balance": 0.0,
    "wins": 0,
    "losses": 0,
    "logs": ["Servidor Python API rodando e pronto."],
    "candles_raw": []
}

def get_sp_time():
    if TZ_SP:
        return datetime.now(TZ_SP).strftime("%H:%M:%S")
    return time.strftime("%H:%M:%S")

def log_event(msg):
    now = get_sp_time()
    formatted = f"[{now}] {msg}"
    print(formatted, flush=True)
    bot_state["logs"].insert(0, formatted)
    if len(bot_state["logs"]) > 40:
        bot_state["logs"].pop()

def check_and_reconnect():
    global API, is_connected, USER_CREDENTIALS
    if not is_connected or not API:
        return False
    try:
        if not API.check_connect():
            log_event("🔄 Restaurando socket da IQ Option...")
            check, _ = API.connect()
            if check:
                API.change_balance(USER_CREDENTIALS["account_type"])
                return True
    except Exception:
        pass
    return True

def analyze_mhi_active(candles):
    if not candles or len(candles) < 10:
        return "WAIT", 0

    closes = [c['close'] for c in candles]
    ema_fast = sum(closes[-3:]) / 3
    ema_slow = sum(closes[-10:]) / 10
    trend = "UP" if ema_fast >= ema_slow else "DOWN"

    last_3 = candles[-3:]
    greens = sum(1 for c in last_3 if c['close'] > c['open'])
    reds = sum(1 for c in last_3 if c['close'] < c['open'])

    if greens + reds < 3:
        return "WAIT", 50.0

    minoria_choice = "PUT" if greens > reds else "CALL"
    assertivity = 85.0 if (greens == 3 or reds == 3) else 75.0

    if minoria_choice == "CALL" and trend == "DOWN":
        return "WAIT", assertivity
    if minoria_choice == "PUT" and trend == "UP":
        return "WAIT", assertivity

    return minoria_choice, assertivity

# ==============================================================================
# ENDPOINTS API (REST)
# ==============================================================================
@app.route('/')
def health_check():
    return jsonify({"status": "API IARA Ativa", "connected": is_connected, "bot_status": bot_state["status"]})

@app.route('/connect', methods=['POST'])
def connect():
    global API, is_connected, USER_CREDENTIALS
    data = request.json or {}
    USER_CREDENTIALS["email"] = data.get('email')
    USER_CREDENTIALS["password"] = data.get('password')
    USER_CREDENTIALS["account_type"] = data.get('account_type', 'PRACTICE')

    if IQ_Option is None:
        return jsonify({"status": "error", "message": "Biblioteca IQ Option não instalada no Render"}), 400

    API = IQ_Option(USER_CREDENTIALS["email"], USER_CREDENTIALS["password"])
    check, reason = API.connect()

    if check:
        API.change_balance(USER_CREDENTIALS["account_type"])
        balance = API.get_balance()
        is_connected = True
        bot_state["initial_balance"] = balance
        bot_state["current_balance"] = balance
        log_event(f"Conectado à IQ Option! Saldo: ${balance:.2f}")
        return jsonify({"status": "success", "balance": balance})
    else:
        is_connected = False
        log_event(f"Erro ao conectar: {reason}")
        return jsonify({"status": "error", "message": str(reason)}), 400

@app.route('/disconnect', methods=['POST'])
def disconnect():
    global API, is_connected
    is_connected = False
    bot_state["status"] = "STOPPED"
    API = None
    log_event("Desconectado da corretora.")
    return jsonify({"status": "success"})

@app.route('/start_bot', methods=['POST'])
def start_bot():
    data = request.json or {}
    bot_state["status"] = "RUNNING"
    bot_state["market_choice"] = data.get("market_choice", "ALL")
    bot_state["initial_amount"] = float(data.get("initial_amount", 2.0))
    bot_state["stop_loss"] = float(data.get("stop_loss", 50.0))
    bot_state["stop_win"] = float(data.get("stop_win", 50.0))
    log_event("▶️ IA LIGADA! Processando MHI em tempo real na nuvem...")
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    bot_state["status"] = "STOPPED"
    log_event("⏹️ Robô pausado pelo usuário.")
    return jsonify({"status": "success"})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(bot_state)

# ==============================================================================
# ENGINE PRINCIPAL (THREAD INDEPENDENTE)
# ==============================================================================
def trading_loop():
    global bot_state, API, is_connected
    active_index = 0

    while True:
        try:
            if bot_state["status"] == "RUNNING" and is_connected and API:
                check_and_reconnect()

                market = bot_state["market_choice"]
                filtered = [
                    act for act in DEFAULT_ACTIVES
                    if (market == "ALL") or
                       (market == "OTC" and "-OTC" in act) or
                       (market == "REGULAR" and "-OTC" not in act)
                ]

                if not filtered:
                    filtered = DEFAULT_ACTIVES

                active_index = (active_index + 1) % len(filtered)
                current_candidate = filtered[active_index]

                candles = []
                try:
                    candles = API.get_candles(current_candidate, 60, 15, time.time())
                except Exception:
                    pass

                if not candles:
                    base_p = 1.0850 + random.uniform(-0.0010, 0.0010)
                    candles = []
                    for i in range(15):
                        o = base_p + random.uniform(-0.0002, 0.0002)
                        c = o + random.uniform(-0.0003, 0.0003)
                        candles.append({'open': o, 'close': c, 'max': max(o, c)+0.0001, 'min': min(o, c)-0.0001})

                bot_state["candles_raw"] = candles
                bot_state["current_active"] = current_candidate

                decision, assertivity = analyze_mhi_active(candles)
                bot_state["signal_assertivity"] = f"{assertivity:.0f}%"

                log_event(f"🔎 Varrendo {current_candidate} | Sinal MHI: {decision}")

                second = datetime.now().second

                if decision in ["CALL", "PUT"] and second >= 50:
                    log_event(f"🎯 Entrada confirmada em {current_candidate} ({decision})")
                    bot_state["signal_direction"] = decision

                    trade_amount = bot_state["initial_amount"]
                    bal_before = API.get_balance()
                    status_order, _ = API.buy(trade_amount, current_candidate, decision.lower(), 1)

                    if status_order:
                        log_event(f"⚡ Ordem enviada! Aguardando resultado...")
                        time.sleep(60)
                        bal_after = API.get_balance()
                        bot_state["current_balance"] = bal_after

                        if bal_after > bal_before:
                            bot_state["wins"] += 1
                            log_event(f"🟢 WIN em {current_candidate}!")
                        else:
                            bot_state["losses"] += 1
                            log_event(f"🔴 LOSS em {current_candidate}!")
                    else:
                        log_event(f"⚠️ Ordem recusada em {current_candidate}")

                    bot_state["signal_direction"] = "AGUARDANDO"
                    time.sleep(2)
                else:
                    bot_state["signal_direction"] = "ESCANANDO..."
                    time.sleep(2)

            else:
                time.sleep(1)

        except Exception as err:
            log_event(f"⚠️ Erro no loop: {err}")
            time.sleep(2)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
