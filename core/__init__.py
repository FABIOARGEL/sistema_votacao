"""Helper para comunicação TCP com coordenadores e urnas."""
import socket
import json

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
