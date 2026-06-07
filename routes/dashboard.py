"""
routes/dashboard.py — Painel administrativo global e por votação
"""
from flask import Blueprint, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models.votacao import VotacaoModel, CandidatoModel, VotoModel, LogModel, ServidorModel
from models.user import UserModel
from models import get_db

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """Dashboard pessoal: votações do usuário + stats gerais."""
    minhas = VotacaoModel.listar_por_criador(current_user.id)

    # Stats globais
    db = get_db()
    total_votacoes = db.votacoes.count_documents({})
    total_usuarios = db.users.count_documents({})
    total_votos    = db.votos.count_documents({"confirmado": True})
    urnas_online   = db.urnas.count_documents({"status": "online"})
    cont_online    = db.contadores.count_documents({"status": "online"})

    # Logs recentes do sistema (todas votações)
    logs_recentes = list(db.logs.find().sort("timestamp", -1).limit(30))

    return render_template(
        "dashboard/index.html",
        minhas=minhas,
        total_votacoes=total_votacoes,
        total_usuarios=total_usuarios,
        total_votos=total_votos,
        urnas_online=urnas_online,
        cont_online=cont_online,
        logs_recentes=logs_recentes,
    )


@dashboard_bp.route("/dashboard/votacao/<vid>")
@login_required
def votacao_detail(vid: str):
    """Dashboard detalhado de uma votação específica (só para o criador)."""
    votacao = VotacaoModel.por_id(vid)
    if not votacao:
        flash("Votação não encontrada.", "danger")
        return redirect(url_for("dashboard.index"))

    if str(votacao.get("criador_id", "")) != str(current_user.id):
        flash("Sem permissão para acessar este dashboard.", "danger")
        return redirect(url_for("dashboard.index"))

    candidatos = CandidatoModel.por_votacao(vid)
    urnas      = ServidorModel.urnas_por_votacao(vid)
    contadores = ServidorModel.contadores_por_votacao(vid)
    logs       = LogModel.recentes(vid, 50)
    contagem   = VotoModel.contagem_por_candidato(vid)

    # Montar dados para gráfico
    grafico_labels = [c["nome"] for c in candidatos]
    grafico_dados  = [contagem.get(str(c["_id"]), 0) for c in candidatos]
    total_votos    = sum(grafico_dados) or 1

    grafico_pct = [round(v / total_votos * 100, 1) for v in grafico_dados]

    return render_template(
        "dashboard/votacao.html",
        votacao=votacao,
        candidatos=candidatos,
        urnas=urnas,
        contadores=contadores,
        logs=logs,
        grafico_labels=grafico_labels,
        grafico_dados=grafico_dados,
        grafico_pct=grafico_pct,
        total_votos=sum(grafico_dados),
    )


@dashboard_bp.route("/admin")
@login_required
def admin():
    """Painel global — todas as votações, todos os logs."""
    db = get_db()

    votacoes = list(db.votacoes.find().sort("criada_em", -1))
    urnas    = list(db.urnas.find().sort("criado_em", -1).limit(100))
    conts    = list(db.contadores.find().sort("criado_em", -1).limit(100))
    logs     = list(db.logs.find().sort("timestamp", -1).limit(100))

    stats = {
        "total_votacoes":  len(votacoes),
        "votacoes_ativas": sum(1 for v in votacoes if v.get("status") == "ativa"),
        "urnas_online":    sum(1 for u in urnas if u.get("status") == "online"),
        "conts_online":    sum(1 for c in conts if c.get("status") == "online"),
        "total_votos":     db.votos.count_documents({"confirmado": True}),
        "total_usuarios":  db.users.count_documents({}),
    }

    return render_template(
        "dashboard/admin.html",
        votacoes=votacoes,
        urnas=urnas,
        conts=conts,
        logs=logs,
        stats=stats,
    )


@dashboard_bp.route("/api/dashboard/stats")
@login_required
def api_stats():
    """API usada pelo Socket.IO para atualizar stats em tempo real."""
    db = get_db()

    def _fmt(d):
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    logs = list(db.logs.find().sort("timestamp", -1).limit(20))

    return jsonify({
        "total_votos":     db.votos.count_documents({"confirmado": True}),
        "urnas_online":    db.urnas.count_documents({"status": "online"}),
        "cont_online":     db.contadores.count_documents({"status": "online"}),
        "votacoes_ativas": db.votacoes.count_documents({"status": "ativa"}),
        "logs": [{
            "tipo":      l["tipo"],
            "mensagem":  l["mensagem"],
            "timestamp": _fmt(l["timestamp"]),
            "servidor":  l.get("servidor_id", "sistema"),
        } for l in logs],
    })
