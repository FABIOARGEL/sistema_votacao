from bson import ObjectId
from models import get_db, utcnow
from flask_bcrypt import Bcrypt
import secrets

bcrypt = Bcrypt()


class UserModel:
    """Operações CRUD sobre a coleção 'users'."""

    COLLECTION = "users"

    @staticmethod
    def col():
        return get_db()[UserModel.COLLECTION]

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------
    @staticmethod
    def criar(nome: str, email: str, senha: str | None = None,
              foto: str | None = None) -> ObjectId:
        doc = {
            "nome":         nome,
            "email":        email.lower().strip(),
            "senha_hash":   bcrypt.generate_password_hash(senha).decode() if senha else None,
            "foto":         foto,
            "criado_em":    utcnow(),
            "ativo":        True,
            "reset_token":  None,
            "reset_expiry": None,
        }
        resultado = UserModel.col().insert_one(doc)
        return resultado.inserted_id

    # ------------------------------------------------------------------
    # Buscas
    # ------------------------------------------------------------------
    @staticmethod
    def por_email(email: str) -> dict | None:
        return UserModel.col().find_one({"email": email.lower().strip()})

    @staticmethod
    def por_id(user_id: str | ObjectId) -> dict | None:
        if isinstance(user_id, str):
            user_id = ObjectId(user_id)
        return UserModel.col().find_one({"_id": user_id})


    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    @staticmethod
    def verificar_senha(user: dict, senha: str) -> bool:
        if not user.get("senha_hash"):
            return False
        return bcrypt.check_password_hash(user["senha_hash"], senha)

    # ------------------------------------------------------------------
    # Recuperação de senha
    # ------------------------------------------------------------------
    @staticmethod
    def gerar_reset_token(email: str) -> str | None:
        user = UserModel.por_email(email)
        if not user:
            return None
        from datetime import timedelta
        token = secrets.token_urlsafe(32)
        expiry = utcnow() + timedelta(hours=1)
        UserModel.col().update_one(
            {"_id": user["_id"]},
            {"$set": {"reset_token": token, "reset_expiry": expiry}}
        )
        return token

    @staticmethod
    def resetar_senha_por_token(token: str, nova_senha: str) -> bool:
        user = UserModel.col().find_one({"reset_token": token})
        if not user:
            return False
        if utcnow() > user["reset_expiry"].replace(tzinfo=__import__("datetime").timezone.utc):
            return False
        novo_hash = bcrypt.generate_password_hash(nova_senha).decode()
        UserModel.col().update_one(
            {"_id": user["_id"]},
            {"$set": {"senha_hash": novo_hash, "reset_token": None, "reset_expiry": None}}
        )
        return True

    # ------------------------------------------------------------------
    # Atualização
    # ------------------------------------------------------------------
    @staticmethod
    def atualizar(user_id: str | ObjectId, dados: dict):
        if isinstance(user_id, str):
            user_id = ObjectId(user_id)
        UserModel.col().update_one({"_id": user_id}, {"$set": dados})
