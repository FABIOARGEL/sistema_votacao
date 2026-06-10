"""
core/urna.py
Processo independente de urna eleitoral.

Uso:
  python core/urna.py <votacao_id> <urna_id_mongo> <porta_propria>
                      <porta_coordenador>
                      <porta_contador_primario> [<porta_contador_2> ...]

Responsabilidades:
  1. Receber votos dos eleitores via TCP (intermediado pelo app.py)
  2. Validar: votação ativa? eleitor já votou? candidato válido?
  3. Encaminhar ao contador primário (com fallback para outros)
  4. Confirmar ao eleitor
  5. Enviar heartbeat ao coordenador
  6. Registrar logs no MongoDB
"""
import socket
import threading
import json
import sys
import time
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymongo import MongoClient
from bson import ObjectId
from config import config

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
VOTACAO_ID      = sys.argv[1]
URNA_ID         = sys.argv[2]
PORTA_PROPRIA   = int(sys.argv[3])
PORTA_COORD     = int(sys.argv[4])
PORTAS_CONTADORES = [int(p) for p in sys.argv[5:]]

HOST    = "127.0.0.1"
_rodando = True
_votos_recebidos = 0
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
def _db():
    c = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return c[config.MONGO_DBNAME]


def _log(tipo: str, msg: str):
    try:
        _db().logs.insert_one({
            "votacao_id":  ObjectId(VOTACAO_ID),
            "servidor_id": f"urna:{PORTA_PROPRIA}",
            "tipo":        tipo,
            "mensagem":    msg,
            "timestamp":   datetime.now(timezone.utc),
        })
    except Exception:
        pass
    print(f"[URNA:{PORTA_PROPRIA}][{tipo.upper()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Envio ao contador (com fallback)
# ---------------------------------------------------------------------------
def _enviar_contador(payload: dict) -> dict | None:
    """Tenta o contador primário primeiro, depois os outros."""
    for porta in PORTAS_CONTADORES:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((HOST, porta))
                s.send(json.dumps(payload).encode())
                resp = s.recv(65536)
                return json.loads(resp.decode())
        except Exception as e:
            _log("erro", f"Falha ao alcançar contador:{porta} — {e}")
    return None


# ---------------------------------------------------------------------------
# Validação de voto
# ---------------------------------------------------------------------------
def _validar(msg: dict) -> tuple[bool, str]:
    votacao_id  = msg.get("votacao_id")
    eleitor_id  = msg.get("eleitor_id")
    candidato_id = msg.get("candidato_id")

    if not all([votacao_id, eleitor_id, candidato_id]):
        return False, "Dados incompletos"

    db = _db()

    # Votação ativa?
    votacao = db.votacoes.find_one({"_id": ObjectId(votacao_id)})
    if not votacao:
        return False, "Votação não encontrada"
    if votacao.get("status") != "ativa":
        return False, f"Votação não está ativa (status={votacao.get('status')})"

    # Horário válido?
    agora = datetime.now(timezone.utc)
    inicio = votacao.get("inicio")
    fim    = votacao.get("fim")

    def _ensure_utc(dt):
        """Garante que datetime tenha timezone UTC."""
        if dt is None:
            return None
        if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    inicio = _ensure_utc(inicio)
    fim = _ensure_utc(fim)

    if inicio and inicio > agora:
        return False, "Votação ainda não iniciou"
    # Não validamos fim aqui — o status da votação é a fonte da verdade.
    # A validação de horário é feita pelo app Flask na rota de voto.
    # Isso evita erros de timezone entre o form (horário local) e UTC.

    # Eleitor já votou?
    ja_votou = db.votos.find_one({
        "votacao_id": ObjectId(votacao_id),
        "eleitor_id": ObjectId(eleitor_id),
    })
    if ja_votou:
        return False, "Eleitor já votou nesta votação"

    # Candidato válido?
    candidato = db.candidatos.find_one({
        "_id": ObjectId(candidato_id),
        "votacao_id": ObjectId(votacao_id),
    })
    if not candidato:
        return False, "Candidato inválido"

    return True, "ok"


# ---------------------------------------------------------------------------
# Processamento de conexões
# ---------------------------------------------------------------------------
def _processar(conn: socket.socket):
    global _votos_recebidos
    try:
        dados = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            dados += chunk
            try:
                json.loads(dados.decode())
                break
            except json.JSONDecodeError:
                continue

        if not dados:
            return

        msg = json.loads(dados.decode())
        tipo = msg.get("tipo")

        # ------------------------------------------------------------------
        # Recebimento de voto
        # ------------------------------------------------------------------
        if tipo == "voto":
            valido, motivo = _validar(msg)
            if not valido:
                conn.send(json.dumps({"status": "erro", "motivo": motivo}).encode())
                _log("info", f"Voto rejeitado: {motivo}")
                return

            # Registrar voto pré-confirmação no MongoDB
            try:
                voto_doc = {
                    "votacao_id":   ObjectId(msg["votacao_id"]),
                    "eleitor_id":   ObjectId(msg["eleitor_id"]),
                    "candidato_id": ObjectId(msg["candidato_id"]),
                    "urna_id":      URNA_ID,
                    "timestamp":    datetime.now(timezone.utc),
                    "confirmado":   False,
                }
                voto_result = _db().votos.insert_one(voto_doc)
                voto_id = str(voto_result.inserted_id)
            except Exception as e:
                conn.send(json.dumps({"status": "erro", "motivo": "Falha ao registrar voto"}).encode())
                _log("erro", f"Erro ao inserir voto no MongoDB: {e}")
                return

            # Encaminhar ao contador
            payload_contador = {
                "tipo":         "voto",
                "candidato_id": msg["candidato_id"],
                "voto_id":      voto_id,
                "urna_id":      URNA_ID,
                "votacao_id":   VOTACAO_ID,
            }
            resp_contador = _enviar_contador(payload_contador)

            if resp_contador and resp_contador.get("status") == "ok":
                with _lock:
                    _votos_recebidos += 1
                    votos = _votos_recebidos

                _db().urnas.update_one(
                    {"_id": ObjectId(URNA_ID)},
                    {"$set": {"votos_recebidos": votos}}
                )

                _log("voto", f"Voto confirmado: eleitor={msg['eleitor_id']} | candidato={msg['candidato_id']}")

                conn.send(json.dumps({
                    "status":   "ok",
                    "voto_id":  voto_id,
                    "mensagem": "Voto registrado com sucesso",
                }).encode())
            else:
                # Rollback: remover voto não confirmado
                try:
                    _db().votos.delete_one({"_id": ObjectId(voto_id)})
                except Exception:
                    pass
                _log("erro", "Nenhum contador disponível — voto revertido")
                conn.send(json.dumps({
                    "status": "erro",
                    "motivo": "Falha nos servidores de contagem. Tente novamente.",
                }).encode())

        # ------------------------------------------------------------------
        # Status da urna
        # ------------------------------------------------------------------
        elif tipo == "status":
            with _lock:
                votos = _votos_recebidos
            conn.send(json.dumps({
                "tipo":            "status_response",
                "porta":           PORTA_PROPRIA,
                "urna_id":         URNA_ID,
                "votos_recebidos": votos,
                "status":          "online",
            }).encode())

    except Exception as e:
        _log("erro", f"Erro ao processar conexão: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def _heartbeat_loop():
    while _rodando:
        try:
            msg = json.dumps({
                "tipo":      "heartbeat",
                "servidor":  "urna",
                "id":        URNA_ID,
                "porta":     PORTA_PROPRIA,
                "votacao_id": VOTACAO_ID,
            }).encode()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((HOST, PORTA_COORD))
                s.send(msg)
        except Exception:
            pass
        time.sleep(config.HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
def iniciar():
    global _rodando

    # Atualizar status no MongoDB
    try:
        _db().urnas.update_one(
            {"_id": ObjectId(URNA_ID)},
            {"$set": {"status": "online", "pid": os.getpid()}}
        )
    except Exception as e:
        _log("erro", f"Falha ao atualizar status inicial: {e}")

    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PORTA_PROPRIA))
    servidor.listen(100)
    servidor.settimeout(1.0)

    _log("info", f"Urna iniciada na porta {PORTA_PROPRIA} | votação={VOTACAO_ID}")

    while _rodando:
        try:
            conn, addr = servidor.accept()
            threading.Thread(target=_processar, args=(conn,), daemon=True).start()
        except socket.timeout:
            continue
        except Exception as e:
            _log("erro", f"Erro no accept: {e}")
            break

    servidor.close()
    _log("info", "Urna encerrada")


if __name__ == "__main__":
    iniciar()
