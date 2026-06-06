"""
core/contador.py
Processo independente de contagem de votos.

Uso:
  python core/contador.py <votacao_id> <contador_id_mongo> <porta_propria>
                          <porta1_outros_contadores> [<porta2> ...]

Responsabilidades:
  1. Receber votos das urnas via TCP
  2. Contabilizar localmente
  3. Replicar para todos os outros contadores
  4. Responder a consultas de estado (sincronização pós-falha)
  5. Enviar heartbeat ao coordenador
"""
import socket
import threading
import json
import sys
import time
import os

# Adiciona raiz do projeto ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymongo import MongoClient
from bson import ObjectId
from config import config

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
VOTACAO_ID      = sys.argv[1]
CONTADOR_ID     = sys.argv[2]       # _id MongoDB deste contador
PORTA_PROPRIA   = int(sys.argv[3])
PORTA_COORD     = int(sys.argv[4])  # porta do coordenador (para heartbeat)
PORTAS_OUTROS   = [int(p) for p in sys.argv[5:]]  # outras réplicas

HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Estado local
# ---------------------------------------------------------------------------
_lock     = threading.Lock()
_contagem = {}           # {"candidato_id_str": int}
_rodando  = True

# ---------------------------------------------------------------------------
# MongoDB (conexão leve — só para persistência periódica e logs)
# ---------------------------------------------------------------------------
def _db():
    c = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return c[config.MONGO_DBNAME]


def _log(tipo: str, msg: str):
    try:
        _db().logs.insert_one({
            "votacao_id":  ObjectId(VOTACAO_ID),
            "servidor_id": f"contador:{PORTA_PROPRIA}",
            "tipo":        tipo,
            "mensagem":    msg,
            "timestamp":   __import__("datetime").datetime.utcnow(),
        })
    except Exception:
        pass
    print(f"[CONTADOR:{PORTA_PROPRIA}][{tipo.upper()}] {msg}", flush=True)


def _persistir_contagem():
    try:
        _db().contadores.update_one(
            {"_id": ObjectId(CONTADOR_ID)},
            {"$set": {"contagem": _contagem, "status": "online",
                      "ultimo_heartbeat": __import__("datetime").datetime.utcnow()}}
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Replicação para outros contadores
# ---------------------------------------------------------------------------
def _replicar(candidato_id: str, voto_id: str, urna_id: str):
    """Envia o voto para todas as réplicas (best-effort)."""
    msg = json.dumps({
        "tipo":        "replica",
        "candidato_id": candidato_id,
        "voto_id":      voto_id,
        "urna_id":      urna_id,
        "origem":       PORTA_PROPRIA,
    }).encode()

    for porta in PORTAS_OUTROS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((HOST, porta))
                s.send(msg)
        except Exception as e:
            _log("erro", f"Falha ao replicar para contador:{porta} — {e}")


# ---------------------------------------------------------------------------
# Sincronização pós-falha
# ---------------------------------------------------------------------------
def _buscar_estado_de_replicas():
    """Ao iniciar, tenta obter estado atual de outro contador."""
    for porta in PORTAS_OUTROS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect((HOST, porta))
                msg = json.dumps({"tipo": "sync_request"}).encode()
                s.send(msg)
                resposta = s.recv(65536).decode()
                dados = json.loads(resposta)
                if dados.get("tipo") == "sync_response":
                    with _lock:
                        _contagem.update(dados["contagem"])
                    _log("info", f"Sincronizado com contador:{porta}")
                    return
        except Exception:
            continue
    _log("info", "Nenhuma réplica disponível para sincronização — iniciando do zero")


# ---------------------------------------------------------------------------
# Processamento de conexões
# ---------------------------------------------------------------------------
def _processar(conn: socket.socket):
    global _contagem
    try:
        dados = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            dados += chunk
            try:
                msg = json.loads(dados.decode())
                break
            except json.JSONDecodeError:
                continue

        if not dados:
            return

        msg = json.loads(dados.decode())
        tipo = msg.get("tipo")

        # ------------------------------------------------------------------
        # Voto primário (vindo de urna)
        # ------------------------------------------------------------------
        if tipo == "voto":
            candidato_id = msg["candidato_id"]
            voto_id      = msg["voto_id"]
            urna_id      = msg["urna_id"]

            with _lock:
                _contagem[candidato_id] = _contagem.get(candidato_id, 0) + 1
                contagem_atual = dict(_contagem)

            _log("voto", f"Voto registrado: candidato={candidato_id} | total={contagem_atual}")

            # Confirmar voto no MongoDB
            try:
                _db().votos.update_one(
                    {"_id": ObjectId(voto_id)},
                    {"$set": {"confirmado": True}}
                )
                _db().votacoes.update_one(
                    {"_id": ObjectId(VOTACAO_ID)},
                    {"$inc": {"total_votos": 1}}
                )
            except Exception as e:
                _log("erro", f"Falha ao confirmar voto no MongoDB: {e}")

            # Persistir contagem
            _persistir_contagem()

            # Replicar para outros contadores
            threading.Thread(
                target=_replicar,
                args=(candidato_id, voto_id, urna_id),
                daemon=True
            ).start()

            # Responder à urna
            resposta = json.dumps({
                "status": "ok",
                "contagem": contagem_atual,
            })
            conn.send(resposta.encode())

        # ------------------------------------------------------------------
        # Réplica (vindo de outro contador)
        # ------------------------------------------------------------------
        elif tipo == "replica":
            candidato_id = msg["candidato_id"]
            origem       = msg.get("origem")

            with _lock:
                _contagem[candidato_id] = _contagem.get(candidato_id, 0) + 1

            _persistir_contagem()
            _log("info", f"Réplica recebida de contador:{origem} para candidato={candidato_id}")
            conn.send(b"ok")

        # ------------------------------------------------------------------
        # Pedido de sincronização (de outro contador que reiniciou)
        # ------------------------------------------------------------------
        elif tipo == "sync_request":
            with _lock:
                contagem_copia = dict(_contagem)
            resposta = json.dumps({"tipo": "sync_response", "contagem": contagem_copia})
            conn.send(resposta.encode())

        # ------------------------------------------------------------------
        # Consulta de status (do coordenador ou app.py)
        # ------------------------------------------------------------------
        elif tipo == "status":
            with _lock:
                contagem_copia = dict(_contagem)
            resposta = json.dumps({
                "tipo":     "status_response",
                "porta":    PORTA_PROPRIA,
                "contagem": contagem_copia,
                "total":    sum(contagem_copia.values()),
            })
            conn.send(resposta.encode())

    except Exception as e:
        _log("erro", f"Erro ao processar conexão: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Heartbeat para o coordenador
# ---------------------------------------------------------------------------
def _heartbeat_loop():
    while _rodando:
        try:
            msg = json.dumps({
                "tipo":      "heartbeat",
                "servidor":  "contador",
                "id":        CONTADOR_ID,
                "porta":     PORTA_PROPRIA,
                "votacao_id": VOTACAO_ID,
            }).encode()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((HOST, PORTA_COORD))
                s.send(msg)
        except Exception:
            pass  # Coordenador pode estar indisponível temporariamente
        time.sleep(config.HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Servidor TCP principal
# ---------------------------------------------------------------------------
def iniciar():
    global _rodando

    # Sincronizar estado com réplicas existentes
    _buscar_estado_de_replicas()

    # Atualizar status no MongoDB
    try:
        _db().contadores.update_one(
            {"_id": ObjectId(CONTADOR_ID)},
            {"$set": {"status": "online", "pid": os.getpid()}}
        )
    except Exception:
        pass

    # Thread de heartbeat
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    # Servidor TCP
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PORTA_PROPRIA))
    servidor.listen(50)
    servidor.settimeout(1.0)

    _log("info", f"Contador iniciado na porta {PORTA_PROPRIA} | votação={VOTACAO_ID}")

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
    _log("info", "Contador encerrado")


if __name__ == "__main__":
    iniciar()
