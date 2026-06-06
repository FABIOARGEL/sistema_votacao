import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "chave-secreta-sistema-votacao-2024")
    
    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DBNAME = os.getenv("MONGO_DBNAME", "sistema_votacao")
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    
    # Faixas de porta para servidores distribuídos
    # Coordenadores: 7000–7499  (max 500 votações simultâneas)
    # Urnas:         8000–8999
    # Contadores:    9000–9999
    PORT_COORDENADOR_BASE = 7000
    PORT_URNA_BASE        = 8000
    PORT_CONTADOR_BASE    = 9000
    
    # Heartbeat
    HEARTBEAT_INTERVAL   = 5   # segundos entre envios
    HEARTBEAT_TIMEOUT    = 15  # segundos sem heartbeat → offline
    
    # Limites
    MAX_URNAS_POR_VOTACAO      = 10
    MAX_CONTADORES_POR_VOTACAO = 5
    
    # SocketIO
    SOCKETIO_ASYNC_MODE = "gevent"

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = DevelopmentConfig()
