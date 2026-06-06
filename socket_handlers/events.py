"""
socket_handlers/events.py — Eventos Socket.IO em tempo real
"""
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import current_user
from models.votacao import VotacaoModel, LogModel, ServidorModel, VotoModel, CandidatoModel
from models import get_db
import threading
import time

socketio = SocketIO()


# ---------------------------------------------------------------------------
# Conexão / desconexão
# ---------------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    emit("connected", {"msg": "Conectado ao servidor de tempo real"})


@socketio.on("disconnect")
def on_disconnect():
    pass


# ---------------------------------------------------------------------------
# Sala por votação — cliente pede para entrar na sala
# ---------------------------------------------------------------------------
@socketio.on("join_votacao")
def on_join_votacao(data):
    vid = data.get("votacao_id")
    if vid:
        join_room(f"votacao_{vid}")
        emit("joined", {"votacao_id": vid})


@socketio.on("leave_votacao")
def on_leave_votacao(data):
    vid = data.get("votacao_id")
    if vid:
        leave_room(f"votacao_{vid}")


# ---------------------------------------------------------------------------
# Sala do dashboard global
# ---------------------------------------------------------------------------
@socketio.on("join_dashboard")
def on_join_dashboard(data):
    join_room("dashboard_global")
    emit("joined", {"room": "dashboard_global"})


# ---------------------------------------------------------------------------
# Funções chamadas pelo app para emitir eventos
# ---------------------------------------------------------------------------
def emitir_novo_log(votacao_id: str, log: dict):
    """Emite novo log para a sala da votação e para o dashboard global."""
    def _fmt(d):
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    payload = {
        "tipo":       log.get("tipo"),
        "mensagem":   log.get("mensagem"),
        "servidor":   log.get("servidor_id", "sistema"),
        "timestamp":  _fmt(log.get("timestamp")),
        "votacao_id": votacao_id,
    }
    socketio.emit("novo_log", payload, room=f"votacao_{votacao_id}")
    socketio.emit("novo_log", payload, room="dashboard_global")


def emitir_status_servidor(votacao_id: str, tipo: str, porta: int, status: str):
    payload = {"tipo": tipo, "porta": porta, "status": status, "votacao_id": votacao_id}
    socketio.emit("status_servidor", payload, room=f"votacao_{votacao_id}")
    socketio.emit("status_servidor", payload, room="dashboard_global")


def emitir_novo_voto(votacao_id: str, total_votos: int, resultado: list):
    payload = {
        "votacao_id": votacao_id,
        "total_votos": total_votos,
        "resultado": resultado,
    }
    socketio.emit("novo_voto", payload, room=f"votacao_{votacao_id}")
    socketio.emit("novo_voto", payload, room="dashboard_global")


# ---------------------------------------------------------------------------
# Background thread — atualiza todos os clientes a cada 3 segundos
# ---------------------------------------------------------------------------
_bg_started = False
_bg_lock    = threading.Lock()


def iniciar_background_thread(app):
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        _bg_started = True

    def _loop():
        with app.app_context():
            while True:
                try:
                    db = get_db()

                    # Stats globais
                    stats = {
                        "total_votos":     db.votos.count_documents({"confirmado": True}),
                        "urnas_online":    db.urnas.count_documents({"status": "online"}),
                        "cont_online":     db.contadores.count_documents({"status": "online"}),
                        "votacoes_ativas": db.votacoes.count_documents({"status": "ativa"}),
                    }
                    socketio.emit("stats_update", stats, room="dashboard_global")

                    # Por votação ativa — enviar resultado atualizado
                    votacoes_ativas = list(db.votacoes.find({"status": "ativa"}))
                    for v in votacoes_ativas:
                        vid = str(v["_id"])
                        candidatos = CandidatoModel.por_votacao(vid)
                        contagem   = VotoModel.contagem_por_candidato(vid)
                        total      = sum(contagem.values()) or 1
                        resultado  = [
                            {
                                "nome":  c["nome"],
                                "votos": contagem.get(str(c["_id"]), 0),
                                "pct":   round(contagem.get(str(c["_id"]), 0) / total * 100, 1),
                            }
                            for c in candidatos
                        ]
                        socketio.emit("resultado_update", {
                            "votacao_id": vid,
                            "resultado":  resultado,
                            "total_votos": v.get("total_votos", 0),
                        }, room=f"votacao_{vid}")

                except Exception:
                    pass
                time.sleep(3)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
