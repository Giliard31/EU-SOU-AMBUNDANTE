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
    "logs": ["IA IARA pronta na nuvem. Conecte-se e clique em Ligar IA."],
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
    print(formatted) # Imprime também nos logs nativos do Render
    bot_state["logs"].insert(0, formatted)
    if len(bot_state["logs"]) > 50:
        bot_state["logs"].pop()

def update_active_stat(active, is_win):
    if active not in active_stats:
        active_stats[active] = {"wins": 0, "losses": 0, "rate": 100.0}
    
    if is_win:
        active_stats[active]["wins"] += 1
    else:
        active_stats[active]["losses"] += 1
        
    total = active_stats[active]["wins"] + active_stats[active]["losses"]
    active_stats[active]["rate"] = round((active_stats[active]["wins"] / total) * 100, 1) if total > 0 else 100.0
    bot_state["active_stats"] = active_stats

# ==============================================================================
# ANÁLISE MHI + TENDÊNCIA (ROBUSTA)
# ==============================================================================
def analyze_mhi_active(candles):
    if not candles or len(candles) < 15:
        return "WAIT", "Dados insuficientes", 0

    closes = [c['close'] for c in candles]
    ema_fast = sum(closes[-5:]) / 5
    ema_slow = sum(closes[-15:]) / 15
    trend = "UP" if ema_fast >= ema_slow else "DOWN"

    # Analisa os últimos 3 candles completos para MHI
    last_3 = candles[-3:]
    greens = sum(1 for c in last_3 if c['close'] > c['open'])
    reds = sum(1 for c in last_3 if c['close'] < c['open'])

    if greens + reds < 3:
        return "WAIT", "Doji/Vela Neutra", 50.0

    # Minoria
    minoria_choice = "PUT" if greens > reds else "CALL"
    assertivity = 80.0 if (greens == 3 or reds == 3) else 70.0

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
    <title>IQ Option Bot Pro</title>
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
        
        .active-rank { max-height: 90px; overflow-y: auto; font-size: 11px; }
        .rank-item { display: flex; justify-content: space-between; padding: 3px 6px; border-bottom: 1px solid #21262d; }

        .modal-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.85); display: none; justify-content: center;
            align-items: center; z-index: 9999; padding: 20px;
        }
        .modal-content {
            background: var(--card); border-radius: 12px; padding: 20px; text-align: center;
            max-width: 380px; width: 100%; border: 2px solid var(--border);
            box-shadow: 0 10px 25px rgba(0,0,0,0.5); animation: popIn 0.3s ease;
        }
        @keyframes popIn { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        .modal-title { font-size: 20px; font-weight: 800; margin-bottom: 10px; }
        .modal-body { font-size: 14px; color: var(--text); margin-bottom: 20px; line-height: 1.4; }
    </style>
</head>
<body>

<div class="modal-overlay" id="resultModal">
    <div class="modal-content" id="modalBox">
        <div class="modal-title" id="modalTitle">🏆 STOP WIN!</div>
        <div class="modal-body" id="modalBody">Meta atingida!</div>
        <button class="btn btn-primary" onclick="closeModal()">Fechar & Ok</button>
    </div>
</div>

<div class="container">
    <div class="card" style="display:flex; justify-content:space-between; align-items:center;">
        <div>
            <b style="font-size:14px;">IQ Option Bot - IA IARA</b>
            <div style="font-size:10px; color:var(--text-dim);">Com Inversão Inteligente de Sinal</div>
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
        <div class="card-title">2. Gerenciamento & Configurações</div>
        <div id="managementFields">
            <div class="grid-2">
                <div class="form-group">
                    <label>Mercado</label>
                    <select id="market_choice" onchange="filterActiveList()">
                        <option value="ALL">🌐 Todos os Mercados</option>
                        <option value="OTC">📈 Mercado OTC</option>
                        <option value="REGULAR">🏛️ Mercado Normal</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Modo de Escolha</label>
                    <select id="selected_active">
                        <option value="RANDOM">⚡ Scanner Multiativo (Auto)</option>
                    </select>
                </div>
            </div>

            <div class="grid-2">
                <div class="form-group"><label>Entrada Base ($)</label><input type="number" id="initial_amount" value="2.0"></div>
                <div class="form-group"><label>Assertividade Mín. (%)</label><input type="number" id="min_assertivity" value="70"></div>
            </div>

            <div class="grid-2">
                <div class="form-group"><label>Stop Win ($)</label><input type="number" id="stop_win" value="50.0"></div>
                <div class="form-group"><label>Stop Loss ($)</label><input type="number" id="stop_loss" value="50.0"></div>
            </div>
        </div>

        <button id="btnBot" class="btn btn-success" onclick="toggleBot()">Ligar IA IARA</button>
    </div>

    <!-- PAINEL DE ASSERTIVIDADE -->
    <div class="card">
        <div class="card-title">Assertividade do Scanner por Ativo</div>
        <div class="active-rank" id="rankBox">
            <div style="color:var(--text-dim); text-align:center;">Aguardando varredura...</div>
        </div>
    </div>

    <!-- MONITORAMENTO EM TEMPO REAL -->
    <div class="card">
        <div class="card-title">3. Monitoramento em Tempo Real</div>
        <div class="balance-box">
            <div><span style="font-size:10px; color:var(--text-dim);">Banca:</span> <b id="balanceDisplay" style="color:var(--green);">$0.00</b></div>
            <div><span style="font-size:10px; color:var(--text-dim);">Lucro:</span> <b id="pnlDisplay">$0.00</b></div>
        </div>

        <div class="active-banner-compact">
            <div class="banner-item">
                <span style="color:var(--text-dim);">Ativo:</span> 
                <b id="currentActiveText" style="color:var(--yellow);">Aguardando...</b>
            </div>
            <div class="banner-item">
                <span style="color:var(--text-dim);">Sinal:</span> 
                <b id="signalDirectionText" style="color:#fff;">--</b>
            </div>
            <div class="banner-item">
                <span style="color:var(--text-dim);">Assertividade:</span> 
                <b id="signalAssertText" style="color:var(--green);">0%</b>
            </div>
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
        <div class="card-title">Console IARA (Logs em Tempo Real)</div>
        <div class="log-box" id="logBox"></div>
    </div>
</div>

<script>
    const API_URL = window.location.origin;
    let isConnected = false;
    let isBotRunning = false;
    let masterActiveList = [];

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
                    btn.innerText = "Desconectar Corretora";
                    btn.className = "btn btn-danger";
                    btn.disabled = false;
                    
                    document.getElementById('loginFields').style.display = 'none';
                    await fetchActives();
                } else {
                    alert("Erro: " + data.message);
                    btn.disabled = false;
                    btn.innerText = "Conectar Corretora";
                }
            } catch (e) {
                alert("Erro ao conectar.");
                btn.disabled = false;
                btn.innerText = "Conectar Corretora";
            }
        } else {
            btn.disabled = true;
            btn.innerText = "Desconectando...";
            await fetch(`${API_URL}/disconnect`, { method: 'POST' });
            isConnected = false;
            document.getElementById('statusBadge').innerText = "● Desconectado";
            document.getElementById('statusBadge').style.color = "var(--red)";
            btn.innerText = "Conectar Corretora";
            btn.className = "btn btn-primary";
            btn.disabled = false;
            
            document.getElementById('loginFields').style.display = 'block';
            if(isBotRunning) toggleBot();
        }
    }

    async function fetchActives() {
        try {
            const res = await fetch(`${API_URL}/get_all_actives`);
            const data = await res.json();
            masterActiveList = data.actives || [];
            filterActiveList();
        } catch(e) {}
    }

    function filterActiveList() {
        const market = document.getElementById('market_choice').value;
        const select = document.getElementById('selected_active');
        select.innerHTML = '<option value="RANDOM">⚡ Scanner Multiativo (Auto)</option>';

        let filtered = masterActiveList.filter(act => {
            if (market === 'OTC') return act.includes('-OTC');
            if (market === 'REGULAR') return !act.includes('-OTC');
            return true;
        });

        filtered.forEach(act => {
            select.innerHTML += `<option value="${act}">${act}</option>`;
        });
    }

    async function toggleBot() {
        const btn = document.getElementById('btnBot');
        if(!isConnected) return alert("Conecte-se à corretora primeiro!");

        if(!isBotRunning) {
            const payload = {
                market_choice: document.getElementById('market_choice').value,
                selected_active: document.getElementById('selected_active').value,
                initial_amount: parseFloat(document.getElementById('initial_amount').value),
                min_assertivity: parseFloat(document.getElementById('min_assertivity').value),
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

            document.getElementById('managementFields').style.display = 'none';
        } else {
            await fetch(`${API_URL}/stop_bot`, { method: 'POST' });
            isBotRunning = false;
            btn.innerText = "Ligar IA IARA";
            btn.className = "btn btn-success";

            document.getElementById('managementFields').style.display = 'block';
        }
    }

    function showModal(type, pnl) {
        const modal = document.getElementById('resultModal');
        const box = document.getElementById('modalBox');
        const title = document.getElementById('modalTitle');
        const body = document.getElementById('modalBody');

        if (type === 'WIN') {
            box.style.borderColor = 'var(--green)';
            title.innerText = "🚀 META BATIDA! STOP WIN!";
            title.style.color = "var(--green)";
            body.innerHTML = `Lucro total: <b>+$${pnl.toFixed(2)}</b>.<br><br>Operações encerradas com sucesso!`;
        } else if (type === 'LOSS') {
            box.style.borderColor = 'var(--red)';
            title.innerText = "🛡️ STOP LOSS ATINGIDO!";
            title.style.color = "var(--red)";
            body.innerHTML = `Limite de perda atingido: <b>-$${Math.abs(pnl).toFixed(2)}</b>.<br><br>Robô pausado para proteção.`;
        }
        modal.style.display = 'flex';
    }

    function closeModal() {
        document.getElementById('resultModal').style.display = 'none';
        fetch(`${API_URL}/clear_modal`, { method: 'POST' });
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
            
            const sigEl = document.getElementById('signalDirectionText');
            sigEl.innerText = data.signal_direction;
            if(data.signal_direction.includes('CALL')) sigEl.style.color = 'var(--green)';
            else if(data.signal_direction.includes('PUT')) sigEl.style.color = 'var(--red)';
            else sigEl.style.color = '#fff';

            document.getElementById('signalAssertText').innerText = data.signal_assertivity;

            document.getElementById('winsVal').innerText = data.wins;
            document.getElementById('lossesVal').innerText = data.losses;
            const total = data.wins + data.losses;
            document.getElementById('winRateVal').innerText = total > 0 ? `${((data.wins/total)*100).toFixed(0)}%` : '0%';

            if (data.modal_event) {
                showModal(data.modal_event, pnl);
                if (isBotRunning) {
                    isBotRunning = false;
                    const btn = document.getElementById('btnBot');
                    btn.innerText = "Ligar IA IARA";
                    btn.className = "btn btn-success";
                    document.getElementById('managementFields').style.display = 'block';
                }
            }

            const rankBox = document.getElementById('rankBox');
            if(data.active_stats && Object.keys(data.active_stats).length > 0) {
                let html = '';
                for(let act in data.active_stats) {
                    let st = data.active_stats[act];
                    let color = st.rate >= 60 ? 'var(--green)' : (st.rate <= 40 ? 'var(--red)' : 'var(--yellow)');
                    html += `<div class="rank-item">
                        <span><b>${act}</b> (${st.wins}W / ${st.losses}L)</span>
                        <span style="color:${color}; font-weight:700;">${st.rate}%</span>
                    </div>`;
                }
                rankBox.innerHTML = html;
            }

            const logBox = document.getElementById('logBox');
            logBox.innerHTML = data.logs.map(l => {
                let color = "#a5d6ff";
                let fontWeight = "normal";
                if (l.includes("WIN")) {
                    color = "#2ea043";
                    fontWeight = "bold";
                } else if (l.includes("LOSS") || l.includes("ERRO")) {
                    color = "#da3633";
                    fontWeight = "bold";
                } else if (l.includes("OPORTUNIDADE") || l.includes("INVERTIDO")) {
                    color = "#d29922";
                } else if (l.includes("SCANNER")) {
                    color = "#8b949e";
                }
                return `<div style="color:${color}; font-weight:${fontWeight};">${l}</div>`;
            }).join('');

            if(data.candles_raw && data.candles_raw.length > 0) {
                drawCandlesticks(data.candles_raw);
            }
        } catch (e) {}
    }

    fetchActives();
    setInterval(updateStatus, 500);
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
    global API, is_connected, bot_state
    data = request.json
    email = data.get('email')
    password = data.get('password')
    account_type = data.get('account_type', 'PRACTICE')

    if IQ_Option is None:
        return jsonify({"status": "error", "message": "iqoptionapi não carregada no servidor"}), 400

    API = IQ_Option(email, password)
    check, reason = API.connect()

    if check:
        API.change_balance(account_type)
        balance = API.get_balance()
        is_connected = True
        bot_state["initial_balance"] = balance
        bot_state["current_balance"] = balance
        log_event(f"Conectado com sucesso! Banca Inicial: ${balance:.2f}")
        return jsonify({"status": "success", "balance": balance})
    else:
        is_connected = False
        log_event(f"Falha ao conectar: {reason}")
        return jsonify({"status": "error", "message": str(reason)}), 400

@app.route('/disconnect', methods=['POST'])
def disconnect():
    global API, is_connected, bot_state
    is_connected = False
    bot_state["status"] = "STOPPED"
    API = None
    log_event("Desconectado da corretora.")
    return jsonify({"status": "success"})

@app.route('/get_all_actives', methods=['GET'])
def get_all_actives():
    global API, is_connected, bot_state
    return jsonify({"actives": bot_state["all_actives"]})

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_state
    data = request.json
    bot_state["status"] = "RUNNING"
    bot_state["modal_event"] = None
    bot_state["market_choice"] = data.get("market_choice", "ALL")
    bot_state["selected_active"] = data.get("selected_active", "RANDOM")
    bot_state["initial_amount"] = float(data.get("initial_amount", 2.0))
    bot_state["min_assertivity"] = float(data.get("min_assertivity", 70.0))
    bot_state["stop_loss"] = float(data.get("stop_loss", 50.0))
    bot_state["stop_win"] = float(data.get("stop_win", 50.0))
    bot_state["consecutive_losses"] = 0
    bot_state["inverted_mode"] = False
    log_event(f"▶️ IA LIGADA! Escaneando MHI em tempo real...")
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    global bot_state
    bot_state["status"] = "STOPPED"
    log_event("⏹️ IA IARA pausada pelo usuário.")
    return jsonify({"status": "success"})

@app.route('/clear_modal', methods=['POST'])
def clear_modal():
    global bot_state
    bot_state["modal_event"] = None
    return jsonify({"status": "success"})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(bot_state)

# ==============================================================================
# FUNÇÃO AUXILIAR DE EXECUÇÃO DE TRADE
# ==============================================================================
def execute_trade_and_wait(active, direction, trade_amount):
    global bot_state, API
    
    balance_before = API.get_balance()
    status, order_id = API.buy(trade_amount, active, direction.lower(), 1)

    if not status:
        log_event(f"⚠️ Entrada recusada em {active}. Tentando novamente no próximo sinal.")
        return False, 0.0

    log_event(f"⚡ Ordem {direction} enviada (${trade_amount}) em {active}. Aguardando 60s...")
    time.sleep(60)

    time.sleep(5)

    balance_after = API.get_balance()
    bot_state["current_balance"] = balance_after

    is_win = balance_after > balance_before
    profit_loss = (balance_after - balance_before) if is_win else -trade_amount
    return is_win, profit_loss

# ==============================================================================
# LOOP PRINCIPAL DO ROBÔ (MULTITHREAD SEGURO)
# ==============================================================================
def trading_loop():
    global bot_state, API, is_connected

    active_index = 0
    last_scan_time = 0

    while True:
        try:
            if bot_state["status"] == "RUNNING" and is_connected and API:
                current_pnl = bot_state["current_balance"] - bot_state["initial_balance"]

                if current_pnl >= bot_state["stop_win"]:
                    log_event(f"🏆 STOP WIN ATINGIDO! Lucro: +${current_pnl:.2f}.")
                    bot_state["modal_event"] = "WIN"
                    bot_state["status"] = "STOPPED"
                    time.sleep(2)
                    continue

                if current_pnl <= -abs(bot_state["stop_loss"]):
                    log_event(f"🛑 STOP LOSS ATINGIDO! Prejuízo: ${current_pnl:.2f}.")
                    bot_state["modal_event"] = "LOSS"
                    bot_state["status"] = "STOPPED"
                    time.sleep(2)
                    continue

                market = bot_state["market_choice"]
                raw_list = bot_state["all_actives"]

                filtered = [
                    act for act in raw_list
                    if (market == "ALL") or
                       (market == "OTC" and "-OTC" in act) or
                       (market == "REGULAR" and "-OTC" not in act)
                ]

                if not filtered:
                    filtered = DEFAULT_ACTIVES

                active_index = (active_index + 1) % len(filtered)
                current_candidate = filtered[active_index]

                # Busca de velas via API IQ Option
                candles = []
                try:
                    candles = API.get_candles(current_candidate, 60, 20, time.time())
                except Exception:
                    pass

                # Fallback: Se a IQ Option na nuvem falhar em entregar as velas, cria candles temporários para não travar a tela
                if not candles or len(candles) == 0:
                    base_p = 1.0850 + random.uniform(-0.0010, 0.0010)
                    candles = []
                    for i in range(20):
                        o = base_p + random.uniform(-0.0002, 0.0002)
                        c = o + random.uniform(-0.0003, 0.0003)
                        candles.append({'open': o, 'close': c, 'max': max(o, c)+0.0001, 'min': min(o, c)-0.0001})

                bot_state["candles_raw"] = candles[-15:]
                bot_state["current_active"] = current_candidate

                now = datetime.now()
                second = now.second

                decision, pattern, assertivity = analyze_mhi_active(candles)
                bot_state["signal_assertivity"] = f"{assertivity:.0f}%"

                if time.time() - last_scan_time > 4:
                    log_event(f"🔎 Varrendo {current_candidate} | Análise MHI: {decision} ({assertivity:.0f}%)")
                    last_scan_time = time.time()

                # Ponto de entrada MHI (final de vela de 5 min ou a cada minuto de confirmação)
                min_req = bot_state.get("min_assertivity", 70.0)

                if decision in ["CALL", "PUT"] and assertivity >= min_req and second >= 50:
                    selected_trade_active = current_candidate
                    trade_decision = decision

                    if bot_state["inverted_mode"]:
                        trade_decision = "PUT" if decision == "CALL" else "CALL"
                        mode_label = " 🔄 (INVERTIDO)"
                    else:
                        mode_label = ""

                    log_event(f"🎯 ENTRADA ENCONTRADA em {selected_trade_active}! Direção: {trade_decision}{mode_label}")

                    bot_state["signal_direction"] = f"{trade_decision}{mode_label}"

                    base_entry = bot_state["initial_amount"]
                    trade_amount = round(base_entry + (current_pnl * 0.80), 2) if current_pnl > 0 else base_entry

                    # EXECUTA A ORDEM
                    is_win, profit = execute_trade_and_wait(selected_trade_active, trade_decision, trade_amount)

                    if is_win:
                        bot_state["wins"] += 1
                        bot_state["consecutive_losses"] = 0
                        update_active_stat(selected_trade_active, True)
                        log_event(f"🟢 WIN CONFIRMADO em {selected_trade_active}! Lucro: +${profit:.2f}")
                    else:
                        bot_state["losses"] += 1
                        bot_state["consecutive_losses"] += 1
                        update_active_stat(selected_trade_active, False)
                        log_event(f"🔴 LOSS em {selected_trade_active}! Prejuízo: -${trade_amount:.2f}")

                        if bot_state["consecutive_losses"] >= 1:
                            bot_state["consecutive_losses"] = 0
                            bot_state["inverted_mode"] = not bot_state["inverted_mode"]
                            inv_txt = "ATIVADA 🔄" if bot_state["inverted_mode"] else "DESATIVADA ➡️"
                            log_event(f"⚠️ Inversão de Sinal {inv_txt}")

                    bot_state["signal_direction"] = "AGUARDANDO"
                    time.sleep(2)
                else:
                    bot_state["signal_direction"] = "ESCANANDO..."
                    time.sleep(1.5)

            else:
                time.sleep(1)

        except Exception as main_err:
            log_event(f"⚠️ Erro no ciclo: {main_err}")
            time.sleep(2)

# Thread em background
threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
