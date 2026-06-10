from bson import ObjectId
from models import get_db, utcnow
from datetime import datetime


class VotacaoModel:
    COLLECTION = "votacoes"

    @staticmethod
    def col():
        return get_db()[VotacaoModel.COLLECTION]

    @staticmethod
    def criar(dados: dict) -> ObjectId:
        doc = {
            "titulo":          dados["titulo"],
            "descricao":       dados.get("descricao", ""),
            "criador_id":      ObjectId(dados["criador_id"]),
            "inicio":          dados["inicio"],
            "fim":             dados["fim"],
            "publica":         dados.get("publica", True),
            "senha":           dados.get("senha"),           # None = sem senha
            "candidatos":      dados.get("candidatos", []),  # [{"nome": ..., "numero": ...}]
            "qtd_urnas":       dados.get("qtd_urnas", 2),
            "qtd_contadores":  dados.get("qtd_contadores", 2),
            "status":          "configurando",               # configurando | ativa | encerrada | erro
            "criada_em":       utcnow(),
            # Portas alocadas — preenchido pelo coordenador
            "porta_coordenador": None,
            "portas_urnas":    [],
            "portas_contadores": [],
            "pid_coordenador": None,
            # Resultado final
            "resultado_final": None,
            "total_votos":     0,
        }
        return get_db()[VotacaoModel.COLLECTION].insert_one(doc).inserted_id

    @staticmethod
    def por_id(votacao_id: str | ObjectId) -> dict | None:
        if isinstance(votacao_id, str):
            votacao_id = ObjectId(votacao_id)
        return VotacaoModel.col().find_one({"_id": votacao_id})

    @staticmethod
    def listar_publicas(skip=0, limit=20) -> list:
        return list(
            VotacaoModel.col()
            .find({"publica": True, "status": {"$in": ["ativa", "encerrada"]}})
            .sort("criada_em", -1)
            .skip(skip)
            .limit(limit)
        )

    @staticmethod
    def listar_disponiveis(skip=0, limit=20) -> list:
        return list(
            VotacaoModel.col()
            .find({"status": {"$in": ["ativa", "encerrada"]}})
            .sort("criada_em", -1)
            .skip(skip)
            .limit(limit)
        )

    @staticmethod
    def listar_por_criador(criador_id: str | ObjectId) -> list:
        if isinstance(criador_id, str):
            criador_id = ObjectId(criador_id)
        return list(
            VotacaoModel.col()
            .find({"criador_id": criador_id})
            .sort("criada_em", -1)
        )

    @staticmethod
    def atualizar(votacao_id: str | ObjectId, dados: dict):
        if isinstance(votacao_id, str):
            votacao_id = ObjectId(votacao_id)
        VotacaoModel.col().update_one({"_id": votacao_id}, {"$set": dados})

    @staticmethod
    def incrementar_votos(votacao_id: str | ObjectId):
        if isinstance(votacao_id, str):
            votacao_id = ObjectId(votacao_id)
        VotacaoModel.col().update_one({"_id": votacao_id}, {"$inc": {"total_votos": 1}})

    @staticmethod
    def encerrar(votacao_id: str | ObjectId, resultado: dict):
        VotacaoModel.atualizar(votacao_id, {
            "status": "encerrada",
            "resultado_final": resultado,
            "encerrada_em": utcnow(),
        })


class CandidatoModel:
    COLLECTION = "candidatos"

    @staticmethod
    def col():
        return get_db()[CandidatoModel.COLLECTION]

    @staticmethod
    def criar_multiplos(votacao_id: str | ObjectId, candidatos: list) -> list:
        """candidatos = [{"nome": ..., "numero": ..., "descricao": ...}]"""
        if isinstance(votacao_id, str):
            votacao_id = ObjectId(votacao_id)
        docs = []
        for c in candidatos:
            docs.append({
                "votacao_id": votacao_id,
                "nome":       c["nome"],
                "numero":     c.get("numero", 0),
                "descricao":  c.get("descricao", ""),
                "foto":       c.get("foto"),
            })
        if docs:
            CandidatoModel.col().insert_many(docs)
        return docs

    @staticmethod
    def por_votacao(votacao_id: str | ObjectId) -> list:
        if isinstance(votacao_id, str):
            votacao_id = ObjectId(votacao_id)
        return list(CandidatoModel.col().find({"votacao_id": votacao_id}))


class VotoModel:
    COLLECTION = "votos"

    @staticmethod
    def col():
        return get_db()[VotoModel.COLLECTION]

    @staticmethod
    def registrar(votacao_id, eleitor_id, candidato_id, urna_id) -> dict | None:
        """Retorna None se eleitor já votou."""
        doc = {
            "votacao_id":   ObjectId(str(votacao_id)),
            "eleitor_id":   ObjectId(str(eleitor_id)),
            "candidato_id": ObjectId(str(candidato_id)),
            "urna_id":      str(urna_id),
            "timestamp":    utcnow(),
            "confirmado":   False,
        }
        try:
            result = VotoModel.col().insert_one(doc)
            doc["_id"] = result.inserted_id
            return doc
        except Exception:
            return None  # unique index violation = já votou

    @staticmethod
    def confirmar(voto_id: str | ObjectId):
        if isinstance(voto_id, str):
            voto_id = ObjectId(voto_id)
        VotoModel.col().update_one({"_id": voto_id}, {"$set": {"confirmado": True}})

    @staticmethod
    def eleitor_ja_votou(votacao_id, eleitor_id) -> bool:
        return VotoModel.col().find_one({
            "votacao_id": ObjectId(str(votacao_id)),
            "eleitor_id": ObjectId(str(eleitor_id)),
        }) is not None

    @staticmethod
    def contagem_por_candidato(votacao_id) -> dict:
        pipeline = [
            {"$match": {"votacao_id": ObjectId(str(votacao_id)), "confirmado": True}},
            {"$group": {"_id": "$candidato_id", "votos": {"$sum": 1}}},
        ]
        return {str(r["_id"]): r["votos"] for r in get_db().votos.aggregate(pipeline)}


class LogModel:
    COLLECTION = "logs"

    TIPOS = ["info", "erro", "heartbeat", "falha", "voto", "encerramento"]

    @staticmethod
    def col():
        return get_db()[LogModel.COLLECTION]

    @staticmethod
    def registrar(votacao_id, servidor_id: str, tipo: str, mensagem: str):
        doc = {
            "votacao_id":  ObjectId(str(votacao_id)) if votacao_id else None,
            "servidor_id": servidor_id,
            "tipo":        tipo,
            "mensagem":    mensagem,
            "timestamp":   utcnow(),
        }
        LogModel.col().insert_one(doc)
        return doc

    @staticmethod
    def recentes(votacao_id, limit=50) -> list:
        return list(
            LogModel.col()
            .find({"votacao_id": ObjectId(str(votacao_id))})
            .sort("timestamp", -1)
            .limit(limit)
        )


class ServidorModel:
    """Gerencia urnas e contadores no MongoDB."""

    @staticmethod
    def registrar_urna(votacao_id, porta: int, pid: int) -> ObjectId:
        doc = {
            "votacao_id":        ObjectId(str(votacao_id)),
            "porta":             porta,
            "pid":               pid,
            "status":            "iniciando",
            "ultimo_heartbeat":  utcnow(),
            "votos_recebidos":   0,
            "criado_em":         utcnow(),
        }
        return get_db().urnas.insert_one(doc).inserted_id

    @staticmethod
    def registrar_contador(votacao_id, porta: int, pid: int) -> ObjectId:
        doc = {
            "votacao_id":        ObjectId(str(votacao_id)),
            "porta":             porta,
            "pid":               pid,
            "status":            "iniciando",
            "ultimo_heartbeat":  utcnow(),
            "contagem":          {},
            "criado_em":         utcnow(),
        }
        return get_db().contadores.insert_one(doc).inserted_id

    @staticmethod
    def heartbeat(collection: str, doc_id: str | ObjectId):
        if isinstance(doc_id, str):
            doc_id = ObjectId(doc_id)
        get_db()[collection].update_one(
            {"_id": doc_id},
            {"$set": {"status": "online", "ultimo_heartbeat": utcnow()}}
        )

    @staticmethod
    def marcar_offline(collection: str, doc_id: str | ObjectId):
        if isinstance(doc_id, str):
            doc_id = ObjectId(doc_id)
        get_db()[collection].update_one(
            {"_id": doc_id},
            {"$set": {"status": "offline"}}
        )

    @staticmethod
    def urnas_por_votacao(votacao_id) -> list:
        return list(get_db().urnas.find({"votacao_id": ObjectId(str(votacao_id))}))

    @staticmethod
    def contadores_por_votacao(votacao_id) -> list:
        return list(get_db().contadores.find({"votacao_id": ObjectId(str(votacao_id))}))

    @staticmethod
    def atualizar_contagem(contador_id: str | ObjectId, contagem: dict):
        if isinstance(contador_id, str):
            contador_id = ObjectId(contador_id)
        get_db().contadores.update_one(
            {"_id": contador_id},
            {"$set": {"contagem": contagem}}
        )
