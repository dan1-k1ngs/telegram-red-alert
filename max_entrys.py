import asyncio
import csv
import os
import re
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telethon import TelegramClient, events

# =========================================================
# CONFIG DESDE ENV
# =========================================================
api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

source_group = int(os.environ["SOURCE_GROUP"])
target_chat_raw = os.environ["TARGET_CHAT"]

# TARGET_CHAT puede ser número o "me" o username
try:
    target_chat = int(target_chat_raw)
except ValueError:
    target_chat = target_chat_raw

session_name = os.environ.get("SESSION_NAME", "quant_max_entries_session")

MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "120"))

# PAYOUT
GAIN = int(os.environ.get("GAIN", "500"))
LOSS = int(os.environ.get("LOSS", "-1000"))

# RR
MAX_RR_ENTRIES = int(os.environ.get("MAX_RR_ENTRIES", "4"))

# MOMENTUM
MOMENTUM_TRIGGER = int(os.environ.get("MOMENTUM_TRIGGER", "9"))
MAX_MOMENTUM_ENTRIES_PER_STREAK = int(
    os.environ.get("MAX_MOMENTUM_ENTRIES_PER_STREAK", "5")
)

# PATTERNS
PREMIUM_PATTERNS = {
    "RRR", "GRRR", "GGRRR", "GGGRRR"
}

STRONG_PATTERNS = {
    "RRG", "GRRG", "GGRRG"
}

MODERATE_PATTERNS = {
    "RG", "RGG", "GRG", "RRGG", "RGR", "RGGR", "GRR", "RGRG", "RGRGG"
}

STATE_FILE = os.environ.get("STATE_FILE", "quant_max_entries_state.txt")
LOG_FILE = os.environ.get("LOG_FILE", "quant_max_entries_log.csv")

GREEN_RE = re.compile(r"\bGREEN\b", re.IGNORECASE)
RED_RE = re.compile(r"\bRED\b", re.IGNORECASE)
MULT_RE = re.compile(r"Resultado:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

# =========================================================
# HEALTH SERVER PARA RAILWAY
# =========================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server escuchando en puerto {port}")
    server.serve_forever()


# =========================================================
# CLIENT
# =========================================================
client = TelegramClient(session_name, api_id, api_hash)

# =========================================================
# STATE
# =========================================================
history = deque(maxlen=MAX_HISTORY)

last_processed_msg_id = None

# RR
rr_active = False
rr_entry_step = 0
rr_pending_trade = False

# Momentum
green_streak = 0
momentum_entries_used = 0
momentum_pending_trade = False

# Pattern
pattern_pending_trade = False
pattern_pending_name = ""
pattern_pending_level = ""

# Session stats
session_trades = 0
session_wins = 0
session_losses = 0
session_pnl = 0
session_peak = 0
session_max_dd = 0


# =========================================================
# HELPERS
# =========================================================
def parse_signal(text: str):
    if not text:
        return None, None

    text = text.strip()

    if GREEN_RE.search(text):
        m = MULT_RE.search(text)
        multiplier = float(m.group(1)) if m else None
        return "G", multiplier

    if RED_RE.search(text):
        return "R", 0.0

    return None, None


def current_winrate():
    return (session_wins / session_trades) if session_trades > 0 else 0.0


def update_drawdown():
    global session_peak, session_max_dd
    session_peak = max(session_peak, session_pnl)
    dd = session_peak - session_pnl
    session_max_dd = max(session_max_dd, dd)


def ensure_log_file():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_local",
                "message_id",
                "signal",
                "multiplier",
                "recent_pattern",
                "rr_active",
                "rr_entry_step",
                "rr_pending_trade",
                "green_streak",
                "momentum_entries_used",
                "momentum_pending_trade",
                "pattern_pending_trade",
                "pattern_pending_name",
                "pattern_pending_level",
                "action",
                "reason",
                "session_trades",
                "session_wins",
                "session_losses",
                "session_pnl",
                "session_max_dd",
            ])


def log_event(message_id, signal, multiplier, recent_pattern, action, reason):
    ensure_log_file()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            message_id,
            signal,
            multiplier if multiplier is not None else "",
            recent_pattern,
            rr_active,
            rr_entry_step,
            rr_pending_trade,
            green_streak,
            momentum_entries_used,
            momentum_pending_trade,
            pattern_pending_trade,
            pattern_pending_name,
            pattern_pending_level,
            action,
            reason,
            session_trades,
            session_wins,
            session_losses,
            session_pnl,
            session_max_dd,
        ])


def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(f"last_processed_msg_id={last_processed_msg_id or ''}\n")
        f.write(f"rr_active={rr_active}\n")
        f.write(f"rr_entry_step={rr_entry_step}\n")
        f.write(f"rr_pending_trade={rr_pending_trade}\n")
        f.write(f"green_streak={green_streak}\n")
        f.write(f"momentum_entries_used={momentum_entries_used}\n")
        f.write(f"momentum_pending_trade={momentum_pending_trade}\n")
        f.write(f"pattern_pending_trade={pattern_pending_trade}\n")
        f.write(f"pattern_pending_name={pattern_pending_name}\n")
        f.write(f"pattern_pending_level={pattern_pending_level}\n")
        f.write(f"session_trades={session_trades}\n")
        f.write(f"session_wins={session_wins}\n")
        f.write(f"session_losses={session_losses}\n")
        f.write(f"session_pnl={session_pnl}\n")
        f.write(f"session_peak={session_peak}\n")
        f.write(f"session_max_dd={session_max_dd}\n")


def load_state():
    global last_processed_msg_id
    global rr_active, rr_entry_step, rr_pending_trade
    global green_streak, momentum_entries_used, momentum_pending_trade
    global pattern_pending_trade, pattern_pending_name, pattern_pending_level
    global session_trades, session_wins, session_losses, session_pnl, session_peak, session_max_dd

    if not os.path.exists(STATE_FILE):
        return

    data = {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                data[k] = v

    lp = data.get("last_processed_msg_id", "")
    last_processed_msg_id = int(lp) if lp else None

    rr_active = data.get("rr_active", "False") == "True"
    rr_entry_step = int(data.get("rr_entry_step", "0"))
    rr_pending_trade = data.get("rr_pending_trade", "False") == "True"

    green_streak = int(data.get("green_streak", "0"))
    momentum_entries_used = int(data.get("momentum_entries_used", "0"))
    momentum_pending_trade = data.get("momentum_pending_trade", "False") == "True"

    pattern_pending_trade = data.get("pattern_pending_trade", "False") == "True"
    pattern_pending_name = data.get("pattern_pending_name", "")
    pattern_pending_level = data.get("pattern_pending_level", "")

    session_trades = int(data.get("session_trades", "0"))
    session_wins = int(data.get("session_wins", "0"))
    session_losses = int(data.get("session_losses", "0"))
    session_pnl = int(data.get("session_pnl", "0"))
    session_peak = int(data.get("session_peak", "0"))
    session_max_dd = int(data.get("session_max_dd", "0"))


def get_recent_pattern():
    seq = "".join(history)
    max_len = min(6, len(seq))
    patterns = []

    for L in range(2, max_len + 1):
        patterns.append(seq[-L:])

    patterns = sorted(patterns, key=len, reverse=True)

    for p in patterns:
        if p in PREMIUM_PATTERNS:
            return p, "PREMIUM"
    for p in patterns:
        if p in STRONG_PATTERNS:
            return p, "STRONG"
    for p in patterns:
        if p in MODERATE_PATTERNS:
            return p, "MODERATE"

    return "", "NONE"


def build_entry_message(mode, detail, recent_tail, icon="🟢"):
    return (
        f"{icon} ENTRAR AHORA\n\n"
        f"Modo: {mode}\n"
        f"Detalle: {detail}\n"
        f"Payoff: G=+{GAIN} | R={LOSS}\n"
        f"Green streak actual: {green_streak}\n"
        f"Historial reciente: {recent_tail}\n"
        f"Trades sesión: {session_trades}\n"
        f"W/L sesión: {session_wins}/{session_losses}\n"
        f"Winrate sesión: {current_winrate():.4f}\n"
        f"PnL sesión: {session_pnl}\n"
        f"DD máx sesión: {session_max_dd}"
    )


# =========================================================
# CORE
# =========================================================
@client.on(events.NewMessage(chats=source_group))
async def handler(event):
    global last_processed_msg_id
    global rr_active, rr_entry_step, rr_pending_trade
    global green_streak, momentum_entries_used, momentum_pending_trade
    global pattern_pending_trade, pattern_pending_name, pattern_pending_level
    global session_trades, session_wins, session_losses, session_pnl

    if last_processed_msg_id is not None and event.message.id <= last_processed_msg_id:
        return

    text = event.raw_text or ""
    signal, multiplier = parse_signal(text)

    if signal is None:
        return

    history.append(signal)
    recent_tail = ",".join(list(history)[-12:])
    recent_pattern, pattern_level = get_recent_pattern()

    action = "NOOP"
    reason = "Sin acción"

    # 1) Resolver trades pendientes
    if rr_pending_trade:
        session_trades += 1
        if signal == "G":
            session_wins += 1
            session_pnl += GAIN
            update_drawdown()

            if rr_active and rr_entry_step < MAX_RR_ENTRIES:
                rr_entry_step += 1
                rr_pending_trade = True

                msg = build_entry_message("RR", f"Entrada {rr_entry_step}/{MAX_RR_ENTRIES}", recent_tail)
                await client.send_message(target_chat, msg)
                action = f"RR_ENTRY_{rr_entry_step}"
                reason = f"Win RR previo; abrir RR {rr_entry_step}"
                print(msg)
                print("-" * 70)
            else:
                rr_active = False
                rr_entry_step = 0
                rr_pending_trade = False
                action = "RR_CLOSE"
                reason = "Cluster RR cerrado"
        else:
            session_losses += 1
            session_pnl += LOSS
            update_drawdown()
            rr_active = False
            rr_entry_step = 0
            rr_pending_trade = False
            action = "RR_RESET"
            reason = "Loss en RR"

    if pattern_pending_trade:
        session_trades += 1
        if signal == "G":
            session_wins += 1
            session_pnl += GAIN
            update_drawdown()
            action = "PATTERN_WIN"
            reason = f"Win patrón {pattern_pending_name}"
        else:
            session_losses += 1
            session_pnl += LOSS
            update_drawdown()
            action = "PATTERN_LOSS"
            reason = f"Loss patrón {pattern_pending_name}"

        pattern_pending_trade = False
        pattern_pending_name = ""
        pattern_pending_level = ""

    if momentum_pending_trade:
        session_trades += 1
        if signal == "G":
            session_wins += 1
            session_pnl += GAIN
            update_drawdown()
            action = "MOM_WIN"
            reason = "Win momentum"
        else:
            session_losses += 1
            session_pnl += LOSS
            update_drawdown()
            action = "MOM_LOSS"
            reason = "Loss momentum"

        momentum_pending_trade = False

    # 2) Actualizar streak
    if signal == "G":
        green_streak += 1
    else:
        green_streak = 0
        momentum_entries_used = 0

    # 3) RR: activar con RR
    if not rr_pending_trade:
        if len(history) >= 2 and history[-2] == "R" and history[-1] == "R":
            rr_active = True
            rr_entry_step = 1
            rr_pending_trade = True

            msg = build_entry_message("RR", f"Entrada 1/{MAX_RR_ENTRIES} tras RR", recent_tail)
            await client.send_message(target_chat, msg)
            action = "RR_ENTRY_1"
            reason = "RR detectado"
            print(msg)
            print("-" * 70)

    # 4) Pattern: más permisivo
    if not rr_pending_trade and not pattern_pending_trade:
        if pattern_level == "PREMIUM":
            pattern_pending_trade = True
            pattern_pending_name = recent_pattern
            pattern_pending_level = pattern_level
            msg = build_entry_message("PATRON", f"PREMIUM {recent_pattern}", recent_tail, "🟢")
            await client.send_message(target_chat, msg)
            action = "PATTERN_PREMIUM"
            reason = f"Patrón premium {recent_pattern}"
            print(msg)
            print("-" * 70)

        elif pattern_level == "STRONG":
            pattern_pending_trade = True
            pattern_pending_name = recent_pattern
            pattern_pending_level = pattern_level
            msg = build_entry_message("PATRON", f"FUERTE {recent_pattern}", recent_tail, "🟢")
            await client.send_message(target_chat, msg)
            action = "PATTERN_STRONG"
            reason = f"Patrón fuerte {recent_pattern}"
            print(msg)
            print("-" * 70)

        elif pattern_level == "MODERATE":
            pattern_pending_trade = True
            pattern_pending_name = recent_pattern
            pattern_pending_level = pattern_level
            msg = build_entry_message("PATRON", f"MODERADO {recent_pattern}", recent_tail, "🟡")
            await client.send_message(target_chat, msg)
            action = "PATTERN_MODERATE"
            reason = f"Patrón moderado {recent_pattern}"
            print(msg)
            print("-" * 70)

    # 5) Momentum: más temprano y más entradas
    if not rr_pending_trade and not pattern_pending_trade and not momentum_pending_trade:
        if green_streak >= MOMENTUM_TRIGGER and momentum_entries_used < MAX_MOMENTUM_ENTRIES_PER_STREAK:
            momentum_entries_used += 1
            momentum_pending_trade = True

            msg = build_entry_message(
                "MOMENTUM",
                f"Entrada {momentum_entries_used}/{MAX_MOMENTUM_ENTRIES_PER_STREAK} por streak >= {MOMENTUM_TRIGGER}",
                recent_tail,
                "🟢"
            )
            await client.send_message(target_chat, msg)
            action = "MOM_ENTRY"
            reason = f"Momentum activado en streak {green_streak}"
            print(msg)
            print("-" * 70)

    last_processed_msg_id = event.message.id
    log_event(event.message.id, signal, multiplier, recent_pattern, action, reason)
    save_state()


# =========================================================
# MAIN
# =========================================================
async def main():
    ensure_log_file()
    load_state()

    threading.Thread(target=run_health_server, daemon=True).start()

    try:
        await client.start()
        print("Motor MAX ENTRIES escuchando...")
        print(f"RR activo: {rr_active}, step: {rr_entry_step}, pending: {rr_pending_trade}")
        print(f"Green streak: {green_streak}, Momentum used: {momentum_entries_used}, pending: {momentum_pending_trade}")
        print(f"Pattern pending: {pattern_pending_trade}, pattern: {pattern_pending_name}, level: {pattern_pending_level}")
        print("Solo notificará cuando toque ENTRAR.")
        await client.run_until_disconnected()
    except Exception as e:
        print(f"ERROR FATAL: {repr(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
