"""
core/coordenador.py
Processo coordenador — um por votação.

Uso:
  python core/coordenador.py <votacao_id> <porta_coordenador>
                             <porta_urna_1> [<porta_urna_2> ...] --
                             <porta_contador_1> [<porta_contador_2> ...]

O coordenador:
  1. Spawna processos de urna e contador
  2. Monitora heartbeats de todos os servidores
  3. Detecta e registra falhas
  4. Fornece balanceamento de carga (round-robin de urnas)
  5. Permite encerramento controlado da votação
  6. Expõe API TCP para o app.py consultar status
"""
import socket
import threading
import subprocess
import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymongo import MongoClient
from bson import ObjectId
from config import config

# ---------------------------------------------------------------------------
# Argumentos: coordenador.py <votacao_id> <porta_coord> <u1> <u2> ... -- <c1> <c2> ...
# ---------------------------------------------------------------------------
VOTACAO_ID    = sys.argv[1]
PORTA_COORD   = int(sys.argv[2])
resto         = sys.argv[3:]
separador_idx = resto.index("--")
PORTAS_URNAS      = [int(p) for p in resto[:separador_idx]]
PORTAS_CONTADORES = [int(p) for p in resto[separador_idx + 1:]]

HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Estado do coordenador
# ---------------------------------------------------------------------------
_processos_urnas      = []  # Popen
_processos_contadores = []  # Popen
_ids_urnas            = []  # ObjectId MongoDB
_ids_contadores       = []  # ObjectId MongoDB
_ultimo_heartbeat     = {}  # porta -> datetime
_status_servidores    = {}  # porta -> "online"|"offline"
_urna_idx             = 0   # round-robin
_lock_rr              = threading.Lock()
_rodando              = True


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
            "servidor_id": f"coordenador:{PORTA_COORD}",
            "tipo":        tipo,
            "mensagem":    msg,
            "timestamp":   datetime.now(timezone.utc),
        })
    except Exception:
        pass
    print(f"[COORD:{PORTA_COORD}][{tipo.upper()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Spawn dos processos filhos
# ---------------------------------------------------------------------------
def _spawn_processos():
    db = _db()
    python_exec = sys.executable  # mesmo interpretador que está rodando o coordenador

    urna_script     = os.path.join(os.path.dirname(__file__), "urna.py")
    contador_script = os.path.join(os.path.dirname(__file__), "contador.py")

    # -- Registrar contadores no MongoDB primeiro (para passar IDs)
    cont_docs = []
    for porta in PORTAS_CONTADORES:
        doc = {
            "votacao_id":       ObjectId(VOTACAO_ID),
            "porta":            porta,
            "pid":              0,
            "status":           "iniciando",
            "ultimo_heartbeat": datetime.now(timezone.utc),
            "contagem":         {},
            "criado_em":        datetime.now(timezone.utc),
        }
        cont_id = db.contadores.insert_one(doc).inserted_id
        cont_docs.append((porta, str(cont_id)))
        _ids_contadores.append(cont_id)
        _status_servidores[porta] = "iniciando"
        _ultimo_heartbeat[porta]  = datetime.now(timezone.utc)

    # -- Spawnar contadores
    for porta, cont_id in cont_docs:
        outras_portas = [p for p, _ in cont_docs if p != porta]
        args = [
            python_exec, contador_script,
            VOTACAO_ID, cont_id, str(porta), str(PORTA_COORD),
        ] + [str(p) for p in outras_portas]
        proc = subprocess.Popen(args)
        _processos_contadores.append(proc)
        _log("info", f"Contador spawned na porta {porta} (pid={proc.pid})")

    # -- Registrar urnas no MongoDB
    urna_docs = []
    for porta in PORTAS_URNAS:
        doc = {
            "votacao_id":       ObjectId(VOTACAO_ID),
            "porta":            porta,
            "pid":              0,
            "status":           "iniciando",
            "ultimo_heartbeat": datetime.now(timezone.utc),
            "votos_recebidos":  0,
            "criado_em":        datetime.now(timezone.utc),
        }
        urna_id = db.urnas.insert_one(doc).inserted_id
        urna_docs.append((porta, str(urna_id)))
        _ids_urnas.append(urna_id)
        _status_servidores[porta] = "iniciando"
        _ultimo_heartbeat[porta]  = datetime.now(timezone.utc)

    # -- Aguardar contadores iniciarem (máx 5s)
    time.sleep(2)

    # -- Spawnar urnas
    portas_cont = [p for p, _ in cont_docs]
    for porta, urna_id in urna_docs:
        args = [
            python_exec, urna_script,
            VOTACAO_ID, urna_id, str(porta), str(PORTA_COORD),
        ] + [str(p) for p in portas_cont]
        proc = subprocess.Popen(args)
        _processos_urnas.append(proc)
        _log("info", f"Urna spawned na porta {porta} (pid={proc.pid})")

    # Atualizar votação com portas e pids
    db.votacoes.update_one(
        {"_id": ObjectId(VOTACAO_ID)},
        {"$set": {
            "status":              "ativa",
            "portas_urnas":        PORTAS_URNAS,
            "portas_contadores":   PORTAS_CONTADORES,
            "porta_coordenador":   PORTA_COORD,
            "pid_coordenador":     os.getpid(),
        }}
    )
    _log("info", "Infraestrutura distribuída ativa")


# ---------------------------------------------------------------------------
# Monitor de heartbeat
# ---------------------------------------------------------------------------
def _monitor_heartbeat():
    timeout = config.HEARTBEAT_TIMEOUT
    while _rodando:
        agora = datetime.now(timezone.utc)
        for porta, ultimo in list(_ultimo_heartbeat.items()):
            delta = (agora - ultimo).total_seconds()
            status_atual = _status_servidores.get(porta)
            if delta > timeout and status_atual != "offline":
                _status_servidores[porta] = "offline"
                # Detectar tipo de servidor
                tipo = "urna" if porta in PORTAS_URNAS else "contador"
                _log("falha", f"{tipo.capitalize()}:{porta} offline! Sem heartbeat há {delta:.0f}s")
                # Atualizar MongoDB
                col = "urnas" if tipo == "urna" else "contadores"
                try:
                    _db()[col].update_one(
                        {"votacao_id": ObjectId(VOTACAO_ID), "porta": porta},
                        {"$set": {"status": "offline"}}
                    )
                except Exception:
                    pass
            elif delta <= timeout and status_atual == "offline":
                _status_servidores[porta] = "online"
                _log("info", f"Servidor:{porta} recuperado após {delta:.0f}s")

        time.sleep(config.HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Processamento de mensagens TCP ao coordenador
# ---------------------------------------------------------------------------
def _processar(conn: socket.socket):
    global _urna_idx
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

        # -- Heartbeat de urna ou contador
        if tipo == "heartbeat":
            porta    = msg.get("porta")
            servidor = msg.get("servidor")
            srv_id   = msg.get("id")

            _ultimo_heartbeat[porta]  = datetime.now(timezone.utc)
            _status_servidores[porta] = "online"

            col = "urnas" if servidor == "urna" else "contadores"
            try:
                _db()[col].update_one(
                    {"_id": ObjectId(srv_id)},
                    {"$set": {"status": "online", "ultimo_heartbeat": datetime.now(timezone.utc)}}
                )
            except Exception:
                pass
            conn.send(b"ok")

        # -- Solicitar urna (balanceamento round-robin)
        elif tipo == "get_urna":
            portas_online = [p for p in PORTAS_URNAS
                             if _status_servidores.get(p) == "online"]
            if not portas_online:
                conn.send(json.dumps({"status": "erro", "motivo": "Nenhuma urna disponível"}).encode())
                return
            with _lock_rr:
                _urna_idx = _urna_idx % len(portas_online)
                porta_escolhida = portas_online[_urna_idx]
                _urna_idx += 1

            conn.send(json.dumps({"status": "ok", "porta_urna": porta_escolhida}).encode())

        # -- Status geral
        elif tipo == "status":
            status = {
                "votacao_id": VOTACAO_ID,
                "porta":      PORTA_COORD,
                "urnas":      {str(p): _status_servidores.get(p, "desconhecido") for p in PORTAS_URNAS},
                "contadores": {str(p): _status_servidores.get(p, "desconhecido") for p in PORTAS_CONTADORES},
            }
            conn.send(json.dumps(status).encode())

        # -- Encerrar votação
        elif tipo == "encerrar":
            _log("encerramento", "Comando de encerramento recebido")
            conn.send(b"ok")
            _encerrar()

    except Exception as e:
        _log("erro", f"Erro ao processar conexão no coordenador: {e}")
    finally:
        conn.close()


def _encerrar():
    global _rodando
    _rodando = False
    _log("encerramento", "Encerrando todos os processos...")
    for p in _processos_urnas + _processos_contadores:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        _db().votacoes.update_one(
            {"_id": ObjectId(VOTACAO_ID)},
            {"$set": {"status": "encerrada", "encerrada_em": datetime.now(timezone.utc)}}
        )
    except Exception:
        pass
    _log("encerramento", "Coordenador encerrado")


# ---------------------------------------------------------------------------
# Servidor TCP do coordenador
# ---------------------------------------------------------------------------
def iniciar():
    _spawn_processos()

    threading.Thread(target=_monitor_heartbeat, daemon=True).start()

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PORTA_COORD))
    servidor.listen(50)
    servidor.settimeout(1.0)

    _log("info", f"Coordenador escutando na porta {PORTA_COORD}")

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


if __name__ == "__main__":
    iniciar()
