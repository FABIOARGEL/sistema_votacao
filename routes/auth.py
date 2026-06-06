"""
routes/auth.py — Autenticação: email/senha + Google OAuth
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from models.user import UserModel, bcrypt
from bson import ObjectId
import functools

auth_bp = Blueprint("auth", __name__)
oauth    = OAuth()


# ---------------------------------------------------------------------------
# Classe de usuário para Flask-Login
# ---------------------------------------------------------------------------
class LoginUser:
    def __init__(self, doc: dict):
        self._doc = doc

    @property
    def is_authenticated(self): return True
    @property
    def is_active(self):        return self._doc.get("ativo", True)
    @property
    def is_anonymous(self):     return False
    def get_id(self):            return str(self._doc["_id"])

    # Acesso fácil
    @property
    def id(self):    return str(self._doc["_id"])
    @property
    def nome(self):  return self._doc.get("nome", "")
    @property
    def email(self): return self._doc.get("email", "")
    @property
    def foto(self):  return self._doc.get("foto")


def configurar_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


# ---------------------------------------------------------------------------
# Rotas de autenticação local
# ---------------------------------------------------------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("votacoes.lista"))

    if request.method == "POST":
        nome  = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")
        conf  = request.form.get("confirmar_senha", "")

        if not all([nome, email, senha]):
            flash("Preencha todos os campos.", "danger")
            return render_template("auth/register.html")

        if senha != conf:
            flash("As senhas não coincidem.", "danger")
            return render_template("auth/register.html")

        if len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/register.html")

        if UserModel.por_email(email):
            flash("E-mail já cadastrado.", "danger")
            return render_template("auth/register.html")

        uid = UserModel.criar(nome=nome, email=email, senha=senha)
        user_doc = UserModel.por_id(uid)
        user = LoginUser(user_doc)
        login_user(user)
        flash(f"Bem-vindo, {nome}!", "success")
        return redirect(url_for("votacoes.lista"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("votacoes.lista"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")

        user_doc = UserModel.por_email(email)
        if not user_doc or not UserModel.verificar_senha(user_doc, senha):
            flash("E-mail ou senha incorretos.", "danger")
            return render_template("auth/login.html")

        user = LoginUser(user_doc)
        login_user(user, remember=request.form.get("lembrar") == "on")
        flash(f"Bem-vindo de volta, {user_doc['nome']}!", "success")
        next_page = request.args.get("next")
        return redirect(next_page or url_for("votacoes.lista"))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você saiu da conta.", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Recuperação de senha
# ---------------------------------------------------------------------------
@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        token = UserModel.gerar_reset_token(email)
        if token:
            reset_url = url_for("auth.reset_senha", token=token, _external=True)
            # Sem email real — exibe token na tela (modo acadêmico)
            flash(f"Link de recuperação gerado! Em um sistema real seria enviado por e-mail. "
                  f"Use este link: {reset_url}", "info")
        else:
            flash("E-mail não encontrado.", "danger")
    return render_template("auth/reset_request.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_senha(token: str):
    if request.method == "POST":
        nova_senha = request.form.get("senha", "")
        if len(nova_senha) < 6:
            flash("Senha deve ter ao menos 6 caracteres.", "danger")
            return render_template("auth/reset_senha.html", token=token)
        ok = UserModel.resetar_senha_por_token(token, nova_senha)
        if ok:
            flash("Senha redefinida com sucesso! Faça login.", "success")
            return redirect(url_for("auth.login"))
        else:
            flash("Token inválido ou expirado.", "danger")
    return render_template("auth/reset_senha.html", token=token)


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
@auth_bp.route("/login/google")
def google_login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/login/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.google.userinfo()

        google_id = userinfo["sub"]
        email     = userinfo.get("email", "")
        nome      = userinfo.get("name", email.split("@")[0])
        foto      = userinfo.get("picture")

        # Buscar por google_id primeiro, depois por email
        user_doc = UserModel.por_google_id(google_id)
        if not user_doc:
            user_doc = UserModel.por_email(email)
            if user_doc:
                UserModel.atualizar(user_doc["_id"], {"google_id": google_id, "foto": foto})
                user_doc = UserModel.por_id(user_doc["_id"])
            else:
                uid = UserModel.criar(nome=nome, email=email, google_id=google_id, foto=foto)
                user_doc = UserModel.por_id(uid)

        user = LoginUser(user_doc)
        login_user(user)
        flash(f"Bem-vindo, {nome}!", "success")
        return redirect(url_for("votacoes.lista"))
    except Exception as e:
        flash(f"Erro no login com Google: {str(e)}", "danger")
        return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------------
@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_doc = UserModel.por_id(current_user.id)
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if nome:
            UserModel.atualizar(current_user.id, {"nome": nome})
            flash("Perfil atualizado.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", user=user_doc)
