"""
app.py — Aplicação Flask principal
Plataforma de Votação Distribuída — Sistemas Distribuídos
"""
from gevent import monkey
monkey.patch_all()

from flask import Flask, render_template, redirect, url_for
from flask_login import LoginManager, current_user
from flask_bcrypt import Bcrypt
from bson import ObjectId

from config import config
from models import get_db
from models.user import UserModel, bcrypt as _bcrypt
from routes.auth import auth_bp, LoginUser, configurar_oauth
from routes.votacoes import votacoes_bp
from routes.dashboard import dashboard_bp
from socket_handlers.events import socketio, iniciar_background_thread

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_app():
    app = Flask(__name__)
    app.config.from_object(config)
    app.config["SECRET_KEY"] = config.SECRET_KEY

    # Bcrypt
    _bcrypt.init_app(app)

    # Flask-Login
    login_manager = LoginManager(app)
    login_manager.login_view     = "auth.login"
    login_manager.login_message  = "Faça login para continuar."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str):
        doc = UserModel.por_id(user_id)
        return LoginUser(doc) if doc else None

    # OAuth
    configurar_oauth(app)

    # Socket.IO
    socketio.init_app(
        app,
        async_mode=config.SOCKETIO_ASYNC_MODE,
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    # Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(votacoes_bp)
    app.register_blueprint(dashboard_bp)

    # Filtros Jinja2 úteis
    @app.template_filter("fmt_dt")
    def fmt_dt(value):
        if not value:
            return "—"
        if hasattr(value, "strftime"):
            return value.strftime("%d/%m/%Y %H:%M")
        return str(value)

    @app.template_filter("fmt_status")
    def fmt_status(s):
        emojis = {
            "online": "", "offline": "", "iniciando": "",
            "ativa": "", "encerrada": "", "configurando": "", "erro": "",
        }
        return emojis.get(s, "") + " " + (s or "").capitalize()

    @app.template_filter("objectid_str")
    def objectid_str(v):
        return str(v)

    # Landing page
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("votacoes.lista"))
        db = get_db()
        stats = {
            "votacoes": db.votacoes.count_documents({}),
            "votos":    db.votos.count_documents({"confirmado": True}),
            "usuarios": db.users.count_documents({}),
        }
        return render_template("index.html", stats=stats)

    # Erros
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    # Inicializar MongoDB (cria índices)
    with app.app_context():
        get_db()

    # Background thread Socket.IO
    iniciar_background_thread(app)

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
