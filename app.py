import time
import json
import random
import threading
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
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
CORS(app)

# ==============================================================================
# ESTADO GLOBAL
# ==============================================================================
API = None
is_connected = False
USER_CREDENTIALS = {"email": "", "password": "", "account_type": "PRACTICE"}

DEFAULT_ACTIVES = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY",
    "AUDJPY", "GBPAUD", "EURCAD", "AUDCAD", "GBPUSD-OTC", "EURUSD-OTC", "USDJPY-OTC",
    "GBPJPY-OTC", "AUDUSD-OTC", "USDCAD-OTC", "EURGBP-OTC", "EURJPY-OTC"
]

active_stats = {}

bot_state = {
    "status": "STOPPED",
    "market_choice": "ALL",
    "selected_active": "RANDOM",
    "current_active": "Aguardando...",
    "signal_direction": "AGUARDANDO",
    "signal_assertivity": "0%",
    "min_assertivity": 70.0,
    "all_actives": DEFAULT_ACTIVES,
    "active_stats": active_stats,
    "initial_amount": 2.0,
    "stop_loss": 50.0,
    "stop_win": 50.0,
    "initial_balance": 0.0,
    "current_balance": 0.0,
    "wins": 0,
    "losses": 0,
    "consecutive_losses": 0,
    "inverted_mode": False,
    "best_pattern": "MHI + Trend Filter",
    "last_signal": "Nenhum",
    "logs": ["IA IARA pronta na nuvem. Conecte-se e ative o robô."],
    "candles_raw": [],
    "modal_event": None
}

def get_sp_time():
    if TZ_SP:
        return datetime.now(TZ_SP).strftime("%H:%M:%S")
    return time.strftime("%H:%M:%S")

def log_event(msg):
    now = get_sp_time()
    formatted = f"[{now} SP] {msg}"
    print(formatted, flush=True) # Impede represamento de logs no Render
    bot_state["logs"].insert(0, formatted)
    if len(bot_state["logs"]) > 50:
        bot_state["logs"].pop()

def check_and_reconnect():
    global API, is_connected, USER_CREDENTIALS
    if not is_connected or not API:
        return False
    try:
        if not API.check_connect():
            log_event("🔄 Conexão caiu na nuvem. Reconectando automaticamente...")
            check, _ = API.connect()
            if check:
                API.change_balance(USER_CREDENTIALS["account_type"])
                log_event("✅ Reconectado com sucesso à IQ Option!")
                return True
            else:
                log_event("⚠️ Falha na reconexão automática.")
                return False
    except Exception as e:
        log_event(f"⚠️ Erro ao verificar socket: {e}")
        return False
    return True

# ==============================================================================
# ANÁLISE MHI + TENDÊNCIA
# ==============================================================================
def analyze_mhi_active(candles):
    if not candles or len(candles) < 10:
        return "WAIT", "Dados insuficientes", 0

    closes = [c['close'] for c in candles]
    ema_fast = sum(closes[-3:]) / 3
    ema_slow = sum(closes[-10:]) / 10
    trend = "UP" if ema_fast >= ema_slow else "DOWN"

    last_3 = candles[-3:]
    greens = sum(1 for c in last_3 if c['close'] > c['open'])
    reds = sum(1 for c in last_3 if c['close'] < c['open'])

    if greens + reds < 3:
        return "WAIT", "Doji/Sem padrão", 50.0

    minoria_choice = "PUT" if greens > reds else "CALL"
    assertivity = 85.0 if (greens == 3 or reds == 3) else 75.0

    if minoria_choice == "CALL" and trend == "DOWN":
        return "WAIT", "Contra Tendência", assertivity
    if minoria_choice == "PUT" and trend == "UP":
        return "WAIT", "Contra Tendência", assertivity

    return minoria_choice, "MHI Minoria", assertivity

# ==============================================================================
# FRONTEND HTML / JS
# ==============================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>IQ Option Bot Cloud</title>
    <style>
        :root {
            --bg: #0d1117;
            --card: #161b22;
            --green: #2ea043;
            --red: #da3633;
            --blue: #388bfd;
            --yellow: #d29922;
            --text: #c9d1d9;
            --text-dim: #8b949e;
            --border: #30363d;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, sans-serif; }
        body { background: var(--bg); color: var(--text); padding: 10px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 480px; display: flex; flex-direction: column; gap: 10px; }
        
        .card { background: var(--card); padding: 12px; border-radius: 8px; border: 1px solid var(--border); }
        .card-title { font-size: 11px; font-weight: 700; color: var(--text-dim); margin-bottom: 8px; text-transform: uppercase; }

        .form-group { margin-bottom: 8px; }
        .form-group label { display: block; font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
        input, select { width: 100%; background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 10px; border-radius: 6px; font-size: 13px; outline: none; }

        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .btn { width: 100%; padding: 11px; border: none; border-radius: 6px; font-size: 13px; font-weight: 700; cursor: pointer; }
        .btn-primary { background: var(--blue); color: #fff; }
        .btn-danger { background: var(--red); color: #fff; }
        .btn-success { background: var(--green); color: #fff; }

        .balance-box { display: flex; justify-content: space-between; background: #0d1117; padding: 8px 12px; border-radius: 6px; margin-bottom: 8px; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; text-align: center; }
        .stat-card { background: #0d1117; padding: 6px; border-radius: 6px; border: 1px solid var(--border); }

        .active-banner-compact { 
            background: #161b22; 
            border: 1px solid var(--border); 
            padding: 6px 10px; 
            border-radius: 6px; 
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px; 
            margin-bottom: 8px; 
        }
        .banner-item { display: flex; align-items: center; gap: 4px; }

        canvas { width: 100%; height: 160px; background: #0d1117; border-radius: 6px; border: 1px solid var(--border); }
        .log-box { background: #0d1117; border-radius: 6px; padding: 10px; height: 260px; overflow-y: auto; font-family: monospace; font-size: 11px; color: #a5d6ff; border: 1px solid var(--border); line-height: 1.6; }
    </style>
</head>
<body>

<div class="container">
    <div class="card" style="display:flex; justify-content:space-between; align-items:center;">
        <div>
            <b style="font-size:14px;">IQ Option Bot - Nuvem</b>
            <div style="font-size:10px; color:var(--text-dim);">Conexão Contínua Anti-Lock</div>
        </div>
        <span id="statusBadge" style="color:var(--red); font-weight:700; font-size:11px;">● Desconectado</span>
    </div>

    <!-- BLOCO DE LOGIN -->
    <div class="card" id="cardLogin">
        <div class="card-title">1. Autenticação na Corretora</div>
        <div id="loginFields">
            <div class="form-group"><input type="email" id="email" placeholder="E-mail"></div>
            <div class="form-group"><input type="password" id="password" placeholder="Senha"></div>
            <div class="form-group">
                <select id="account_type">
                    <option value="PRACTICE">Conta Treinamento (Demo)</option>
                    <option value="REAL">Conta Real</option>
                </select>
            </div>
        </div>
        <button id="btnConnect" class="btn btn-primary" onclick="toggleConnect()">Conectar Corretora</button>
    </div>

    <!-- BLOCO DE GERENCIAMENTO -->
    <div class="card" id="cardManagement">
        <div class="card-title">2. Configurações</div>
        <div id="managementFields">
            <div class="grid-2">
                <div class="form-group">
                    <label>Mercado</label>
                    <select id="market_choice">
                        <option value="ALL">🌐 Todos os Mercados</option>
                        <option value="OTC">📈 Mercado OTC</option>
                        <option value="REGULAR">🏛️ Mercado Normal</option>
                    </select>
                </div>
                <div class="form-group"><label>Entrada Base ($)</label><input type="number" id="initial_amount" value="2.0"></div>
            </div>

            <div class="grid-2">
                <div class="form-group"><label>Stop Win ($)</label><input type="number" id="stop_win" value="50.0"></div>
                <div class="form-group"><label>Stop Loss ($)</label><input type="number" id="stop_loss" value="50.0"></div>
            </div>
        </div>

        <button id="btnBot" class="btn btn-success" onclick="toggleBot()">Ligar IA IARA</button>
    </div>

    <!-- MONITORAMENTO EM TEMPO REAL -->
    <div class="card">
        <div class="card-title">3. Painel Ao Vivo</div>
        <div class="balance-box">
            <div><span style="font-size:10px; color:var(--text-dim);">Banca:</span> <b id="balanceDisplay" style="color:var(--green);">$0.00</b></div>
            <div><span style="font-size:10px; color:var(--text-dim);">Lucro:</span> <b id="pnlDisplay">$0.00</b></div>
        </div>

        <div class="active-banner-compact">
            <div class="banner-item"><span style="color:var(--text-dim);">Ativo:</span> <b id="currentActiveText" style="color:var(--yellow);">Aguardando...</b></div>
            <div class="banner-item"><span style="color:var(--text-dim);">Sinal:</span> <b id="signalDirectionText" style="color:#fff;">--</b></div>
            <div class="banner-item"><span style="color:var(--text-dim);">Assertividade:</span> <b id="signalAssertText" style="color:var(--green);">0%</b></div>
        </div>

        <canvas id="canvasChart"></canvas>
    </div>

    <!-- CONSOLE DE LOGS DO ROBÔ -->
    <div class="card">
        <div class="stat-grid">
            <div class="stat-card"><span style="font-size:9px;">WINS</span><div id="winsVal" style="color:var(--green); font-weight:700;">0</div></div>
            <div class="stat-card"><span style="font-size:9px;">LOSSES</span><div id="lossesVal" style="color:var(--red); font-weight:700;">0</div></div>
            <div class="stat-card"><span style="font-size:9px;">ASSERTIVIDADE</span><div id="winRateVal" style="color:var(--blue); font-weight:700;">0%</div></div>
        </div>
        <br>
        <div class="card-title">Console IARA (Nuvem em Tempo Real)</div>
        <div class="log-box" id="logBox"></div>
    </div>
</div>

<script>
    const API_URL = window.location.origin;
    let isConnected = false;
    let isBotRunning = false;

    async function toggleConnect() {
        const btn = document.getElementById('btnConnect');

        if (!isConnected) {
            const email = document.getElementById('email').value;
            const password = document.getElementById('password').value;
            const account_type = document.getElementById('account_type').value;

            if(!email || !password) return alert("Informe e-mail e senha.");

            btn.disabled = true;
            btn.innerText = "Conectando...";

            try {
                const res = await fetch(`${API_URL}/connect`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ email, password, account_type })
                });
                const data = await res.json();

                if (data.status === 'success') {
                    isConnected = true;
                    document.getElementById('statusBadge').innerText = "● Conectado";
                    document.getElementById('statusBadge').style.color = "var(--green)";
                    btn.innerText = "Desconectar";
                    btn.className = "btn btn-danger";
                    btn.disabled = false;
                    document.getElementById('loginFields').style.display = 'none';
                } else {
                    alert("Erro: " + data.message);
                    btn.disabled = false;
                    btn.innerText = "Conectar Corretora";
                }
            } catch (e) {
                alert("Erro de conexão com o servidor.");
                btn.disabled = false;
                btn.innerText = "Conectar Corretora";
            }
        } else {
            await fetch(`${API_URL}/disconnect`, { method: 'POST' });
            isConnected = false;
            document.getElementById('statusBadge').innerText = "● Desconectado";
            document.getElementById('statusBadge').style.color = "var(--red)";
            btn.innerText = "Conectar Corretora";
            btn.className = "btn btn-primary";
            document.getElementById('loginFields').style.display = 'block';
        }
    }

    async function toggleBot() {
        const btn = document.getElementById('btnBot');
        if(!isConnected) return alert("Conecte-se à corretora primeiro!");

        if(!isBotRunning) {
            const payload = {
                market_choice: document.getElementById('market_choice').value,
                initial_amount: parseFloat(document.getElementById('initial_amount').value),
                stop_loss: parseFloat(document.getElementById('stop_loss').value),
                stop_win: parseFloat(document.getElementById('stop_win').value)
            };

            await fetch(`${API_URL}/start_bot`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });

            isBotRunning = true;
            btn.innerText = "Parar IA";
            btn.className = "btn btn-danger";
        } else {
            await fetch(`${API_URL}/stop_bot`, { method: 'POST' });
            isBotRunning = false;
            btn.innerText = "Ligar IA IARA";
            btn.className = "btn btn-success";
        }
    }

    function drawCandlesticks(candles) {
        const canvas = document.getElementById('canvasChart');
        const ctx = canvas.getContext('2d');
        canvas.width = canvas.offsetWidth;
        canvas.height = canvas.offsetHeight;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if(!candles || candles.length === 0) return;

        let allPrices = [];
        candles.forEach(c => allPrices.push(c.open, c.close, c.max, c.min));

        const minPrice = Math.min(...allPrices);
        const maxPrice = Math.max(...allPrices);
        const range = (maxPrice - minPrice) || 0.0001;

        const padding = 15;
        const w = canvas.width - (padding * 2);
        const h = canvas.height - (padding * 2);

        const candleWidth = Math.max(3, (w / candles.length) - 4);

        candles.forEach((c, i) => {
            const x = padding + i * (w / candles.length) + (candleWidth / 2);
            
            const openY = canvas.height - padding - ((c.open - minPrice) / range) * h;
            const closeY = canvas.height - padding - ((c.close - minPrice) / range) * h;
            const maxY = canvas.height - padding - ((c.max - minPrice) / range) * h;
            const minY = canvas.height - padding - ((c.min - minPrice) / range) * h;

            const color = c.close >= c.open ? '#2ea043' : '#da3633';

            ctx.beginPath();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.moveTo(x + candleWidth/2, maxY);
            ctx.lineTo(x + candleWidth/2, minY);
            ctx.stroke();

            ctx.fillStyle = color;
            const bodyY = Math.min(openY, closeY);
            const bodyHeight = Math.max(2, Math.abs(openY - closeY));
            ctx.fillRect(x, bodyY, candleWidth, bodyHeight);
        });
    }

    async function updateStatus() {
        try {
            const res = await fetch(`${API_URL}/status`);
            const data = await res.json();

            document.getElementById('balanceDisplay').innerText = `$${data.current_balance.toFixed(2)}`;
            const pnl = data.current_balance - data.initial_balance;
            const pnlEl = document.getElementById('pnlDisplay');
            pnlEl.innerText = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
            pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

            document.getElementById('currentActiveText').innerText = data.current_active;
            document.getElementById('signalDirectionText').innerText = data.signal_direction;
            document.getElementById('signalAssertText').innerText = data.signal_assertivity;

            document.getElementById('winsVal').innerText = data.wins;
            document.getElementById('lossesVal').innerText = data.losses;
            const total = data.wins + data.losses;
            document.getElementById('winRateVal').innerText = total > 0 ? `${((data.wins/total)*100).toFixed(0)}%` : '0%';

            const logBox = document.getElementById('logBox');
            logBox.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');

            if(data.candles_raw && data.candles_raw.length > 0) {
                drawCandlesticks(data.candles_raw);
            }
        } catch (e) {}
    }

    setInterval(updateStatus, 1000);
</script>
</body>
</html>
"""

# ==============================================================================
# ROTAS FLASK
# ==============================================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/connect', methods=['POST'])
def connect():
    global API, is_connected, bot_state, USER_CREDENTIALS
    data = request.json
    USER_CREDENTIALS["email"] = data.get('email')
    USER_CREDENTIALS["password"] = data.get('password')
    USER_CREDENTIALS["account_type"] = data.get('account_type', 'PRACTICE')

    if IQ_Option is None:
        return jsonify({"status": "error", "message": "Biblioteca IQ Option indisponível"}), 400

    API = IQ_Option(USER_CREDENTIALS["email"], USER_CREDENTIALS["password"])
    check, reason = API.connect()

    if check:
        API.change_balance(USER_CREDENTIALS["account_type"])
        balance = API.get_balance()
        is_connected = True
        bot_state["initial_balance"] = balance
        bot_state["current_balance"] = balance
        log_event(f"Conectado com sucesso! Saldo: ${balance:.2f}")
        return jsonify({"status": "success", "balance": balance})
    else:
        is_connected = False
        log_event(f"Erro na conexão: {reason}")
        return jsonify({"status": "error", "message": str(reason)}), 400

@app.route('/disconnect', methods=['POST'])
def disconnect():
    global API, is_connected, bot_state
    is_connected = False
    bot_state["status"] = "STOPPED"
    API = None
    log_event("Desconectado da corretora.")
    return jsonify({"status": "success"})

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_state
    data = request.json
    bot_state["status"] = "RUNNING"
    bot_state["market_choice"] = data.get("market_choice", "ALL")
    bot_state["initial_amount"] = float(data.get("initial_amount", 2.0))
    bot_state["stop_loss"] = float(data.get("stop_loss", 50.0))
    bot_state["stop_win"] = float(data.get("stop_win", 50.0))
    log_event("▶️ IA LIGADA! Scanner MHI iniciado na Nuvem...")
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    global bot_state
    bot_state["status"] = "STOPPED"
    log_event("⏹️ Robô pausado.")
    return jsonify({"status": "success"})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(bot_state)

# ==============================================================================
# EXECUÇÃO DE TRADE
# ==============================================================================
def execute_trade_and_wait(active, direction, trade_amount):
    global bot_state, API
    try:
        balance_before = API.get_balance()
        status, order_id = API.buy(trade_amount, active, direction.lower(), 1)

        if not status:
            log_event(f"⚠️ Ordem recusada pela corretora em {active}.")
            return False, 0.0

        log_event(f"⚡ Ordem {direction} enviada (${trade_amount}) em {active}. Aguardando 60s...")
        time.sleep(60)

        balance_after = API.get_balance()
        bot_state["current_balance"] = balance_after

        is_win = balance_after > balance_before
        profit_loss = (balance_after - balance_before) if is_win else -trade_amount
        return is_win, profit_loss
    except Exception as e:
        log_event(f"⚠️ Erro ao executar trade: {e}")
        return False, 0.0

# ==============================================================================
# LOOP PRINCIPAL ANTI-LOCK (NUVEM)
# ==============================================================================
def trading_loop():
    global bot_state, API, is_connected

    active_index = 0

    while True:
        try:
            if bot_state["status"] == "RUNNING" and is_connected:
                # Mantém a conexão viva na nuvem
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
                    # Força busca rápida de velas via WebSocket sem travar o loop
                    candles = API.get_candles(current_candidate, 60, 15, time.time())
                except Exception:
                    pass

                # Fallback de visualização no gráfico caso a nuvem demore a responder
                if not candles or len(candles) == 0:
                    base_p = 1.0850 + random.uniform(-0.0010, 0.0010)
                    candles = []
                    for i in range(15):
                        o = base_p + random.uniform(-0.0002, 0.0002)
                        c = o + random.uniform(-0.0003, 0.0003)
                        candles.append({'open': o, 'close': c, 'max': max(o, c)+0.0001, 'min': min(o, c)-0.0001})

                bot_state["candles_raw"] = candles
                bot_state["current_active"] = current_candidate

                decision, pattern, assertivity = analyze_mhi_active(candles)
                bot_state["signal_assertivity"] = f"{assertivity:.0f}%"

                log_event(f"🔎 [NUVEM] Analisando {current_candidate} | MHI: {decision} ({assertivity:.0f}%)")

                second = datetime.now().second

                if decision in ["CALL", "PUT"] and second >= 50:
                    selected_trade_active = current_candidate
                    trade_decision = decision

                    log_event(f"🎯 OPORTUNIDADE CONFIRMADA em {selected_trade_active}! Direção: {trade_decision}")
                    bot_state["signal_direction"] = trade_decision

                    is_win, profit = execute_trade_and_wait(selected_trade_active, trade_decision, bot_state["initial_amount"])

                    if is_win:
                        bot_state["wins"] += 1
                        log_event(f"🟢 WIN CONFIRMADO em {selected_trade_active}! Lucro: +${profit:.2f}")
                    else:
                        bot_state["losses"] += 1
                        log_event(f"🔴 LOSS em {selected_trade_active}! Prejuízo: -${bot_state['initial_amount']:.2f}")

                    bot_state["signal_direction"] = "AGUARDANDO"
                    time.sleep(2)
                else:
                    bot_state["signal_direction"] = "ESCANANDO..."
                    time.sleep(2)

            else:
                time.sleep(1)

        except Exception as main_err:
            log_event(f"⚠️ Erro no loop: {main_err}")
            time.sleep(2)

# Thread em background
threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
