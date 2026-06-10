"""Helper para comunicação TCP com coordenadores e urnas."""
import socket
import json
import subprocess
import sys
import os
import time

HOST = "127.0.0.1"


def _tcp_request(porta: int, payload: dict, timeout: float = 5.0) -> dict | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((HOST, porta))
            s.send(json.dumps(payload).encode())
            resp = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
                try:
                    return json.loads(resp.decode())
                except json.JSONDecodeError:
                    continue
    except Exception:
        return None


def obter_urna(porta_coord: int) -> int | None:
    """Retorna a porta da urna designada pelo coordenador (round-robin)."""
    resp = _tcp_request(porta_coord, {"tipo": "get_urna"})
    if resp and resp.get("status") == "ok":
        return resp["porta_urna"]
    return None


def enviar_voto(porta_urna: int, votacao_id: str, eleitor_id: str, candidato_id: str) -> dict:
    """Envia voto à urna e retorna a resposta."""
    payload = {
        "tipo":         "voto",
        "votacao_id":   votacao_id,
        "eleitor_id":   eleitor_id,
        "candidato_id": candidato_id,
    }
    resp = _tcp_request(porta_urna, payload, timeout=10.0)
    if resp is None:
        return {"status": "erro", "motivo": "Urna inacessível"}
    return resp


def status_coordenador(porta_coord: int) -> dict | None:
    return _tcp_request(porta_coord, {"tipo": "status"})


def encerrar_votacao(porta_coord: int) -> bool:
    resp = _tcp_request(porta_coord, {"tipo": "encerrar"})
    return resp is not None


def status_contador(porta_contador: int) -> dict | None:
    return _tcp_request(porta_contador, {"tipo": "status"})


def coordenador_alive(porta_coord: int) -> bool:
    """Verifica se o coordenador está respondendo."""
    resp = _tcp_request(porta_coord, {"tipo": "status"}, timeout=3.0)
    return resp is not None


def reativar_infraestrutura(votacao: dict) -> bool:
    """
    Reativa a infraestrutura distribuída se o coordenador estiver morto.
    Usado quando o servidor principal cai e volta, mas a votação continua ativa.
    Retorna True se reativou com sucesso.
    """
    porta_coord = votacao.get("porta_coordenador")
    if not porta_coord:
        return False

    # Se o coordenador já está vivo, não precisa reativar
    if coordenador_alive(porta_coord):
        return True

    # Coordenador morto — reiniciar
    votacao_id = str(votacao["_id"])
    portas_urnas = votacao.get("portas_urnas", [])
    portas_contadores = votacao.get("portas_contadores", [])

    if not portas_urnas or not portas_contadores:
        return False

    coord_script = os.path.join(
        os.path.dirname(__file__), "coordenador.py"
    )
    args = (
        [sys.executable, coord_script, votacao_id, str(porta_coord)]
        + [str(p) for p in portas_urnas]
        + ["--"]
        + [str(p) for p in portas_contadores]
    )
    try:
        proc = subprocess.Popen(args)
        time.sleep(2)
        return True
    except Exception:
        return False
