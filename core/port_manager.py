"""
Gerenciador de portas — garante que cada votação receba portas únicas.
Usa a coleção 'port_registry' no MongoDB para evitar conflitos entre processos.
"""
import socket
from pymongo import MongoClient
from config import config


def _db():
    client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return client[config.MONGO_DBNAME]


def _porta_livre(porta: int) -> bool:
    """Verifica se a porta TCP está realmente disponível no OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", porta))
            return True
        except OSError:
            return False


def alocar_portas(votacao_id: str, qtd_urnas: int, qtd_contadores: int) -> dict:
    """
    Aloca as portas necessárias para uma votação:
      - 1 porta para o coordenador
      - qtd_urnas portas para as urnas
      - qtd_contadores portas para os contadores

    Retorna:
        {
            "coordenador": int,
            "urnas": [int, ...],
            "contadores": [int, ...]
        }
    """
    db = _db()
    registry = db.port_registry

    total = 1 + qtd_urnas + qtd_contadores
    alocadas = []

    # Candidatos: coordenador na faixa 7000, urnas em 8000, contadores em 9000
    candidatos = (
        list(range(config.PORT_COORDENADOR_BASE, config.PORT_COORDENADOR_BASE + 500)) +
        list(range(config.PORT_URNA_BASE, config.PORT_URNA_BASE + 1000)) +
        list(range(config.PORT_CONTADOR_BASE, config.PORT_CONTADOR_BASE + 1000))
    )

    # Portas já em uso no registry
    em_uso = {doc["porta"] for doc in registry.find({"liberada": False})}

    para_coordenador = []
    para_urnas = []
    para_contadores = []

    for porta in range(config.PORT_COORDENADOR_BASE, config.PORT_COORDENADOR_BASE + 500):
        if porta not in em_uso and _porta_livre(porta):
            para_coordenador.append(porta)
            break

    for porta in range(config.PORT_URNA_BASE, config.PORT_URNA_BASE + 1000):
        if porta not in em_uso and porta not in para_coordenador and _porta_livre(porta):
            para_urnas.append(porta)
            em_uso.add(porta)
        if len(para_urnas) == qtd_urnas:
            break

    for porta in range(config.PORT_CONTADOR_BASE, config.PORT_CONTADOR_BASE + 1000):
        if porta not in em_uso and _porta_livre(porta):
            para_contadores.append(porta)
            em_uso.add(porta)
        if len(para_contadores) == qtd_contadores:
            break

    todas = para_coordenador + para_urnas + para_contadores

    # Registrar no MongoDB
    docs = [{"porta": p, "votacao_id": votacao_id, "liberada": False} for p in todas]
    if docs:
        registry.insert_many(docs)

    return {
        "coordenador": para_coordenador[0] if para_coordenador else None,
        "urnas":        para_urnas,
        "contadores":   para_contadores,
    }


def liberar_portas(votacao_id: str):
    """Libera todas as portas registradas para uma votação."""
    db = _db()
    db.port_registry.update_many(
        {"votacao_id": votacao_id},
        {"$set": {"liberada": True}}
    )
