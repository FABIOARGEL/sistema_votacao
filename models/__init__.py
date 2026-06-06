"""
Camada de acesso ao MongoDB.
Todas as coleções são centralizadas aqui.
"""
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure
from config import config
from datetime import datetime, timezone
import sys

_client = None
_db     = None

def get_db():
    global _client, _db
    if _db is None:
        try:
            _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
            _client.admin.command("ping")
            _db = _client[config.MONGO_DBNAME]
            _criar_indices()
        except ConnectionFailure as e:
            print(f"[ERRO] Falha ao conectar ao MongoDB: {e}", file=sys.stderr)
            sys.exit(1)
    return _db


def _criar_indices():
    db = _db
    db.users.create_index("email", unique=True)
    db.votacoes.create_index([("status", ASCENDING), ("inicio", DESCENDING)])
    db.votacoes.create_index("criador_id")
    db.votos.create_index([("votacao_id", ASCENDING), ("eleitor_id", ASCENDING)], unique=True)
    db.urnas.create_index([("votacao_id", ASCENDING), ("status", ASCENDING)])
    db.contadores.create_index([("votacao_id", ASCENDING)])
    db.logs.create_index([("votacao_id", ASCENDING), ("timestamp", DESCENDING)])
    db.sessoes.create_index([("eleitor_id", ASCENDING), ("votacao_id", ASCENDING)])
    db.port_registry.create_index("porta", unique=True)


def utcnow():
    return datetime.now(timezone.utc)
